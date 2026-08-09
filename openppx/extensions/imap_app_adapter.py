"""Read-only IMAPS App adapter with fixed public provider boundaries."""

from __future__ import annotations

import asyncio
import imaplib
import ssl
from contextlib import contextmanager
from email import policy as email_policy
from email.parser import BytesParser
from typing import Any, Awaitable, Callable, Iterator, Mapping, Protocol

from .app_adapters import (
    NativeAppAdapterProbe,
    NativeAppAdapterReadiness,
    NativeAppContext,
)
from .native_app_adapters import NativeAppTool


_DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_FETCH_BYTES = 128 * 1024
_MAX_BODY_CHARS = 64 * 1024
_ENDPOINTS = {
    "qq.com": ("imap.qq.com", 993),
    "foxmail.com": ("imap.qq.com", 993),
    "163.com": ("imap.163.com", 993),
    "vip.163.com": ("imap.vip.163.com", 993),
    "126.com": ("imap.126.com", 993),
    "vip.126.com": ("imap.vip.126.com", 993),
    "188.com": ("imap.188.com", 993),
    "vip.188.com": ("imap.vip.188.com", 993),
    "yeah.net": ("imap.yeah.net", 993),
}


class _UnsupportedProvider(ValueError):
    """Raised before socket I/O when an account domain is not reviewed."""


class NativeImapTransport(Protocol):
    """Injectable read-only IMAPS boundary used by the office mail adapter."""

    async def probe(self, *, host: str, port: int, username: str, password: str) -> dict[str, Any]:
        """Verify login without returning account or credential data."""
        ...

    async def list_messages(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        limit: int,
    ) -> dict[str, Any]:
        """List bounded message headers without changing message state."""
        ...

    async def search_messages(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        query: str,
        limit: int,
    ) -> dict[str, Any]:
        """Search and return bounded message headers."""
        ...

    async def get_message(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        uid: str,
    ) -> dict[str, Any]:
        """Read bounded text content without downloading attachments."""
        ...


class StdlibImapTransport:
    """Bounded stdlib IMAPS client that never performs mailbox mutations."""

    async def probe(self, *, host: str, port: int, username: str, password: str) -> dict[str, Any]:
        return await _run_io(lambda: _probe(host, port, username, password))

    async def list_messages(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        limit: int,
    ) -> dict[str, Any]:
        return await _run_io(lambda: _list(host, port, username, password, limit=limit))

    async def search_messages(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        query: str,
        limit: int,
    ) -> dict[str, Any]:
        return await _run_io(
            lambda: _search(
                host,
                port,
                username,
                password,
                query=query,
                limit=limit,
            )
        )

    async def get_message(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        uid: str,
    ) -> dict[str, Any]:
        return await _run_io(lambda: _get(host, port, username, password, uid=uid))


