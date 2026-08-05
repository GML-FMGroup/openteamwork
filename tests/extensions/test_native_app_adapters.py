"""Deterministic runtime tests for Node-shipped native App adapters."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

import pytest

from openppx.config import InMemorySecretStore, SecretRef, SecretValue
from openppx.extensions import NativeAppContext, default_extension_starter_catalog
from openppx.extensions.app_models import AppConnection, AppDefinition
from openppx.extensions.native_app_adapters import (
    HttpxNativeAppTransport,
    default_native_app_adapter_registry,
)


class FakeTransport:
    """Capture fixed provider requests without network access."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "params": dict(params or {}),
                "json": dict(json_body or {}),
            }
        )
        return {"ok": True, "status": 200, "data": {"accepted": True}}


class RejectedTransport(FakeTransport):
    """Return a provider-level authentication rejection over HTTP 200."""

    async def request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        await super().request(*args, **kwargs)
        return {"ok": True, "status": 200, "data": {"ok": False, "error": "invalid_auth"}}


def _context(starter_id: str, secret_slot: str, secret_value: str) -> NativeAppContext:
    starter = default_extension_starter_catalog().get(starter_id)
    definition = AppDefinition.model_validate(starter.template["definition"])
    secret_store = InMemorySecretStore()
    ref = SecretRef(name=f"test-{secret_slot}")
    secret_store.put(ref, SecretValue(secret_value))
    connection = AppConnection.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AppConnection",
            "metadata": {"name": f"{definition.metadata.name}-test"},
            "spec": {
                "appId": definition.metadata.name,
                "displayName": "Test connection",
                "credentialRefs": {secret_slot: ref.model_dump(mode="json", by_alias=True)},
                "enabledTools": None,
                "requireConfirmation": True,
                "enabledAgentIds": [],
            },
        }
    )
    return NativeAppContext(
        definition=definition,
        connection=connection,
        tools=tuple(definition.spec.tools),
        secret_store=secret_store,
    )


def test_telegram_adapter_builds_selected_tools_and_keeps_token_out_of_result() -> None:
    transport = FakeTransport()
    registry = default_native_app_adapter_registry(transport=transport)
    context = _context("app-telegram", "bot-token", "telegram-private-token")
    tools = registry.require("telegram-bot-api").build_tools(context)

    send = next(tool for tool in tools if tool.name == "telegram_send_message")
    result = asyncio.run(send.run_async(args={"chat_id": "42", "text": "hello"}, tool_context=None))

    assert sorted(tool.name for tool in tools) == ["telegram_get_updates", "telegram_send_message"]
    assert result == {"ok": True, "status": 200, "data": {"accepted": True}}
    assert transport.calls[-1]["json"] == {"chat_id": "42", "text": "hello"}
    assert "telegram-private-token" not in str(result)


@pytest.mark.parametrize(
    ("starter_id", "adapter_id", "secret_slot", "expected_path"),
    [
        ("app-telegram", "telegram-bot-api", "bot-token", "/getMe"),
        ("app-slack", "slack-web-api", "bot-token", "/auth.test"),
        ("app-gmail", "gmail-api", "access-token", "/profile"),
        ("app-google-calendar", "google-calendar-api", "access-token", "/calendars/primary"),
        ("app-outlook", "microsoft-graph", "access-token", "/me"),
    ],
)
def test_native_adapter_probe_calls_minimal_identity_endpoint(
    starter_id: str,
    adapter_id: str,
    secret_slot: str,
    expected_path: str,
) -> None:
    transport = FakeTransport()
    adapter = default_native_app_adapter_registry(transport=transport).require(adapter_id)

    result = asyncio.run(adapter.probe(_context(starter_id, secret_slot, "private-token")))

    assert result.ready is True
    assert result.issue is None
    assert transport.calls[-1]["url"].endswith(expected_path)
    assert "private-token" not in str(result)


@pytest.mark.parametrize(
    ("starter_id", "adapter_id", "secret_slot", "tool_name", "args"),
    [
        ("app-slack", "slack-web-api", "bot-token", "slack_list_channels", {}),
        ("app-gmail", "gmail-api", "access-token", "gmail_search_messages", {"query": "newer:1d"}),
        ("app-google-calendar", "google-calendar-api", "access-token", "gcal_list_events", {}),
        ("app-outlook", "microsoft-graph", "access-token", "outlook_search_messages", {}),
    ],
)
def test_native_app_adapters_execute_representative_read_tool(
    starter_id: str,
    adapter_id: str,
    secret_slot: str,
    tool_name: str,
    args: dict[str, Any],
) -> None:
    transport = FakeTransport()
    context = _context(starter_id, secret_slot, "private-token")
    tools = default_native_app_adapter_registry(transport=transport).require(adapter_id).build_tools(context)

    result = asyncio.run(
        next(tool for tool in tools if tool.name == tool_name).run_async(
            args=args,
            tool_context=None,
        )
    )

    assert result["ok"] is True
    assert transport.calls
    assert "private-token" not in str(result)


def test_http_transport_returns_stable_failure_without_request_details() -> None:
    transport = HttpxNativeAppTransport(timeout_seconds=0.01)

    result = asyncio.run(
        transport.request(
            "GET",
            "http://127.0.0.1:1/secret-in-path",
            headers={"Authorization": "Bearer do-not-leak"},
        )
    )

    assert result == {"ok": False, "error": "provider_unreachable"}
    assert "do-not-leak" not in str(result)


def test_native_probe_rejects_provider_level_auth_failure_without_echoing_token() -> None:
    adapter = default_native_app_adapter_registry(transport=RejectedTransport()).require(
        "slack-web-api"
    )

    result = asyncio.run(adapter.probe(_context("app-slack", "bot-token", "private-token")))

    assert result.ready is False
    assert result.issue == "provider_rejected_credentials"
    assert "private-token" not in str(result)
