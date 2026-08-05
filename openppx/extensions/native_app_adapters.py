"""Verified Node-shipped adapters for high-confidence branded Apps.

The App catalog remains declarative.  This module is the only place where
product-specific HTTP behavior is registered, which keeps credentials inside
the Node and prevents an App definition from importing arbitrary code.
"""

from __future__ import annotations

import asyncio
import base64
from email.message import EmailMessage
from typing import Any, Awaitable, Callable, Mapping, Protocol
from urllib.parse import quote

import httpx
from google.adk.tools.base_tool import BaseTool
from google.genai import types

from .app_adapters import (
    NativeAppAdapterProbe,
    NativeAppAdapterReadiness,
    NativeAppAdapterRegistry,
    NativeAppContext,
)
from .app_models import AppToolSpec


_DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_RESPONSE_BYTES = 512 * 1024


class NativeAppHttpTransport(Protocol):
    """Small injectable HTTP boundary used by native App adapters."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one bounded request and return a client-safe payload."""
        ...


class HttpxNativeAppTransport:
    """Production HTTP transport with bounded time and response size."""

    def __init__(
        self,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a fixed provider endpoint without exposing request credentials."""
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers=dict(headers or {}),
                    params=dict(params or {}),
                    json=dict(json_body) if json_body is not None else None,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            return {"ok": False, "error": "provider_unreachable"}
        if len(response.content) > self.max_response_bytes:
            return {
                "ok": False,
                "status": response.status_code,
                "error": "provider_response_too_large",
            }
        if response.status_code == 204 or not response.content:
            data: Any = {}
        else:
            try:
                data = response.json()
            except ValueError:
                data = {"text": response.text[:4096]}
        if response.is_error:
            return {
                "ok": False,
                "status": response.status_code,
                "error": "provider_request_rejected",
            }
        return {"ok": True, "status": response.status_code, "data": data}


NativeOperation = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class NativeAppTool(BaseTool):
    """One schema-first ADK tool backed by a trusted native operation."""

    def __init__(
        self,
        *,
        spec: AppToolSpec,
        parameters: dict[str, Any],
        operation: NativeOperation,
        adapter_id: str,
    ) -> None:
        super().__init__(
            name=spec.name,
            description=spec.description,
            custom_metadata={
                "openppx": {
                    "extensionKind": "app",
                    "adapter": adapter_id,
                    "access": spec.access,
                    "risk": spec.risk,
                }
            },
        )
        self._parameters = parameters
        self._operation = operation

    def _get_declaration(self) -> types.FunctionDeclaration:
        """Return the exact JSON schema advertised to the model."""
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parametersJsonSchema=self._parameters,
        )

    async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> Any:
        """Execute one provider operation with stable, non-sensitive failures."""
        del tool_context
        try:
            return await self._operation(dict(args))
        except asyncio.CancelledError:
            raise
        except Exception:
            return {"ok": False, "error": "app_operation_failed"}


class _NativeRestAdapter:
    """Base for a fixed set of REST operations shipped by the Node."""

    adapter_id = ""

    def __init__(self, transport: NativeAppHttpTransport | None = None) -> None:
        self.transport = transport or HttpxNativeAppTransport()

    def readiness(self, context: NativeAppContext) -> NativeAppAdapterReadiness:
        """Return ready after the App manager has validated credential presence."""
        del context
        return NativeAppAdapterReadiness(ready=True)

    async def probe(self, context: NativeAppContext) -> NativeAppAdapterProbe:
        """Call a minimal provider identity endpoint and discard its response body."""
        result = await self._probe(context)
        data = result.get("data")
        provider_rejected = isinstance(data, Mapping) and data.get("ok") is False
        if bool(result.get("ok")) and not provider_rejected:
            return NativeAppAdapterProbe(ready=True)
        issue = (
            "provider_rejected_credentials"
            if provider_rejected
            else str(result.get("error") or "provider_request_failed")[:128]
        )
        return NativeAppAdapterProbe(ready=False, issue=issue)

    def build_tools(self, context: NativeAppContext) -> tuple[Any, ...]:
        """Build only the tools selected by this immutable App connection."""
        operations = self._operations(context)
        built: list[NativeAppTool] = []
        for tool in context.tools:
            operation = operations.get(tool.name)
            if operation is None:
                continue
            schema, callback = operation
            built.append(
                NativeAppTool(
                    spec=tool,
                    parameters=schema,
                    operation=callback,
                    adapter_id=self.adapter_id,
                )
            )
        return tuple(built)

    def _operations(
        self,
        context: NativeAppContext,
    ) -> dict[str, tuple[dict[str, Any], NativeOperation]]:
        raise NotImplementedError

    async def _probe(self, context: NativeAppContext) -> dict[str, Any]:
        raise NotImplementedError


class TelegramBotAdapter(_NativeRestAdapter):
    """Telegram Bot API read/send tools using a protected bot token."""

    adapter_id = "telegram-bot-api"

    async def _probe(self, context: NativeAppContext) -> dict[str, Any]:
        token = context.credential("bot-token").reveal()
        return await self.transport.request(
            "GET",
            f"https://api.telegram.org/bot{token}/getMe",
        )

    def _operations(self, context: NativeAppContext):
        token = context.credential("bot-token").reveal()
        base = f"https://api.telegram.org/bot{token}"

        async def get_updates(args: dict[str, Any]) -> dict[str, Any]:
            limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=100)
            offset = args.get("offset")
            params: dict[str, Any] = {"limit": limit, "timeout": 0}
            if offset is not None:
                params["offset"] = int(offset)
            return await self.transport.request("GET", f"{base}/getUpdates", params=params)

        async def send_message(args: dict[str, Any]) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "chat_id": _required_text(args, "chat_id", 256),
                "text": _required_text(args, "text", 4096),
            }
            if thread_id := _optional_text(args, "message_thread_id", 64):
                payload["message_thread_id"] = int(thread_id)
            return await self.transport.request(
                "POST",
                f"{base}/sendMessage",
                json_body=payload,
            )

        return {
            "telegram_get_updates": (_object_schema({"limit": _integer(), "offset": _integer()}), get_updates),
            "telegram_send_message": (
                _object_schema(
                    {
                        "chat_id": _string("Telegram chat identifier."),
                        "text": _string("Message text."),
                        "message_thread_id": _string("Optional forum topic identifier."),
                    },
                    required=("chat_id", "text"),
                ),
                send_message,
            ),
        }


class SlackWebApiAdapter(_NativeRestAdapter):
    """Slack Web API tools using a manually provisioned bot token."""

    adapter_id = "slack-web-api"

    async def _probe(self, context: NativeAppContext) -> dict[str, Any]:
        token = context.credential("bot-token").reveal()
        return await self.transport.request(
            "POST",
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
        )

    def _operations(self, context: NativeAppContext):
        token = context.credential("bot-token").reveal()
        headers = {"Authorization": f"Bearer {token}"}
        base = "https://slack.com/api"

        async def list_channels(args: dict[str, Any]) -> dict[str, Any]:
            return await self.transport.request(
                "GET",
                f"{base}/conversations.list",
                headers=headers,
                params={
                    "limit": _bounded_int(args.get("limit"), default=100, minimum=1, maximum=200),
                    "exclude_archived": True,
                },
            )

        async def history(args: dict[str, Any]) -> dict[str, Any]:
            return await self.transport.request(
                "GET",
                f"{base}/conversations.history",
                headers=headers,
                params={
                    "channel": _required_text(args, "channel", 256),
                    "limit": _bounded_int(args.get("limit"), default=50, minimum=1, maximum=100),
                },
            )

        async def post_message(args: dict[str, Any]) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "channel": _required_text(args, "channel", 256),
                "text": _required_text(args, "text", 40_000),
            }
            if thread_ts := _optional_text(args, "thread_ts", 64):
                payload["thread_ts"] = thread_ts
            return await self.transport.request(
                "POST",
                f"{base}/chat.postMessage",
                headers=headers,
                json_body=payload,
            )

        return {
            "slack_list_channels": (_object_schema({"limit": _integer()}), list_channels),
            "slack_read_messages": (
                _object_schema(
                    {"channel": _string("Slack channel ID."), "limit": _integer()},
                    required=("channel",),
                ),
                history,
            ),
            "slack_send_message": (
                _object_schema(
                    {
                        "channel": _string("Slack channel ID."),
                        "text": _string("Message text."),
                        "thread_ts": _string("Optional parent message timestamp."),
                    },
                    required=("channel", "text"),
                ),
                post_message,
            ),
        }


class GmailApiAdapter(_NativeRestAdapter):
    """Gmail REST tools using a protected OAuth access token."""

    adapter_id = "gmail-api"

    async def _probe(self, context: NativeAppContext) -> dict[str, Any]:
        token = context.credential("access-token").reveal()
        return await self.transport.request(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers=_bearer_headers(token),
        )

    def _operations(self, context: NativeAppContext):
        token = context.credential("access-token").reveal()
        headers = _bearer_headers(token)
        base = "https://gmail.googleapis.com/gmail/v1/users/me"

        async def search(args: dict[str, Any]) -> dict[str, Any]:
            return await self.transport.request(
                "GET",
                f"{base}/messages",
                headers=headers,
                params={
                    "q": _required_text(args, "query", 2048),
                    "maxResults": _bounded_int(args.get("max_results"), default=10, minimum=1, maximum=20),
                },
            )

        async def get_message(args: dict[str, Any]) -> dict[str, Any]:
            message_id = quote(_required_text(args, "message_id", 256), safe="")
            return await self.transport.request(
                "GET",
                f"{base}/messages/{message_id}",
                headers=headers,
                params={"format": "full"},
            )

        async def send(args: dict[str, Any]) -> dict[str, Any]:
            message = EmailMessage()
            message["To"] = _required_text(args, "to", 2048)
            message["Subject"] = _required_text(args, "subject", 998)
            if cc := _optional_text(args, "cc", 2048):
                message["Cc"] = cc
            message.set_content(_required_text(args, "body", 100_000))
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
            return await self.transport.request(
                "POST",
                f"{base}/messages/send",
                headers=headers,
                json_body={"raw": raw},
            )

        return {
            "gmail_search_messages": (
                _object_schema(
                    {"query": _string("Gmail search query."), "max_results": _integer()},
                    required=("query",),
                ),
                search,
            ),
            "gmail_get_message": (
                _object_schema({"message_id": _string("Gmail message ID.")}, required=("message_id",)),
                get_message,
            ),
            "gmail_send_email": (
                _object_schema(
                    {
                        "to": _string("Recipient addresses."),
                        "subject": _string("Email subject."),
                        "body": _string("Plain text message body."),
                        "cc": _string("Optional CC addresses."),
                    },
                    required=("to", "subject", "body"),
                ),
                send,
            ),
        }


class GoogleCalendarApiAdapter(_NativeRestAdapter):
    """Google Calendar REST tools using a protected OAuth access token."""

    adapter_id = "google-calendar-api"

    async def _probe(self, context: NativeAppContext) -> dict[str, Any]:
        token = context.credential("access-token").reveal()
        return await self.transport.request(
            "GET",
            "https://www.googleapis.com/calendar/v3/calendars/primary",
            headers=_bearer_headers(token),
        )

    def _operations(self, context: NativeAppContext):
        token = context.credential("access-token").reveal()
        headers = _bearer_headers(token)
        base = "https://www.googleapis.com/calendar/v3"

        async def list_events(args: dict[str, Any]) -> dict[str, Any]:
            calendar_id = quote(_optional_text(args, "calendar_id", 1024) or "primary", safe="")
            params: dict[str, Any] = {
                "singleEvents": True,
                "orderBy": "startTime",
                "maxResults": _bounded_int(args.get("max_results"), default=10, minimum=1, maximum=20),
            }
            if value := _optional_text(args, "time_min", 128):
                params["timeMin"] = value
            if value := _optional_text(args, "time_max", 128):
                params["timeMax"] = value
            return await self.transport.request(
                "GET",
                f"{base}/calendars/{calendar_id}/events",
                headers=headers,
                params=params,
            )

        async def free_busy(args: dict[str, Any]) -> dict[str, Any]:
            calendars = [
                {"id": item.strip()}
                for item in _optional_text(args, "calendars", 4096).split(",")
                if item.strip()
            ] or [{"id": "primary"}]
            return await self.transport.request(
                "POST",
                f"{base}/freeBusy",
                headers=headers,
                json_body={
                    "timeMin": _required_text(args, "time_min", 128),
                    "timeMax": _required_text(args, "time_max", 128),
                    "timeZone": _optional_text(args, "timezone", 128) or "UTC",
                    "items": calendars,
                },
            )

        async def create_event(args: dict[str, Any]) -> dict[str, Any]:
            calendar_id = quote(_optional_text(args, "calendar_id", 1024) or "primary", safe="")
            timezone = _optional_text(args, "timezone", 128) or "UTC"
            return await self.transport.request(
                "POST",
                f"{base}/calendars/{calendar_id}/events",
                headers=headers,
                json_body={
                    "summary": _required_text(args, "summary", 1024),
                    "description": _optional_text(args, "description", 8192),
                    "start": {"dateTime": _required_text(args, "start", 128), "timeZone": timezone},
                    "end": {"dateTime": _required_text(args, "end", 128), "timeZone": timezone},
                },
            )

        common = {
            "calendar_id": _string("Calendar ID; defaults to primary."),
            "time_min": _string("RFC3339 lower bound."),
            "time_max": _string("RFC3339 upper bound."),
            "max_results": _integer(),
        }
        return {
            "gcal_list_events": (_object_schema(common), list_events),
            "gcal_free_busy": (
                _object_schema(
                    {
                        "time_min": _string("RFC3339 lower bound."),
                        "time_max": _string("RFC3339 upper bound."),
                        "calendars": _string("Comma-separated calendar IDs."),
                        "timezone": _string("IANA timezone, defaults to UTC."),
                    },
                    required=("time_min", "time_max"),
                ),
                free_busy,
            ),
            "gcal_create_event": (
                _object_schema(
                    {
                        "summary": _string("Event title."),
                        "start": _string("Start date-time."),
                        "end": _string("End date-time."),
                        "calendar_id": common["calendar_id"],
                        "timezone": _string("IANA timezone, defaults to UTC."),
                        "description": _string("Optional event description."),
                    },
                    required=("summary", "start", "end"),
                ),
                create_event,
            ),
        }


class MicrosoftGraphAdapter(_NativeRestAdapter):
    """Outlook mail/calendar tools through Microsoft Graph."""

    adapter_id = "microsoft-graph"

    async def _probe(self, context: NativeAppContext) -> dict[str, Any]:
        token = context.credential("access-token").reveal()
        return await self.transport.request(
            "GET",
            "https://graph.microsoft.com/v1.0/me",
            headers=_bearer_headers(token),
            params={"$select": "id"},
        )

    def _operations(self, context: NativeAppContext):
        token = context.credential("access-token").reveal()
        headers = _bearer_headers(token)
        base = "https://graph.microsoft.com/v1.0/me"

        async def search_messages(args: dict[str, Any]) -> dict[str, Any]:
            params: dict[str, Any] = {
                "$top": _bounded_int(args.get("max_results"), default=10, minimum=1, maximum=20)
            }
            if query_value := _optional_text(args, "query", 2048):
                params["$search"] = f'"{query_value}"'
            return await self.transport.request(
                "GET",
                f"{base}/messages",
                headers=headers,
                params=params,
            )

        async def send_mail(args: dict[str, Any]) -> dict[str, Any]:
            return await self.transport.request(
                "POST",
                f"{base}/sendMail",
                headers=headers,
                json_body={
                    "message": {
                        "subject": _required_text(args, "subject", 998),
                        "body": {
                            "contentType": "Text",
                            "content": _required_text(args, "body", 100_000),
                        },
                        "toRecipients": [
                            {
                                "emailAddress": {
                                    "address": _required_text(args, "to", 2048)
                                }
                            }
                        ],
                    }
                },
            )

        async def list_events(args: dict[str, Any]) -> dict[str, Any]:
            params: dict[str, Any] = {
                "$top": _bounded_int(args.get("max_results"), default=10, minimum=1, maximum=50),
                "$orderby": "start/dateTime",
            }
            if value := _optional_text(args, "start", 128):
                params["startDateTime"] = value
            if value := _optional_text(args, "end", 128):
                params["endDateTime"] = value
            path = "calendarView" if "startDateTime" in params and "endDateTime" in params else "events"
            return await self.transport.request(
                "GET",
                f"{base}/{path}",
                headers=headers,
                params=params,
            )

        async def create_event(args: dict[str, Any]) -> dict[str, Any]:
            timezone = _optional_text(args, "timezone", 128) or "UTC"
            payload: dict[str, Any] = {
                "subject": _required_text(args, "subject", 1024),
                "body": {"contentType": "Text", "content": _optional_text(args, "body", 8192)},
                "start": {"dateTime": _required_text(args, "start", 128), "timeZone": timezone},
                "end": {"dateTime": _required_text(args, "end", 128), "timeZone": timezone},
            }
            attendees = [item.strip() for item in _optional_text(args, "attendees", 4096).split(",") if item.strip()]
            if attendees:
                payload["attendees"] = [
                    {"emailAddress": {"address": item}, "type": "required"}
                    for item in attendees
                ]
            return await self.transport.request(
                "POST",
                f"{base}/events",
                headers=headers,
                json_body=payload,
            )

        return {
            "outlook_search_messages": (
                _object_schema({"query": _string("Search text."), "max_results": _integer()}),
                search_messages,
            ),
            "outlook_send_mail": (
                _object_schema(
                    {
                        "to": _string("Recipient address."),
                        "subject": _string("Email subject."),
                        "body": _string("Plain text message body."),
                    },
                    required=("to", "subject", "body"),
                ),
                send_mail,
            ),
            "outlook_list_events": (
                _object_schema(
                    {"start": _string("Optional ISO start."), "end": _string("Optional ISO end."), "max_results": _integer()}
                ),
                list_events,
            ),
            "outlook_create_event": (
                _object_schema(
                    {
                        "subject": _string("Event title."),
                        "start": _string("Start date-time."),
                        "end": _string("End date-time."),
                        "timezone": _string("IANA timezone, defaults to UTC."),
                        "body": _string("Optional event body."),
                        "attendees": _string("Comma-separated attendee addresses."),
                    },
                    required=("subject", "start", "end"),
                ),
                create_event,
            ),
        }


def default_native_app_adapter_registry(
    *,
    transport: NativeAppHttpTransport | None = None,
) -> NativeAppAdapterRegistry:
    """Return the verified native adapter set shipped by this Node version."""
    registry = NativeAppAdapterRegistry()
    for adapter in (
        TelegramBotAdapter(transport),
        SlackWebApiAdapter(transport),
        GmailApiAdapter(transport),
        GoogleCalendarApiAdapter(transport),
        MicrosoftGraphAdapter(transport),
    ):
        registry.register(adapter)
    return registry


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _required_text(args: Mapping[str, Any], key: str, maximum: int) -> str:
    value = str(args.get(key, "")).strip()
    if not value or len(value) > maximum or any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise ValueError(f"{key} is required and must be within {maximum} characters")
    return value


def _optional_text(args: Mapping[str, Any], key: str, maximum: int) -> str:
    value = str(args.get(key, "")).strip()
    if len(value) > maximum or any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise ValueError(f"{key} must be within {maximum} characters")
    return value


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


__all__ = [
    "GmailApiAdapter",
    "GoogleCalendarApiAdapter",
    "HttpxNativeAppTransport",
    "MicrosoftGraphAdapter",
    "NativeAppHttpTransport",
    "NativeAppTool",
    "SlackWebApiAdapter",
    "TelegramBotAdapter",
    "default_native_app_adapter_registry",
]