Operation = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ImapReadOnlyAdapter:
    """Read-only IMAPS tools for an explicit set of reviewed public providers."""

    adapter_id = "imap-readonly"

    def __init__(self, transport: NativeImapTransport | None = None) -> None:
        self.transport = transport or StdlibImapTransport()

    def readiness(self, context: NativeAppContext) -> NativeAppAdapterReadiness:
        """Validate credential availability and provider selection without socket I/O."""

        try:
            self._connection(context)
        except _UnsupportedProvider:
            return NativeAppAdapterReadiness(
                ready=False,
                issues=("unsupported_imap_provider",),
            )
        except Exception:
            return NativeAppAdapterReadiness(
                ready=False,
                issues=("imap_credentials_unavailable",),
            )
        return NativeAppAdapterReadiness(ready=True)

    async def probe(self, context: NativeAppContext) -> NativeAppAdapterProbe:
        """Verify the fixed provider login and return only a sanitized state."""

        try:
            host, port, username, password = self._connection(context)
            result = await self.transport.probe(
                host=host,
                port=port,
                username=username,
                password=password,
            )
        except asyncio.CancelledError:
            raise
        except _UnsupportedProvider:
            return NativeAppAdapterProbe(ready=False, issue="unsupported_imap_provider")
        except Exception:
            return NativeAppAdapterProbe(ready=False, issue="provider_unreachable")
        if result.get("ok") is True:
            return NativeAppAdapterProbe(ready=True)
        return NativeAppAdapterProbe(
            ready=False,
            issue=str(result.get("error") or "provider_request_failed")[:128],
        )

    def build_tools(self, context: NativeAppContext) -> tuple[Any, ...]:
        """Build the selected tools with a fixed IMAPS endpoint and read-only calls."""

        host, port, username, password = self._connection(context)
        origin = f"imaps://{host}:{port}/"

        async def list_messages(args: dict[str, Any]) -> dict[str, Any]:
            return await self.transport.list_messages(
                host=host,
                port=port,
                username=username,
                password=password,
                limit=_bounded_int(args.get("limit"), default=10, minimum=1, maximum=20),
            )

        async def search_messages(args: dict[str, Any]) -> dict[str, Any]:
            return await self.transport.search_messages(
                host=host,
                port=port,
                username=username,
                password=password,
                query=_required_ascii_text(args, "query", 256),
                limit=_bounded_int(args.get("limit"), default=10, minimum=1, maximum=20),
            )

        async def get_message(args: dict[str, Any]) -> dict[str, Any]:
            return await self.transport.get_message(
                host=host,
                port=port,
                username=username,
                password=password,
                uid=_required_uid(args),
            )

        operations: dict[str, tuple[dict[str, Any], Operation]] = {
            "imap_list_messages": (_object_schema({"limit": _integer()}), list_messages),
            "imap_search_messages": (
                _object_schema(
                    {
                        "query": _string("ASCII text searched by the IMAP server."),
                        "limit": _integer(),
                    },
                    required=("query",),
                ),
                search_messages,
            ),
            "imap_get_message": (
                _object_schema(
                    {"uid": _string("Numeric IMAP UID returned by list or search.")},
                    required=("uid",),
                ),
                get_message,
            ),
        }
        tools: list[NativeAppTool] = []
        for tool in context.tools:
            operation = operations.get(tool.name)
            if operation is None:
                continue
            schema, callback = operation
            tools.append(
                NativeAppTool(
                    spec=tool,
                    parameters=schema,
                    operation=callback,
                    adapter_id=self.adapter_id,
                    network_origin=origin,
                )
            )
        return tuple(tools)

    @staticmethod
    def _connection(context: NativeAppContext) -> tuple[str, int, str, str]:
        username = context.credential("email-address").reveal().strip()
        password = context.credential("app-password").reveal()
        if (
            not username
            or len(username) > 320
            or any(ord(character) < 33 or ord(character) == 127 for character in username)
            or not password
        ):
            raise ValueError("IMAP credentials are invalid")
        _, separator, domain = username.rpartition("@")
        endpoint = _ENDPOINTS.get(domain.lower()) if separator else None
        if endpoint is None:
            raise _UnsupportedProvider("IMAP provider is not reviewed")
        return endpoint[0], endpoint[1], username, password


