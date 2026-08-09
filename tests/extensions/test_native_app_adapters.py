"""Deterministic runtime tests for Node-shipped native App adapters."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

import pytest

import openppx.extensions.imap_app_adapter as imap_adapter_module
from openppx.config import InMemorySecretStore, SecretRef, SecretValue
from openppx.extensions import NativeAppContext, default_extension_starter_catalog
from openppx.extensions.app_models import AppConnection, AppDefinition
from openppx.extensions.imap_app_adapter import StdlibImapTransport
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


class FakeImapTransport:
    """Capture a fixed IMAPS request without opening a socket."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def probe(self, *, host: str, port: int, username: str, password: str) -> dict[str, Any]:
        self.calls.append({"operation": "probe", "host": host, "port": port, "username": username, "password": password})
        return {"ok": True, "data": {"ready": True}}

    async def list_messages(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        limit: int,
    ) -> dict[str, Any]:
        self.calls.append({"operation": "list", "host": host, "port": port, "username": username, "password": password, "limit": limit})
        return {"ok": True, "data": {"messages": [{"uid": "7", "subject": "Hello"}]}}

    async def search_messages(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"operation": "search", **kwargs})
        return {"ok": True, "data": {"messages": []}}

    async def get_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"operation": "get", **kwargs})
        return {"ok": True, "data": {"uid": kwargs["uid"], "body": "Hello"}}


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


