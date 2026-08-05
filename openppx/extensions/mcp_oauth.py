"""Explicit browser OAuth lifecycle for remote MCP servers.

The MCP SDK owns OAuth 2.1 discovery, Dynamic Client Registration, PKCE, refresh,
and token validation. OpenPPX supplies a protected token store, an explicit
Desktop-triggered authorization flow, and a state-gated callback handoff.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from openppx.config import SecretBackendUnavailable, SecretNotFound, SecretRef, SecretStore, SecretValue

from .errors import ExtensionError


CALLBACK_PATH = "/api/v1/mcp/oauth/callback"
FLOW_TIMEOUT_SECONDS = 300


def oauth_secret_ref(server_id: str) -> SecretRef:
    """Return the stable protected record used for one MCP OAuth session."""
    return SecretRef(store="system", name=f"mcp-oauth-{server_id}")


def oauth_tokens_available(server_id: str, store: SecretStore) -> bool:
    """Check for a usable protected token without returning any credential value."""
    try:
        raw = json.loads(store.resolve(oauth_secret_ref(server_id)).reveal())
    except (SecretBackendUnavailable, SecretNotFound, TypeError, ValueError):
        return False
    tokens = raw.get("tokens") if isinstance(raw, dict) else None
    return isinstance(tokens, dict) and bool(str(tokens.get("access_token") or "").strip())


class McpOAuthTokenStorage(TokenStorage):
    """Persist MCP SDK token and DCR state as one encrypted system credential."""

    def __init__(self, server_id: str, store: SecretStore) -> None:
        self._ref = oauth_secret_ref(server_id)
        self._store = store

    def _read(self) -> dict[str, Any]:
        try:
            raw = self._store.resolve(self._ref).reveal()
        except SecretNotFound:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _merge(self, patch: dict[str, Any]) -> None:
        self._store.put(
            self._ref,
            SecretValue(json.dumps({**self._read(), **patch}, separators=(",", ":"))),
        )

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        try:
            return OAuthToken.model_validate(raw) if raw else None
        except Exception:
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._merge({"tokens": tokens.model_dump(mode="json", exclude_none=True)})

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("clientInfo")
        try:
            return OAuthClientInformationFull.model_validate(raw) if raw else None
        except Exception:
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._merge({"clientInfo": client_info.model_dump(mode="json", exclude_none=True)})


class McpOAuthAuthorizationRequired(RuntimeError):
    """Raised when a background Runtime would otherwise open an authorization page."""


class McpOAuthCallbackError(RuntimeError):
    """Raised when the authorization server returns a state-matched OAuth error."""


@dataclass(slots=True)
class _AuthorizationFlow:
    server_id: str
    server_url: str
    callback_base: str
    status: str = "starting"
    authorize_url: str = ""
    error: str = ""
    expected_state: str = ""
    loop: asyncio.AbstractEventLoop | None = None
    callback_future: asyncio.Future[tuple[str, str | None]] | None = None
    ready: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)


class McpOAuthService:
    """Own one explicit OAuth flow at a time and non-interactive Runtime auth."""

    def __init__(self, secret_store: SecretStore) -> None:
        self.secret_store = secret_store
        self._lock = threading.RLock()
        self._flows: dict[str, _AuthorizationFlow] = {}
        self._active_server = ""

    def begin(self, server_id: str, server_url: str, callback_base: str) -> dict[str, object]:
        """Start an explicit browser flow and return its authorization URL promptly."""
        callback_base = _validate_callback_base(callback_base)
        with self._lock:
            current = self._flows.get(server_id)
            if current is not None and not current.finished.is_set():
                return self.status(server_id)
            if self._active_server and self._active_server != server_id:
                raise ExtensionError("oauth_busy", "Another MCP authorization is already in progress.")
            flow = _AuthorizationFlow(server_id, server_url, callback_base)
            self._flows[server_id] = flow
            self._active_server = server_id
            threading.Thread(
                target=self._run_flow,
                args=(flow,),
                name=f"openppx-mcp-oauth-{server_id}",
                daemon=True,
            ).start()
        flow.ready.wait(timeout=15)
        return self.status(server_id)

    def status(self, server_id: str) -> dict[str, object]:
        """Return non-sensitive flow/token state for polling clients."""
        with self._lock:
            flow = self._flows.get(server_id)
            token_state = self.secret_store.status(oauth_secret_ref(server_id)).state
            if flow is None:
                status = "connected" if token_state == "available" and oauth_tokens_available(server_id, self.secret_store) else "needs_auth"
                return {"serverId": server_id, "status": status, "authorizeUrl": "", "error": ""}
            return {
                "serverId": server_id,
                "status": flow.status,
                "authorizeUrl": flow.authorize_url,
                "error": flow.error,
            }

    def deliver_callback(
        self,
        code: str,
        state: str | None,
        *,
        error: str = "",
    ) -> bool:
        """Deliver one state-matched loopback callback without consuming stray hits."""
        with self._lock:
            flow = self._flows.get(self._active_server)
            if flow is None or flow.callback_future is None or flow.callback_future.done():
                return False
            if flow.expected_state and (
                state is None or not secrets.compare_digest(state, flow.expected_state)
            ):
                return False
            loop = flow.loop
            future = flow.callback_future
        if loop is None or loop.is_closed():
            return False
        if error:
            loop.call_soon_threadsafe(
                future.set_exception,
                McpOAuthCallbackError("The authorization server declined the request."),
            )
        else:
            loop.call_soon_threadsafe(future.set_result, (code, state))
        return True

    def sign_out(self, server_id: str) -> dict[str, object]:
        """Forget both OAuth tokens and the DCR client registration."""
        self.secret_store.delete(oauth_secret_ref(server_id))
        with self._lock:
            self._flows.pop(server_id, None)
            if self._active_server == server_id:
                self._active_server = ""
        return self.status(server_id)

    def runtime_httpx_factory(self, server_id: str, server_url: str):
        """Build a silent-refresh HTTP client factory for a normal Agent Runtime."""
        provider = self._provider(
            server_id,
            server_url,
            "http://127.0.0.1:18765",
            redirect_handler=_refuse_redirect,
            callback_handler=_refuse_callback,
        )

        def factory(headers=None, timeout=None, auth=None):
            return create_mcp_http_client(headers=headers, timeout=timeout, auth=provider)

        return factory

    def _run_flow(self, flow: _AuthorizationFlow) -> None:
        try:
            asyncio.run(self._authorize(flow))
            flow.status = "connected"
        except Exception as exc:
            flow.status = "error"
            flow.error = _safe_flow_error(exc)
        finally:
            flow.ready.set()
            flow.finished.set()
            with self._lock:
                if self._active_server == flow.server_id:
                    self._active_server = ""

    async def _authorize(self, flow: _AuthorizationFlow) -> None:
        provider = self._provider(
            flow.server_id,
            flow.server_url,
            flow.callback_base,
            redirect_handler=lambda url: self._capture_redirect(flow, url),
            callback_handler=lambda: self._wait_for_callback(flow),
        )
        async with streamablehttp_client(flow.server_url, auth=provider) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                await session.list_tools()

    def _provider(self, server_id, server_url, callback_base, *, redirect_handler, callback_handler):
        metadata = OAuthClientMetadata.model_validate(
            {
                "client_name": "OpenPPX",
                "redirect_uris": [callback_base.rstrip("/") + CALLBACK_PATH],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            }
        )
        return OAuthClientProvider(
            server_url=server_url,
            client_metadata=metadata,
            storage=McpOAuthTokenStorage(server_id, self.secret_store),
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )

    async def _capture_redirect(self, flow: _AuthorizationFlow, url: str) -> None:
        values = parse_qs(urlsplit(url).query).get("state")
        flow.authorize_url = url
        flow.expected_state = values[0] if values else ""
        flow.status = "authorizing"
        flow.ready.set()

    async def _wait_for_callback(self, flow: _AuthorizationFlow) -> tuple[str, str | None]:
        flow.loop = asyncio.get_running_loop()
        flow.callback_future = flow.loop.create_future()
        try:
            return await asyncio.wait_for(flow.callback_future, timeout=FLOW_TIMEOUT_SECONDS)
        finally:
            flow.callback_future = None


async def _refuse_redirect(_url: str) -> None:
    raise McpOAuthAuthorizationRequired("MCP sign-in must be started explicitly from Extensions.")


async def _refuse_callback() -> tuple[str, str | None]:
    raise McpOAuthAuthorizationRequired("MCP sign-in must be started explicitly from Extensions.")


def _validate_callback_base(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ExtensionError("invalid_oauth_callback", "The OAuth callback origin is invalid.")
    return f"{parsed.scheme}://{parsed.netloc}"


def _safe_flow_error(exc: Exception) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "Sign-in timed out. Try connecting again."
    if isinstance(exc, McpOAuthAuthorizationRequired):
        return "Sign-in is required."
    if isinstance(exc, McpOAuthCallbackError):
        return "Sign-in was cancelled or declined."
    return f"MCP authorization failed ({type(exc).__name__})."


__all__ = ["McpOAuthService", "McpOAuthTokenStorage", "oauth_secret_ref", "oauth_tokens_available"]