async def _run_io(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run blocking stdlib IMAP I/O off the ADK event loop with stable failures."""

    try:
        return await asyncio.to_thread(operation)
    except asyncio.CancelledError:
        raise
    except (imaplib.IMAP4.error, OSError, ssl.SSLError, TimeoutError, ValueError):
        return {"ok": False, "error": "provider_request_failed"}


@contextmanager
def _session(host: str, port: int, username: str, password: str) -> Iterator[imaplib.IMAP4_SSL]:
    """Open one certificate-verified IMAPS session and always attempt logout."""

    client = imaplib.IMAP4_SSL(
        host,
        port,
        ssl_context=ssl.create_default_context(),
        timeout=_DEFAULT_TIMEOUT_SECONDS,
    )
    try:
        status, _ = client.login(username, password)
        _require_ok(status)
        yield client
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _probe(host: str, port: int, username: str, password: str) -> dict[str, Any]:
    with _session(host, port, username, password) as client:
        status, _ = client.noop()
        _require_ok(status)
    return {"ok": True, "data": {"ready": True}}


def _list(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    limit: int,
) -> dict[str, Any]:
    with _session(host, port, username, password) as client:
        _select_inbox(client)
        selected = tuple(reversed(_search_uids(client, "ALL")[-limit:]))
        messages = [_fetch_summary(client, uid) for uid in selected]
    return {"ok": True, "data": {"messages": messages}}


def _search(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    query: str,
    limit: int,
) -> dict[str, Any]:
    with _session(host, port, username, password) as client:
        _select_inbox(client)
        status, data = client.uid("search", None, "TEXT", _imap_quoted_string(query))
        _require_ok(status)
        selected = tuple(reversed(_parse_uid_data(data)[-limit:]))
        messages = [_fetch_summary(client, uid) for uid in selected]
    return {"ok": True, "data": {"messages": messages}}


def _get(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    uid: str,
) -> dict[str, Any]:
    with _session(host, port, username, password) as client:
        _select_inbox(client)
        status, data = client.uid("fetch", uid, f"(BODY.PEEK[]<0.{_MAX_FETCH_BYTES}>)")
        _require_ok(status)
        payload = _response_bytes(data)
    if not payload:
        raise ValueError("IMAP message was empty")
    message = BytesParser(policy=email_policy.default).parsebytes(payload)
    return {
        "ok": True,
        "data": {
            **_message_headers(uid, message),
            "body": _message_text(message),
            "truncated": len(payload) >= _MAX_FETCH_BYTES,
        },
    }


def _select_inbox(client: imaplib.IMAP4_SSL) -> None:
    status, _ = client.select("INBOX", readonly=True)
    _require_ok(status)


def _search_uids(client: imaplib.IMAP4_SSL, criterion: str) -> tuple[str, ...]:
    status, data = client.uid("search", None, criterion)
    _require_ok(status)
    return _parse_uid_data(data)


def _parse_uid_data(data: Any) -> tuple[str, ...]:
    if not isinstance(data, list):
        return ()
    tokens = b" ".join(item for item in data if isinstance(item, bytes)).split()
    return tuple(item.decode("ascii") for item in tokens if item.isdigit())


def _fetch_summary(client: imaplib.IMAP4_SSL, uid: str) -> dict[str, str]:
    status, data = client.uid(
        "fetch",
        uid,
        "(BODY.PEEK[HEADER.FIELDS (DATE FROM TO SUBJECT MESSAGE-ID)])",
    )
    _require_ok(status)
    payload = _response_bytes(data)
    if not payload:
        raise ValueError("IMAP message header was empty")
    return _message_headers(
        uid,
        BytesParser(policy=email_policy.default).parsebytes(payload),
    )


def _response_bytes(data: Any) -> bytes:
    if not isinstance(data, list):
        return b""
    chunks = [
        item[1]
        for item in data
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes)
    ]
    return b"".join(chunks)[:_MAX_FETCH_BYTES]


def _message_headers(uid: str, message: Any) -> dict[str, str]:
    return {
        "uid": uid,
        "subject": str(message.get("Subject", ""))[:998],
        "from": str(message.get("From", ""))[:2048],
        "to": str(message.get("To", ""))[:2048],
        "date": str(message.get("Date", ""))[:256],
        "messageId": str(message.get("Message-ID", ""))[:512],
    }


def _message_text(message: Any) -> str:
    candidates: list[str] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except Exception:
            continue
        if isinstance(content, str) and content:
            candidates.append(content)
        if sum(len(item) for item in candidates) >= _MAX_BODY_CHARS:
            break
    return "\n".join(candidates)[:_MAX_BODY_CHARS]


def _require_ok(status: Any) -> None:
    normalized = status.decode("ascii", errors="replace") if isinstance(status, bytes) else str(status)
    if normalized.upper() != "OK":
        raise imaplib.IMAP4.error("IMAP operation failed")


def _required_text(args: Mapping[str, Any], key: str, maximum: int) -> str:
    value = str(args.get(key, "")).strip()
    if not value or len(value) > maximum or any(ord(character) < 32 for character in value):
        raise ValueError(f"{key} is required and must be within {maximum} characters")
    return value


def _required_ascii_text(args: Mapping[str, Any], key: str, maximum: int) -> str:
    value = _required_text(args, key, maximum)
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{key} must use ASCII text in the first IMAP release") from exc
    return value


def _required_uid(args: Mapping[str, Any]) -> str:
    value = _required_text(args, "uid", 32)
    if not value.isascii() or not value.isdigit():
        raise ValueError("uid must be numeric")
    return value


def _imap_quoted_string(value: str) -> str:
    """Encode one validated search value as an IMAP quoted string."""

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value) if value is not None else default
    except (TypeError, ValueError):
        resolved = default
    return max(minimum, min(resolved, maximum))


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _integer() -> dict[str, Any]:
    return {"type": "integer"}


def _object_schema(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


__all__ = ["ImapReadOnlyAdapter", "NativeImapTransport", "StdlibImapTransport"]