def _imap_context(email_address: str = "reader@qq.com") -> NativeAppContext:
    starter = default_extension_starter_catalog().get("app-email")
    definition = AppDefinition.model_validate(starter.template["definition"])
    secret_store = InMemorySecretStore()
    refs = {
        "email-address": SecretRef(name="test-imap-address"),
        "app-password": SecretRef(name="test-imap-password"),
    }
    secret_store.put(refs["email-address"], SecretValue(email_address))
    secret_store.put(refs["app-password"], SecretValue("private-imap-password"))
    connection = AppConnection.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AppConnection",
            "metadata": {"name": "imap-test"},
            "spec": {
                "appId": definition.metadata.name,
                "displayName": "IMAP test",
                "credentialRefs": {
                    name: ref.model_dump(mode="json", by_alias=True)
                    for name, ref in refs.items()
                },
                "enabledTools": None,
                "requireConfirmation": False,
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
        ("app-notion", "notion-api", "access-token", "/users/me"),
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
        ("app-notion", "notion-api", "access-token", "notion_search", {"query": "meeting"}),
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
    assert all(
        isinstance(tool.custom_metadata["openppx"]["networkOrigin"], str)
        for tool in tools
    )


def test_notion_adapter_uses_current_version_and_bounds_search_page_size() -> None:
    transport = FakeTransport()
    context = _context("app-notion", "access-token", "private-token")
    tools = default_native_app_adapter_registry(transport=transport).require("notion-api").build_tools(context)

    result = asyncio.run(
        next(tool for tool in tools if tool.name == "notion_search").run_async(
            args={"query": "meeting notes", "page_size": 999},
            tool_context=None,
        )
    )

    assert result["ok"] is True
    assert transport.calls[-1]["url"] == "https://api.notion.com/v1/search"
    assert transport.calls[-1]["headers"]["Notion-Version"] == "2026-03-11"
    assert transport.calls[-1]["json"]["page_size"] == 100
    assert transport.calls[-1]["json"]["query"] == "meeting notes"


def test_imap_adapter_uses_a_reviewed_read_only_provider_endpoint() -> None:
    transport = FakeImapTransport()
    adapter = default_native_app_adapter_registry(imap_transport=transport).require(
        "imap-readonly"
    )
    tools = adapter.build_tools(_imap_context())

    result = asyncio.run(
        next(tool for tool in tools if tool.name == "imap_list_messages").run_async(
            args={"limit": 999},
            tool_context=None,
        )
    )

    assert sorted(tool.name for tool in tools) == [
        "imap_get_message",
        "imap_list_messages",
        "imap_search_messages",
    ]
    assert result["data"]["messages"][0]["subject"] == "Hello"
    assert transport.calls[-1]["host"] == "imap.qq.com"
    assert transport.calls[-1]["port"] == 993
    assert transport.calls[-1]["limit"] == 20
    assert tools[0].custom_metadata["openppx"]["networkOrigin"] == "imaps://imap.qq.com:993/"
    assert "private-imap-password" not in str(result)


def test_imap_adapter_rejects_unreviewed_provider_domains_before_socket_io() -> None:
    transport = FakeImapTransport()
    adapter = default_native_app_adapter_registry(imap_transport=transport).require(
        "imap-readonly"
    )

    readiness = adapter.readiness(_imap_context("reader@example.com"))

    assert readiness.ready is False
    assert readiness.issues == ("unsupported_imap_provider",)
    assert transport.calls == []


def test_stdlib_imap_transport_uses_readonly_mailbox_and_peek_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []
    raw_message = (
        b"Subject: Hello\r\n"
        b"From: sender@example.com\r\n"
        b"To: reader@qq.com\r\n"
        b"Date: Sat, 9 Aug 2026 12:00:00 +0800\r\n"
        b"Message-ID: <message-7@example.com>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Bounded body"
    )

    class FakeClient:
        def __init__(self, host: str, port: int, **kwargs: Any) -> None:
            calls.append(("connect", host, port, kwargs))

        def login(self, username: str, password: str):
            calls.append(("login", username, password))
            return "OK", []

        def noop(self):
            return "OK", []

        def select(self, mailbox: str, *, readonly: bool):
            calls.append(("select", mailbox, readonly))
            return "OK", [b"2"]

        def uid(self, command: str, *args: Any):
            calls.append(("uid", command, *args))
            if command == "search":
                return "OK", [b"5 7"]
            fetch_spec = str(args[-1])
            payload = raw_message if "BODY.PEEK[]" in fetch_spec else raw_message.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            return "OK", [(b"7 FETCH", payload), b")"]

        def logout(self):
            calls.append(("logout",))
            return "BYE", []

    monkeypatch.setattr(imap_adapter_module.imaplib, "IMAP4_SSL", FakeClient)
    transport = StdlibImapTransport()

    listed = asyncio.run(
        transport.list_messages(
            host="imap.qq.com",
            port=993,
            username="reader@qq.com",
            password="private-password",
            limit=2,
        )
    )
    searched = asyncio.run(
        transport.search_messages(
            host="imap.qq.com",
            port=993,
            username="reader@qq.com",
            password="private-password",
            query='board "notes" \\ Q3',
            limit=2,
        )
    )
    fetched = asyncio.run(
        transport.get_message(
            host="imap.qq.com",
            port=993,
            username="reader@qq.com",
            password="private-password",
            uid="7",
        )
    )

    assert [item["uid"] for item in listed["data"]["messages"]] == ["7", "5"]
    assert [item["uid"] for item in searched["data"]["messages"]] == ["7", "5"]
    assert fetched["data"]["body"] == "Bounded body"
    assert fetched["data"]["subject"] == "Hello"
    assert all(call[1].lower() != "store" for call in calls if call[0] == "uid")
    assert all("BODY.PEEK" in str(call[-1]) for call in calls if call[:2] == ("uid", "fetch"))
    search_call = next(call for call in calls if call[:2] == ("uid", "search") and len(call) == 5)
    assert search_call[-1] == '"board \\"notes\\" \\\\ Q3"'
    assert ("select", "INBOX", True) in calls


def test_stdlib_imap_transport_sanitizes_socket_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_connect(*_args: Any, **_kwargs: Any):
        raise OSError("private-password must not escape")

    monkeypatch.setattr(imap_adapter_module.imaplib, "IMAP4_SSL", fail_connect)

    result = asyncio.run(
        StdlibImapTransport().probe(
            host="imap.qq.com",
            port=993,
            username="reader@qq.com",
            password="private-password",
        )
    )

    assert result == {"ok": False, "error": "provider_request_failed"}
    assert "private-password" not in str(result)


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
