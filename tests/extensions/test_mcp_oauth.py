"""MCP OAuth storage, callback gating, and readiness tests."""

from __future__ import annotations

import asyncio

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from openppx.config import InMemorySecretStore
from openppx.extensions.mcp import McpManager
from openppx.extensions.mcp_oauth import (
    McpOAuthService,
    McpOAuthTokenStorage,
    _AuthorizationFlow,
    _validate_callback_base,
)
from tests.extensions.test_direct_mcp_registry import _server


def test_oauth_token_storage_round_trips_tokens_and_client_registration() -> None:
    secrets = InMemorySecretStore()
    storage = McpOAuthTokenStorage("granola", secrets)
    tokens = OAuthToken(access_token="access-value", refresh_token="refresh-value")
    client = OAuthClientInformationFull(
        redirect_uris=["http://127.0.0.1:18765/api/v1/mcp/oauth/callback"],
        client_id="client-id",
    )

    asyncio.run(storage.set_tokens(tokens))
    asyncio.run(storage.set_client_info(client))

    assert asyncio.run(storage.get_tokens()) == tokens
    assert asyncio.run(storage.get_client_info()) == client


def test_oauth_callback_requires_the_active_flow_state() -> None:
    secrets = InMemorySecretStore()
    service = McpOAuthService(secrets)
    loop = asyncio.new_event_loop()
    flow = _AuthorizationFlow("granola", "https://mcp.granola.ai/mcp", "http://127.0.0.1:18765")
    flow.expected_state = "expected-state"
    flow.loop = loop
    flow.callback_future = loop.create_future()
    service._flows["granola"] = flow
    service._active_server = "granola"

    try:
        assert service.deliver_callback("wrong", "wrong-state") is False
        assert flow.callback_future.done() is False
        assert service.deliver_callback("code-value", "expected-state") is True
        loop.run_until_complete(asyncio.sleep(0))
        assert flow.callback_future.result() == ("code-value", "expected-state")
    finally:
        loop.close()


def test_oauth_callback_base_rejects_credentials_query_and_fragment() -> None:
    assert _validate_callback_base("http://127.0.0.1:18765/path") == "http://127.0.0.1:18765"
    with pytest.raises(Exception):
        _validate_callback_base("http://user@example.com")
    with pytest.raises(Exception):
        _validate_callback_base("http://127.0.0.1:18765?token=value")
    with pytest.raises(Exception):
        _validate_callback_base("file:///tmp/callback")


def test_oauth_resource_is_ready_only_after_protected_tokens_exist(tmp_path) -> None:
    secrets = InMemorySecretStore()
    manager = McpManager(tmp_path, secrets)
    resource = _server(
        "granola",
        transport={
            "type": "streamable_http",
            "url": "https://mcp.granola.ai/mcp",
            "headers": {},
            "auth": "oauth",
        },
    )
    manager.create(resource, expected_revision=None)

    assert manager.readiness("granola").issues == ("oauth_authorization_missing",)
    storage = McpOAuthTokenStorage("granola", secrets)
    asyncio.run(storage.set_client_info(OAuthClientInformationFull(
        redirect_uris=["http://127.0.0.1:18765/api/v1/mcp/oauth/callback"],
        client_id="client-id",
    )))
    assert manager.readiness("granola").issues == ("oauth_authorization_missing",)
    asyncio.run(storage.set_tokens(OAuthToken(access_token="access-value")))
    assert manager.readiness("granola").ready is True
