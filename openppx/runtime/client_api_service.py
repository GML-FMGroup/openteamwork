"""Local HTTP + SSE client API service for openppx."""

from __future__ import annotations

import datetime as dt
import asyncio
import base64
import binascii
import json
import os
import queue
import threading
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from google.genai import types
from pydantic import ValidationError

from ..actions import ActionContext, ActionError, ActionOutcome
from ..client_api.contracts import ActionInvokeRequest, ContractMapper
from ..config import ConfigError
from ..control_plane import CONTROL_PLANE_CAPABILITIES, ControlPlaneApplication, build_control_plane
from ..core.logging_utils import debug_logging_enabled, emit_debug
from ..extensions.mcp_oauth import CALLBACK_PATH
from .access_policy import AccessPolicy
from .agent_access_runtime import ensure_access_principal
from .agent_access_store import AgentAccessStore, AgentMembership, AgentRecord
from .attachment_service import (
    AttachmentValidationError,
    MAX_ATTACHMENT_BYTES,
    MAX_MESSAGE_ATTACHMENT_BYTES,
    MAX_MESSAGE_ATTACHMENTS,
    prepare_attachment,
)
from .client_api_auth import (
    ClientApiAuthPolicy,
)
from .client_api_contract import (
    CLIENT_API_CAPABILITIES,
    build_client_api_health_data,
    build_client_api_node_data,
    build_public_client_api_health_data,
)
from .identity_models import ResolvedPrincipal
from .identity_store import IdentityStore
from .memory_query_service import MemoryQueryService
from .memory_shared import memory_entry_text
from .paths import default_node_root
from .sqlite_memory_service import SQLiteMemoryService
from .session_metadata_store import SessionMetadataStore


_MAX_JSON_BODY_BYTES = 30 * 1024 * 1024


def _safe_artifact_name(value: object) -> str:
    """Validate one user-visible artifact filename without accepting a path."""
    name = str(value or "").strip()
    if not name or len(name) > 255 or name in {".", ".."}:
        raise ValueError("Artifact filename must contain between 1 and 255 characters.")
    if "/" in name or "\\" in name or any(ord(character) < 32 for character in name):
        raise ValueError("Artifact filename must not contain a path or control characters.")
    return name


def _iso_now() -> str:
    """Return the current timestamp as an ISO 8601 string."""

    return dt.datetime.now().astimezone().isoformat()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    """Encode one JSON payload using UTF-8."""

    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _wire_id(value: object, *, prefix: str) -> str:
    """Return one bounded visible wire ID or generate a safe replacement."""
    candidate = str(value or "").strip()
    if candidate and len(candidate) <= 128 and all(ord(character) >= 32 for character in candidate):
        return candidate
    return f"{prefix}_{os.urandom(8).hex()}"


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    """Build a success envelope."""

    return {"ok": True, "data": data}


def _error(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build an error envelope."""

    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }


def _normalize_agent_name(value: str) -> str:
    """Normalize one agent id using the existing filesystem-safe convention."""

    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip().lower())
    return normalized.strip("-_")


_MUTATION_AUDIT_ACTIONS = (
    "set_owner",
    "upsert_membership",
    "delete_membership",
    "batch_add_participants",
    "batch_remove_participants",
    "sync_participants",
)


def _normalize_principal_id_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize one list of principal ids while preserving stable order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        principal_id = str(raw or "").strip()
        if not principal_id or principal_id in seen:
            continue
        seen.add(principal_id)
        normalized.append(principal_id)
    return normalized


def _normalize_access_audit_category(value: str | None) -> str:
    """Normalize one admin-audit category selector."""
    normalized = str(value or "all").strip().lower()
    if normalized in {"", "all", "admin"}:
        return "all"
    if normalized == "mutation":
        return "mutation"
    raise ValueError("Query parameter 'category' must be 'all' or 'mutation'.")


def _actions_for_access_audit_category(category: str) -> tuple[str, ...] | None:
    """Return the audit actions included in one category filter."""
    normalized = _normalize_access_audit_category(category)
    if normalized == "all":
        return None
    return _MUTATION_AUDIT_ACTIONS


def _preview_value(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or fallback
    try:
        dumped = json.dumps(value if value is not None else {}, ensure_ascii=False, indent=2)
    except Exception:
        dumped = str(value)
    dumped = dumped.strip()
    if not dumped or dumped == "{}":
        return fallback
    return dumped[:320] + ("..." if len(dumped) > 320 else "")


def _strip_request_time_prefix(text: str) -> str:
    """Remove runtime-injected request-time guidance from persisted user text."""

    stripped = text.strip()
    if not stripped.startswith("Current request time: "):
        return text

    lines = stripped.splitlines()
    if len(lines) < 2 or "Use this as the reference 'now' for relative time expressions" not in lines[1]:
        return text

    body_lines = lines[2:]
    while body_lines and not body_lines[0].strip():
        body_lines = body_lines[1:]
    return "\n".join(body_lines).strip()


def _step_ref_payload(*, step_id: str, title: str, status: str, detail: str) -> dict[str, Any]:
    """Build one client-facing step part payload."""

    return {
        "type": "step_ref",
        "step_id": step_id,
        "title": title,
        "status": status,
        "detail": detail,
    }


def _message_payload(
    *,
    message_id: str,
    session_id: str,
    run_id: str,
    role: str,
    parts: list[dict[str, Any]],
    status: str,
) -> dict[str, Any]:
    """Build one client-facing message payload."""

    return {
        "id": message_id,
        "session_id": session_id,
        "run_id": run_id,
        "role": role,
        "parts": parts,
        "status": status,
        "created_at": _iso_now(),
        "metadata": {},
    }


def _error_part_payload(*, code: str, text: str) -> dict[str, Any]:
    """Build one client-facing error part payload."""

    return {
        "type": "error",
        "error_code": code,
        "text": text,
    }


def _tool_result_payload(
    *,
    tool_name: str,
    summary: str,
    detail: str,
    raw_text: str,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    """Build one client-facing tool result part payload."""

    payload = {
        "type": "tool_result",
        "tool_name": tool_name,
        "summary": summary,
        "detail": detail,
        "raw_text": raw_text,
    }
    if tool_call_id:
        payload["tool_call_id"] = tool_call_id
    return payload


def _tool_result_summary(tool_name: str, response: Any) -> str:
    """Build a short human-readable summary for one tool response."""

    if isinstance(response, dict):
        message = response.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        summary = response.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        ok = response.get("ok")
        if isinstance(ok, bool):
            return f"{tool_name} returned successfully." if ok else f"{tool_name} reported a failure."
        keys = list(response.keys())
        if keys:
            return f"{tool_name} returned {len(keys)} fields."
    if isinstance(response, str) and response.strip():
        return response.strip()[:140]
    return f"{tool_name} returned a result."


def _event_preview_text(event: dict[str, Any]) -> str:
    """Build a lightweight session preview string from one serialized event."""

    content = event.get("content") if isinstance(event.get("content"), dict) else {}
    raw_parts = content.get("parts") if isinstance(content.get("parts"), list) else []
    texts: list[str] = []
    for raw_part in raw_parts:
        if not isinstance(raw_part, dict):
            continue
        if bool(raw_part.get("thought")):
            continue
        text = raw_part.get("text")
        if isinstance(text, str) and text.strip():
            normalized_text = _strip_request_time_prefix(text)
            if normalized_text.strip():
                texts.append(normalized_text.strip())
    return " ".join(texts).strip()


def _compact_session_title(text: str, *, limit: int = 64) -> str:
    """Return a single-line session title derived from user-visible text."""
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _session_title_from_events(events: list[dict[str, Any]]) -> str:
    """Return the first user message as the stable client-facing session title."""
    for event in events:
        if str(event.get("author") or "").strip().lower() != "user":
            continue
        title = _compact_session_title(_event_preview_text(event))
        if title:
            return title
    return ""


def _debug(tag: str, payload: Any) -> None:
    """Emit one structured debug log when client-api debugging is enabled."""

    if not debug_logging_enabled():
        return
    emit_debug(tag, payload, depth=3)


def project_session_event(event: dict[str, Any], session_id: str) -> dict[str, Any] | None:
    """Project one ADK session event into the client chat message schema."""

    author = str(event.get("author") or "").strip().lower()
    role = "assistant"
    if author == "user":
        role = "user"
    elif author == "tool":
        role = "tool"
    elif author == "system":
        role = "system"

    timestamp = event.get("timestamp")
    if isinstance(timestamp, (int, float)):
        created_at = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).astimezone().isoformat()
    else:
        created_at = _iso_now()

    content = event.get("content") if isinstance(event.get("content"), dict) else {}
    raw_parts = content.get("parts") if isinstance(content, dict) and isinstance(content.get("parts"), list) else []
    has_function_call = any(
        isinstance(raw_part, dict) and isinstance(raw_part.get("function_call"), dict)
        for raw_part in raw_parts
    )
    parts: list[dict[str, Any]] = []
    for raw_part in raw_parts:
        if not isinstance(raw_part, dict):
            continue
        if bool(raw_part.get("thought")):
            continue
        text = raw_part.get("text")
        if isinstance(text, str) and text.strip():
            normalized_text = _strip_request_time_prefix(text)
            if normalized_text.strip():
                part_type = "commentary" if role == "assistant" and has_function_call else "markdown"
                parts.append({"type": part_type, "text": normalized_text})
        inline_data = raw_part.get("inline_data")
        if isinstance(inline_data, dict):
            encoded_data = str(inline_data.get("data") or "")
            mime_type = str(inline_data.get("mime_type") or "application/octet-stream")
            display_name = str(inline_data.get("display_name") or "Attachment")
            size_bytes = (len(encoded_data.rstrip("=")) * 3) // 4 if encoded_data else 0
            if mime_type.startswith("image/") and encoded_data:
                parts.append(
                    {
                        "type": "image",
                        "text": display_name,
                        "url": f"data:{mime_type};base64,{encoded_data}",
                        "mime_type": mime_type,
                    }
                )
            else:
                parts.append(
                    {
                        "type": "file",
                        "text": "Attached file",
                        "file_name": display_name,
                        "size_bytes": size_bytes,
                        "mime_type": mime_type,
                    }
                )
        function_call = raw_part.get("function_call")
        if isinstance(function_call, dict):
            parts.append(
                {
                    "type": "step_ref",
                    "step_id": str(function_call.get("id") or "step"),
                    "title": str(function_call.get("name") or "tool"),
                    "status": "completed",
                    "detail": _preview_value(function_call.get("args"), "No tool arguments"),
                }
            )
        function_response = raw_part.get("function_response")
        if isinstance(function_response, dict):
            step_id = str(function_response.get("id") or function_response.get("name") or "tool")
            tool_name = str(function_response.get("name") or "tool")
            response = function_response.get("response") or {}
            parts.append(
                _tool_result_payload(
                    tool_name=tool_name,
                    summary=_tool_result_summary(tool_name, response),
                    detail=_preview_value(response, "Tool returned without a payload"),
                    raw_text=json.dumps(response, ensure_ascii=False, indent=2),
                    tool_call_id=step_id,
                )
            )
    if not parts:
        return None
    invocation_id = str(event.get("invocation_id") or "").strip()
    return {
        "id": str(event.get("id") or f"msg_{session_id}"),
        "session_id": session_id,
        "run_id": invocation_id or None,
        "role": role,
        "parts": parts,
        "status": "completed",
        "created_at": created_at,
        "metadata": {},
    }


@dataclass(slots=True)
class RunEnvelope:
    """One replayable SSE event payload."""

    event_id: str
    seq: int
    event: str
    payload: dict[str, Any]


@dataclass(slots=True)
class _TimedCacheEntry:
    """One short-lived in-memory cache entry."""

    value: Any
    expires_at: float


class RunHandle:
    """Track one Node-owned Run and its replayable SSE events."""

    def __init__(
        self,
        *,
        run_id: str,
        agent_id: str,
        session_id: str,
    ) -> None:
        self.run_id = run_id
        self.agent_id = agent_id
        self.session_id = session_id
        self.assistant_message_id = f"msg_{run_id}_assistant"
        self._history: list[RunEnvelope] = []
        self._subscribers: list[queue.Queue[RunEnvelope | None]] = []
        self._lock = threading.Lock()
        self._seq = 0
        self.done = threading.Event()
        self.failed = False
        self.invocation_id = ""

    def observe_invocation(self, invocation_id: str) -> None:
        """Remember the ADK invocation that produced this client Run."""
        normalized = str(invocation_id or "").strip()
        if normalized:
            with self._lock:
                self.invocation_id = normalized

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        """Store and fan out one SSE event."""

        with self._lock:
            self._seq += 1
            envelope = RunEnvelope(
                event_id=f"{self.run_id}:{self._seq}",
                seq=self._seq,
                event=event,
                payload=payload,
            )
            self._history.append(envelope)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.put(envelope)

    def finish(self) -> None:
        """Mark the run as completed and close subscribers."""

        with self._lock:
            self.done.set()
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for subscriber in subscribers:
            subscriber.put(None)

    def subscribe(self, last_event_id: str | None = None) -> queue.Queue[RunEnvelope | None]:
        """Create one subscriber queue and replay retained history."""

        q: queue.Queue[RunEnvelope | None] = queue.Queue()
        with self._lock:
            replay = list(self._history)
            if last_event_id:
                last_seen = next((item.seq for item in replay if item.event_id == last_event_id), None)
                if last_seen is not None:
                    replay = [item for item in replay if item.seq > last_seen]
            if not self.done.is_set():
                self._subscribers.append(q)
            done = self.done.is_set()
        for item in replay:
            q.put(item)
        if done:
            q.put(None)
        return q


class ClientApiCoordinator:
    """Coordinate local client-facing HTTP requests and background run streams."""

    _CACHE_TTL_SECONDS = 5.0

    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        identity_store: IdentityStore | None = None,
        agent_access_store: AgentAccessStore | None = None,
        access_policy: AccessPolicy | None = None,
        memory_query_service: MemoryQueryService | None = None,
        control_plane: ControlPlaneApplication | None = None,
        runtime_supervisor: Any | None = None,
        session_metadata: SessionMetadataStore | None = None,
    ) -> None:
        self.data_dir = data_dir or default_node_root()
        default_identity_db_path = self.data_dir / "database" / "identity.db"
        self._identity_store = identity_store or IdentityStore(db_path=default_identity_db_path)
        self._agent_access_store = agent_access_store or AgentAccessStore(db_path=default_identity_db_path)
        self._access_policy = access_policy or AccessPolicy(
            identity_store=self._identity_store,
            agent_access_store=self._agent_access_store,
        )
        if memory_query_service is None:
            local_memory_db_path = self.data_dir / "database" / "memory.db"
            self._memory_query_service = MemoryQueryService(
                identity_store=self._identity_store,
                access_policy=self._access_policy,
                memory_service=SQLiteMemoryService(db_path=local_memory_db_path),
                audit_db_path=local_memory_db_path,
            )
        else:
            self._memory_query_service = memory_query_service
        self._session_agents: dict[str, str] = {}
        self._session_owners: dict[str, str] = {}
        self._runs: dict[str, RunHandle] = {}
        self._lock = threading.Lock()
        self._sessions_cache: dict[tuple[str, str], _TimedCacheEntry] = {}
        self._messages_cache: dict[tuple[str, str], _TimedCacheEntry] = {}
        self._control_plane = control_plane or build_control_plane(self.data_dir)
        self._runtime_supervisor = runtime_supervisor
        self._session_metadata = session_metadata or SessionMetadataStore(
            self.data_dir / "database" / "sessions.db"
        )
        if runtime_supervisor is not None:
            attached = self._control_plane.runtime_supervisor
            if attached is None:
                self._control_plane.attach_runtime(
                    runtime_supervisor,
                    session_metadata=self._session_metadata,
                )
            elif attached is not runtime_supervisor:
                raise ValueError("Control Plane and Client API must share one Runtime Supervisor.")

    def _control_context(
        self,
        *,
        request_id: str,
        correlation_id: str | None = None,
        actor_id: str = "service:client-api",
        confirmed: bool = False,
    ) -> ActionContext:
        """Build the trusted transport-service context for current Client API projections."""
        permissions = frozenset(
            {
                "system.read",
                "config.read",
                "config.write",
                "extension.auth",
                "extension.read",
                "extension.write",
                "flow.read",
                "flow.write",
                "goal.read",
                "goal.write",
                "model.read",
                "model.write",
                "model.use",
                "operations.read",
                "operations.write",
                "audit.read",
                "automation.read",
                "automation.run",
                "automation.write",
                "session.read",
                "session.write",
                "run.control",
                "run.start",
                "secret.read",
                "secret.write",
                "setup.read",
                "setup.write",
                "task.read",
                "task.control",
            }
        )
        return ActionContext(
            request_id=request_id,
            correlation_id=correlation_id or request_id,
            actor_id=actor_id,
            client_id="client-api",
            capabilities=permissions,
            permissions=permissions,
            confirmed=confirmed,
        )

    def _record_goal_fact(self, method_name: str, **facts: object) -> None:
        """Attach a best-effort runtime fact without masking the authoritative ADK Run."""
        try:
            method = getattr(self._control_plane.goal_store, method_name)
            method(**facts)
        except Exception as exc:
            _debug(
                "goal_fact_record_failed",
                {
                    "method": method_name,
                    "run_id": facts.get("run_id"),
                    "session_id": facts.get("session_id"),
                    "error": str(exc),
                },
            )
    def _invoke_control(self, action_id: str, payload: dict[str, object] | None = None) -> ActionOutcome:
        """Invoke one migrated business operation through the Control Plane."""
        request_id = f"client_api_{action_id.replace('.', '_')}"
        return self._control_plane.invoke(
            action_id,
            payload or {},
            self._control_context(request_id=request_id),
        )

    def deliver_mcp_oauth_callback(
        self,
        *,
        code: str,
        state: str | None,
        error: str = "",
    ) -> bool:
        """Deliver one public, state-gated OAuth callback to the Node-owned MCP flow."""
        service = self._control_plane.mcp_oauth_service
        if service is None:
            return False
        return service.deliver_callback(code, state, error=error)

    def action_catalog(
        self,
        *,
        namespace: str | None = None,
        projection: str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the final caller-aware Action catalog envelope."""
        resolved_request_id = _wire_id(request_id, prefix="req")
        resolved_correlation_id = _wire_id(correlation_id or resolved_request_id, prefix="corr")
        context = self._control_context(
            request_id=resolved_request_id,
            correlation_id=resolved_correlation_id,
        )
        outcome = self._control_plane.catalog(
            context,
            namespace=namespace,
            projection=projection,
        )
        envelope = ContractMapper().from_outcome(
            outcome,
            request_id=resolved_request_id,
            correlation_id=resolved_correlation_id,
        )
        return envelope.model_dump(mode="json", by_alias=True)

    def invoke_action(
        self,
        action_id: str,
        raw_input: dict[str, object],
        *,
        request_id: str,
        correlation_id: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        """Invoke one final product Action and return the common contract envelope."""
        context = self._control_context(
            request_id=request_id,
            correlation_id=correlation_id,
            actor_id="principal:client-api",
            confirmed=confirmed,
        )
        outcome = self._control_plane.invoke(action_id, raw_input, context)
        if outcome.ok and action_id in {
            "session.rename",
            "session.archive",
            "session.fork",
            "session.delete",
        }:
            agent_id = str(raw_input.get("agentId") or "")
            user_id = str(raw_input.get("userId") or "")
            if agent_id:
                self._invalidate_agent_cache(agent_id, user_id=user_id or "ppx-client-user")
            session_id = str(raw_input.get("sessionId") or "")
            if session_id:
                self._invalidate_session_cache(session_id, user_id=user_id or "ppx-client-user")
        envelope = ContractMapper().from_outcome(
            outcome,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        return envelope.model_dump(mode="json", by_alias=True)

    def _enabled_agent_ids(self) -> tuple[str, ...]:
        """Return enabled strict Agent IDs without parsing Config in the transport layer."""
        outcome = self._invoke_control("config.agent.list")
        if not outcome.ok or outcome.data is None:
            return ()
        items = outcome.data.get("items")
        if not isinstance(items, list):
            return ()
        return tuple(str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id"))

    def _ensure_requester_principal(self, user_id: str) -> ResolvedPrincipal:
        """Return a persisted requester principal for client-api operations."""
        principal_id = str(user_id or "ppx-client-user").strip() or "ppx-client-user"
        existing = self._identity_store.get_principal(principal_id)
        if existing is not None:
            return existing
        principal = ResolvedPrincipal(
            principal_id=principal_id,
            principal_type="human",
            privilege_level="minimal",
            account_kind="local_client",
            display_name=principal_id,
            authenticated=True,
            external_subject_id=principal_id,
            external_display_id=principal_id,
            metadata={"source": "client_api"},
        )
        return self._identity_store.put_principal(principal)

    def _ensure_agent_access_state(self, agent_id: str) -> Path | None:
        """Project strict Agent ownership into the access store before evaluation."""
        if agent_id not in self._enabled_agent_ids():
            return None
        try:
            resource = self._control_plane.config_repository.read_agent(agent_id)
        except ConfigError:
            return None
        agent = resource.document
        existing = self._agent_access_store.get_agent_record(agent_id)
        owner_source = str(existing.metadata.get("owner_source", "")) if existing is not None else ""
        owner_principal_id = agent.spec.owner_principal_id
        if existing is not None and existing.owner_principal_id and owner_source not in {"", "config"}:
            owner_principal_id = existing.owner_principal_id
        ensure_access_principal(
            self._identity_store,
            principal_id=owner_principal_id,
            source=owner_source or "config",
            account_kind="configured_owner" if owner_source in {"", "config"} else "managed_access",
        )
        self._agent_access_store.upsert_agent_record(
            AgentRecord(
                agent_id=agent_id,
                name=agent.spec.display_name,
                privilege_level=agent.spec.privilege_level,
                owner_principal_id=owner_principal_id,
                status=existing.status if existing is not None else "active",
                config_ref=str(resource.source.path),
                metadata={
                    **(existing.metadata if existing is not None else {}),
                    "owner_source": owner_source or "config",
                    "config_revision": resource.revision,
                },
            )
        )
        return resource.source.path

    def _visible_principal_ids(self, requester_principal_id: str, *, agent_id: str, access_kind: str) -> tuple[Any, tuple[str, ...]]:
        """Resolve the effective visible principal ids for one request."""
        decision = self._access_policy.decide_agent_scope(
            requester_principal_id=requester_principal_id,
            agent_id=agent_id,
            access_kind=access_kind,
        )
        if not decision.allow:
            return decision, ()
        visible_principal_ids = decision.resolved_scope(self._identity_store.list_principal_ids())
        if visible_principal_ids:
            return decision, visible_principal_ids
        return decision, (requester_principal_id,)

    def _read_cache(self, cache: dict[tuple[str, str], _TimedCacheEntry], key: tuple[str, str]) -> Any | None:
        now_ts = dt.datetime.now().timestamp()
        with self._lock:
            entry = cache.get(key)
            if entry is None:
                return None
            if entry.expires_at < now_ts:
                cache.pop(key, None)
                return None
            return entry.value

    def _write_cache(self, cache: dict[tuple[str, str], _TimedCacheEntry], key: tuple[str, str], value: Any) -> None:
        with self._lock:
            cache[key] = _TimedCacheEntry(
                value=value,
                expires_at=dt.datetime.now().timestamp() + self._CACHE_TTL_SECONDS,
            )

    def _invalidate_agent_cache(self, agent_id: str, *, user_id: str) -> None:
        with self._lock:
            self._sessions_cache.pop((agent_id, user_id), None)

    def _invalidate_session_cache(self, session_id: str, *, user_id: str) -> None:
        with self._lock:
            self._messages_cache.pop((session_id, user_id), None)

    def _invalidate_agent_access_caches(self, agent_id: str) -> None:
        """Drop cached views that may become stale after access mutations."""
        with self._lock:
            self._sessions_cache = {
                key: value for key, value in self._sessions_cache.items() if key[0] != agent_id
            }
            affected_session_ids = {
                session_id
                for session_id, cached_agent_id in self._session_agents.items()
                if cached_agent_id == agent_id
            }
            self._messages_cache = {
                key: value for key, value in self._messages_cache.items() if key[0] not in affected_session_ids
            }

    def _record_admin_audit(
        self,
        *,
        agent_id: str,
        requester: ResolvedPrincipal,
        action: str,
        relation_to_agent: str,
        target_principal_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Persist one admin-surface audit event without raising to callers."""
        try:
            self._agent_access_store.record_audit(
                agent_id=agent_id,
                actor_principal_id=requester.principal_id,
                actor_relation=relation_to_agent,
                action=action,
                target_principal_id=target_principal_id,
                details=details,
            )
        except Exception:
            return

    def _validate_membership_management(
        self,
        *,
        agent_id: str,
        requester: ResolvedPrincipal,
        access_kind: str = "membership_write",
        denied_action: str,
        denied_target_principal_id: str = "",
        denied_details: dict[str, Any] | None = None,
    ) -> tuple[Path | None, Any, dict[str, Any] | None]:
        """Validate one membership-management request and prebuild deny payloads."""
        config_path = self._ensure_agent_access_state(agent_id)
        if config_path is None:
            return None, None, _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        decision = self._access_policy.decide_agent_management(
            requester_principal_id=requester.principal_id,
            agent_id=agent_id,
            access_kind=access_kind,
        )
        if decision.allow:
            return config_path, decision, None
        self._record_admin_audit(
            agent_id=agent_id,
            requester=requester,
            action=denied_action,
            relation_to_agent=decision.relation_to_agent,
            target_principal_id=denied_target_principal_id,
            details={
                "allowed": False,
                "reason": decision.reason,
                **dict(denied_details or {}),
            },
        )
        return config_path, decision, _error(
            "ACCESS_DENIED",
            f"Principal '{requester.principal_id}' cannot change memberships for agent '{agent_id}'.",
            {"reason": decision.reason},
        )

    def _read_sessions_for_principal(self, config_path: Path, *, user_id: str) -> list[dict[str, Any]]:
        """Read principal-scoped Sessions from the Node Runtime Supervisor."""
        if self._runtime_supervisor is None:
            raise RuntimeError("The Node Runtime Supervisor is not attached.")
        agent_id = config_path.parent.name
        sessions = self._runtime_supervisor.list_sessions_sync(agent_id, user_id=user_id)
        items: list[dict[str, Any]] = []
        for session in sessions:
            detail = self._runtime_supervisor.get_session_sync(
                agent_id,
                user_id=user_id,
                session_id=str(session.id),
            )
            events = [event.model_dump(mode="json") for event in (detail.events if detail else [])]
            items.append(
                {
                    "id": str(session.id),
                    "last_update_time": detail.last_update_time if detail else session.last_update_time,
                    "title": _session_title_from_events(events),
                    "last_preview": _event_preview_text(events[-1]) if events else "",
                    "metadata": self._session_metadata.get(str(session.id)),
                }
            )
        return items

    def _get_session_for_principal(
        self,
        config_path: Path,
        *,
        user_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        """Read one principal-scoped Session from the Node Runtime Supervisor."""
        if self._runtime_supervisor is None:
            raise RuntimeError("The Node Runtime Supervisor is not attached.")
        session = self._runtime_supervisor.get_session_sync(
            config_path.parent.name,
            user_id=user_id,
            session_id=session_id,
        )
        if session is None:
            return None
        return {
            "id": str(session.id),
            "last_update_time": session.last_update_time,
            "events": [event.model_dump(mode="json") for event in session.events],
        }

    def _collect_visible_sessions(
        self,
        *,
        agent_id: str,
        requester_principal_id: str,
    ) -> tuple[Any, list[tuple[str, dict[str, Any]]]] | dict[str, Any]:
        """Collect session rows visible to one requester for one agent."""
        config_path = self._ensure_agent_access_state(agent_id)
        if config_path is None:
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        decision, visible_principal_ids = self._visible_principal_ids(
            requester_principal_id,
            agent_id=agent_id,
            access_kind="session_list",
        )
        if not decision.allow:
            return _error(
                "ACCESS_DENIED",
                f"Principal '{requester_principal_id}' cannot list sessions for agent '{agent_id}'.",
                {"reason": decision.reason},
            )

        rows: list[tuple[str, dict[str, Any]]] = []
        for subject_principal_id in visible_principal_ids:
            try:
                sessions = self._read_sessions_for_principal(config_path, user_id=subject_principal_id)
            except Exception as exc:
                return _error("RUNTIME_UNAVAILABLE", str(exc))
            for session in sessions:
                rows.append((subject_principal_id, session))
        return decision, rows

    def _find_session_owner(
        self,
        *,
        session_id: str,
        requester_principal_id: str,
    ) -> tuple[str, str] | dict[str, Any]:
        """Resolve the agent id and owner principal for one visible session."""
        agent_id = self._session_agents.get(session_id)
        subject_principal_id = self._session_owners.get(session_id)
        if agent_id and subject_principal_id:
            decision = self._access_policy.decide_subject_access(
                requester_principal_id=requester_principal_id,
                agent_id=agent_id,
                subject_principal_id=subject_principal_id,
                access_kind="session_read",
            )
            if decision.allow:
                return agent_id, subject_principal_id
            return _error(
                "ACCESS_DENIED",
                f"Principal '{requester_principal_id}' cannot read session '{session_id}'.",
                {"reason": decision.reason},
            )

        for candidate in self._enabled_agent_ids():
            visible = self._collect_visible_sessions(
                agent_id=candidate,
                requester_principal_id=requester_principal_id,
            )
            if isinstance(visible, dict):
                if visible.get("error", {}).get("code") == "ACCESS_DENIED":
                    continue
                return visible
            _decision, rows = visible
            for owner_principal_id, session in rows:
                candidate_session_id = str(session.get("id") or "")
                if candidate_session_id != session_id:
                    continue
                self._session_agents[session_id] = candidate
                self._session_owners[session_id] = owner_principal_id
                return candidate, owner_principal_id
        return _error("SESSION_NOT_FOUND", f"Session '{session_id}' was not found.")

    def health(self, *, public: bool = False) -> dict[str, Any]:
        """Return the versioned Client API readiness handshake."""

        if public:
            return _ok(build_public_client_api_health_data(timestamp=_iso_now()))
        status = self._invoke_control("system.status")
        data = status.data or {}
        state = "healthy" if data.get("state") == "ready" else "needs_configuration"
        agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
        return _ok(
            build_client_api_health_data(
                agents=int(agents.get("enabled") or 0),
                timestamp=_iso_now(),
                ready=state == "healthy",
                state=state,
            )
        )

    def node_info(self, *, authentication_required: bool) -> dict[str, Any]:
        """Return authenticated Node identity and capability metadata."""

        status = self._invoke_control("system.status")
        data = status.data or {}
        node = data.get("node") if isinstance(data.get("node"), dict) else None
        agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
        if node is None:
            return _error("NODE_NOT_CONFIGURED", "The Node Config resource is not ready.")
        capabilities = tuple(dict.fromkeys((*CONTROL_PLANE_CAPABILITIES, *CLIENT_API_CAPABILITIES)))
        return _ok(build_client_api_node_data(
            node_id=str(node.get("id") or ""),
            display_name=str(node.get("displayName") or "OpenPPX Node"),
            agents=int(agents.get("enabled") or 0),
            authentication_required=authentication_required,
            capabilities=capabilities,
        ))

    def runtime_status(self) -> dict[str, Any]:
        """Return a client-facing runtime status payload."""

        status = self._invoke_control("system.status")
        data = status.data or {}
        node = data.get("node") if isinstance(data.get("node"), dict) else {}
        ready = data.get("state") == "ready"
        return _ok({
            "target": {
                "id": str(node.get("id") or "unconfigured-node"),
                "type": "local",
                "name": str(node.get("displayName") or "OpenPPX Node"),
            },
            "state": "healthy" if ready else "error",
            "summary": "OpenPPX Node is ready." if ready else "OpenPPX Node needs configuration.",
            "detail": "The Client API delegates system state to the Control Plane.",
        })

    def list_agents(self) -> dict[str, Any]:
        """Return enabled local agent profiles."""

        outcome = self._invoke_control("config.agent.list")
        if outcome.ok and outcome.data is not None:
            return _ok(outcome.data)
        assert outcome.error is not None
        return _error(outcome.error.code.upper(), outcome.error.message, outcome.error.details)

    def list_sessions(self, agent_id: str, *, user_id: str = "ppx-client-user") -> dict[str, Any]:
        """Return projected session summaries for one agent."""

        requester = self._ensure_requester_principal(user_id)
        if agent_id not in self._enabled_agent_ids():
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        config_path = self._ensure_agent_access_state(agent_id)
        if config_path is None:
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        if not config_path.exists():
            return _error("RUNTIME_NOT_CONFIGURED", f"Agent '{agent_id}' runtime is not configured.")
        cache_key = (agent_id, requester.principal_id)
        cached = self._read_cache(self._sessions_cache, cache_key)
        if cached is not None:
            _debug(
                "client_api.list_sessions.cache_hit",
                {"agent_id": agent_id, "user_id": requester.principal_id, "count": len(cached)},
            )
            return _ok({"items": cached})
        visible = self._collect_visible_sessions(
            agent_id=agent_id,
            requester_principal_id=requester.principal_id,
        )
        if isinstance(visible, dict):
            return visible
        _decision, session_rows = visible
        items = []
        for subject_principal_id, session in session_rows:
            session_id = str(session.get("id") or "")
            if not session_id:
                continue
            self._session_agents[session_id] = agent_id
            self._session_owners[session_id] = subject_principal_id
            updated_raw = session.get("last_update_time")
            if isinstance(updated_raw, (int, float)):
                updated_at = dt.datetime.fromtimestamp(updated_raw, tz=dt.timezone.utc).astimezone().isoformat()
            else:
                updated_at = _iso_now()
            items.append(
                {
                    "id": session_id,
                    "agent_id": agent_id,
                    "subject_principal_id": subject_principal_id,
                    "title": (
                        session["metadata"].title
                        if session.get("metadata") is not None and session["metadata"].title
                        else str(session.get("title") or "").strip() or f"Session {session_id[:8]}"
                    ),
                    "updated_at": updated_at,
                    "last_message_preview": str(session.get("last_preview") or ""),
                    "archived": bool(session["metadata"].archived) if session.get("metadata") is not None else False,
                }
            )
        items.sort(key=lambda item: item["updated_at"], reverse=True)
        self._write_cache(self._sessions_cache, cache_key, items)
        return _ok({"items": items})

    def create_session(self, agent_id: str, *, user_id: str = "ppx-client-user") -> dict[str, Any]:
        """Create one Session through the shared Control Plane Action."""

        requester = self._ensure_requester_principal(user_id)
        if self._ensure_agent_access_state(agent_id) is None:
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        outcome = self._invoke_control(
            "session.new",
            {"agentId": agent_id, "userId": requester.principal_id},
        )
        if not outcome.ok or outcome.data is None:
            error = outcome.error
            return _error(
                str(error.code if error is not None else "RUNTIME_UNAVAILABLE").upper(),
                error.message if error is not None else "The Node runtime is unavailable.",
                error.details if error is not None else None,
            )
        session = outcome.data.get("session")
        if not isinstance(session, dict):
            return _error("RUNTIME_UNAVAILABLE", "The Session Action returned an invalid result.")
        session_id = str(session.get("id") or "")
        if not session_id:
            return _error("RUNTIME_UNAVAILABLE", "The Session Action returned no Session id.")
        self._session_agents[session_id] = agent_id
        self._session_owners[session_id] = requester.principal_id
        self._invalidate_agent_cache(agent_id, user_id=requester.principal_id)
        self._invalidate_session_cache(session_id, user_id=requester.principal_id)
        updated_at = str(session.get("updatedAt") or _iso_now())
        return _ok(
            {
                "session": {
                    "id": session_id,
                    "agent_id": agent_id,
                    "subject_principal_id": requester.principal_id,
                    "title": "New chat",
                    "updated_at": updated_at,
                    "last_message_preview": "",
                    "archived": False,
                }
            }
        )

    def get_session_messages(self, session_id: str, *, user_id: str = "ppx-client-user") -> dict[str, Any]:
        """Return projected message history for one session."""

        requester = self._ensure_requester_principal(user_id)
        cache_key = (session_id, requester.principal_id)
        cached = self._read_cache(self._messages_cache, cache_key)
        if cached is not None:
            _debug(
                "client_api.get_session.cache_hit",
                {
                    "session_id": session_id,
                    "user_id": requester.principal_id,
                    "count": len(cached),
                },
            )
            return _ok({"items": cached})
        location = self._find_session_owner(
            session_id=session_id,
            requester_principal_id=requester.principal_id,
        )
        if isinstance(location, dict):
            return location
        agent_id, subject_principal_id = location
        config_path = self._ensure_agent_access_state(agent_id)
        if config_path is None:
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        try:
            session = self._get_session_for_principal(
                config_path,
                user_id=subject_principal_id,
                session_id=session_id,
            )
        except Exception as exc:
            return _error("RUNTIME_UNAVAILABLE", str(exc))
        if session is None:
            return _error("SESSION_NOT_FOUND", f"Session '{session_id}' was not found.")
        events = session.get("events") if isinstance(session.get("events"), list) else []
        messages = [
            message
            for event in events
            if isinstance(event, dict)
            for message in [project_session_event(event, session_id)]
            if message is not None
        ]
        for message in messages:
            metadata = message.setdefault("metadata", {})
            metadata["subject_principal_id"] = subject_principal_id
        self._write_cache(self._messages_cache, cache_key, messages)
        return _ok({"items": messages})

    def get_agent_access(self, agent_id: str, *, user_id: str = "ppx-client-user") -> dict[str, Any]:
        """Return the requester's visible access snapshot for one agent."""

        requester = self._ensure_requester_principal(user_id)
        if self._ensure_agent_access_state(agent_id) is None:
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        decision = self._access_policy.decide_agent_scope(
            requester_principal_id=requester.principal_id,
            agent_id=agent_id,
            access_kind="agent_access_read",
        )
        if not decision.allow:
            self._record_admin_audit(
                agent_id=agent_id,
                requester=requester,
                action="read_access",
                relation_to_agent=decision.relation_to_agent,
                details={"allowed": False, "reason": decision.reason},
            )
            return _error(
                "ACCESS_DENIED",
                f"Principal '{requester.principal_id}' cannot read access state for agent '{agent_id}'.",
                {"reason": decision.reason},
            )

        record = self._agent_access_store.get_agent_record(agent_id)
        if record is None:
            return _error("RUNTIME_UNAVAILABLE", f"Agent '{agent_id}' access record is unavailable.")

        visible_principal_ids = set(decision.resolved_scope(self._identity_store.list_principal_ids()))
        memberships = []
        for membership in self._agent_access_store.list_memberships(agent_id=agent_id):
            if membership.principal_id not in visible_principal_ids and decision.scope_kind != "all":
                continue
            principal = self._identity_store.get_principal(membership.principal_id)
            memberships.append(
                {
                    "principal_id": membership.principal_id,
                    "relation": membership.relation,
                    "joined_at_ms": membership.joined_at_ms,
                    "metadata": dict(membership.metadata),
                    "display_name": principal.display_name if principal is not None else membership.principal_id,
                    "principal_type": principal.principal_type if principal is not None else "unknown",
                    "privilege_level": principal.privilege_level if principal is not None else "",
                }
            )

        owner_visible = bool(record.owner_principal_id) and decision.allows_principal(record.owner_principal_id)
        payload = _ok(
            {
                "agent": {
                    "id": record.agent_id,
                    "name": record.name,
                    "privilege_level": record.privilege_level,
                    "owner_principal_id": record.owner_principal_id if owner_visible else None,
                    "owner_configured": bool(record.owner_principal_id),
                    "status": record.status,
                    "config_ref": record.config_ref or None,
                    "metadata": dict(record.metadata),
                },
                "requester": {
                    "principal_id": requester.principal_id,
                    "relation": decision.relation_to_agent,
                    "reason": decision.reason,
                    "scope_kind": decision.scope_kind,
                    "capabilities": {
                        "can_manage_memberships": self._access_policy.decide_agent_management(
                            requester_principal_id=requester.principal_id,
                            agent_id=agent_id,
                            access_kind="membership_write",
                        ).allow,
                        "can_read_access_audit": self._access_policy.decide_agent_management(
                            requester_principal_id=requester.principal_id,
                            agent_id=agent_id,
                            access_kind="access_audit_read",
                        ).allow,
                        "can_read_admin_audit": self._access_policy.decide_agent_management(
                            requester_principal_id=requester.principal_id,
                            agent_id=agent_id,
                            access_kind="access_audit_read",
                        ).allow,
                        "can_change_owner": self._access_policy.decide_agent_management(
                            requester_principal_id=requester.principal_id,
                            agent_id=agent_id,
                            access_kind="ownership_write",
                        ).allow,
                    },
                },
                "memberships": memberships,
            }
        )
        self._record_admin_audit(
            agent_id=agent_id,
            requester=requester,
            action="read_access",
            relation_to_agent=decision.relation_to_agent,
            details={
                "allowed": True,
                "reason": decision.reason,
                "visible_membership_count": len(memberships),
                "owner_visible": owner_visible,
            },
        )
        return payload

    def set_agent_owner(
        self,
        agent_id: str,
        owner_principal_id: str,
        *,
        user_id: str = "ppx-client-user",
    ) -> dict[str, Any]:
        """Set one agent owner through the managed access layer."""

        requester = self._ensure_requester_principal(user_id)
        if self._ensure_agent_access_state(agent_id) is None:
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        decision = self._access_policy.decide_agent_management(
            requester_principal_id=requester.principal_id,
            agent_id=agent_id,
            access_kind="ownership_write",
        )
        if not decision.allow:
            self._record_admin_audit(
                agent_id=agent_id,
                requester=requester,
                action="set_owner",
                relation_to_agent=decision.relation_to_agent,
                target_principal_id=str(owner_principal_id or "").strip(),
                details={"allowed": False, "reason": decision.reason, "source": "client_api"},
            )
            return _error(
                "ACCESS_DENIED",
                f"Principal '{requester.principal_id}' cannot change owner for agent '{agent_id}'.",
                {"reason": decision.reason},
            )

        normalized_owner_principal_id = str(owner_principal_id or "").strip()
        if not normalized_owner_principal_id:
            return _error("INVALID_REQUEST", "Field 'owner_principal_id' is required.")

        owner_principal = ensure_access_principal(
            self._identity_store,
            principal_id=normalized_owner_principal_id,
            source="client_api_access_mutation",
            account_kind="managed_access",
        )
        record = self._agent_access_store.get_agent_record(agent_id)
        if owner_principal is None or record is None:
            return _error("RUNTIME_UNAVAILABLE", f"Agent '{agent_id}' access record is unavailable.")
        previous_owner_principal_id = record.owner_principal_id

        updated = self._agent_access_store.upsert_agent_record(
            AgentRecord(
                agent_id=record.agent_id,
                name=record.name,
                privilege_level=record.privilege_level,
                owner_principal_id=owner_principal.principal_id,
                status=record.status,
                config_ref=record.config_ref,
                metadata={
                    **dict(record.metadata),
                    "owner_source": "client_api",
                },
            )
        )
        self._agent_access_store.record_audit(
            agent_id=agent_id,
            actor_principal_id=requester.principal_id,
            actor_relation=decision.relation_to_agent,
            action="set_owner",
            target_principal_id=owner_principal.principal_id,
            details={
                "allowed": True,
                "reason": decision.reason,
                "previous_owner_principal_id": previous_owner_principal_id,
                "owner_principal_id": owner_principal.principal_id,
                "changed": previous_owner_principal_id != owner_principal.principal_id,
                "source": "client_api",
            },
        )
        self._invalidate_agent_access_caches(agent_id)
        return _ok(
            {
                "agent": {
                    "id": updated.agent_id,
                    "owner_principal_id": updated.owner_principal_id,
                    "metadata": dict(updated.metadata),
                }
            }
        )

    def upsert_agent_membership(
        self,
        agent_id: str,
        principal_id: str,
        *,
        relation: str = "participant",
        user_id: str = "ppx-client-user",
    ) -> dict[str, Any]:
        """Create or update one agent membership through the managed access layer."""

        requester = self._ensure_requester_principal(user_id)
        _config_path, decision, denied = self._validate_membership_management(
            agent_id=agent_id,
            requester=requester,
            denied_action="upsert_membership",
            denied_target_principal_id=str(principal_id or "").strip(),
            denied_details={"source": "client_api", "relation": str(relation or "").strip().lower()},
        )
        if denied is not None:
            return denied

        normalized_principal_id = str(principal_id or "").strip()
        normalized_relation = str(relation or "").strip().lower()
        if not normalized_principal_id:
            return _error("INVALID_REQUEST", "Field 'principal_id' is required.")
        if normalized_relation != "participant":
            return _error("INVALID_REQUEST", "Field 'relation' must currently be 'participant'.")

        principal = ensure_access_principal(
            self._identity_store,
            principal_id=normalized_principal_id,
            source="client_api_access_mutation",
            account_kind="managed_access",
        )
        if principal is None:
            return _error("RUNTIME_UNAVAILABLE", "Could not ensure the target principal.")
        previous_membership = self._agent_access_store.get_membership(
            agent_id=agent_id,
            principal_id=principal.principal_id,
        )

        membership = self._agent_access_store.upsert_membership(
            AgentMembership(
                agent_id=agent_id,
                principal_id=principal.principal_id,
                relation=normalized_relation,
                metadata={"source": "client_api"},
            )
        )
        self._agent_access_store.record_audit(
            agent_id=agent_id,
            actor_principal_id=requester.principal_id,
            actor_relation=decision.relation_to_agent,
            action="upsert_membership",
            target_principal_id=membership.principal_id,
            details={
                "allowed": True,
                "reason": decision.reason,
                "relation": membership.relation,
                "previous_relation": previous_membership.relation if previous_membership is not None else None,
                "changed": previous_membership is None
                or previous_membership.relation != membership.relation
                or dict(previous_membership.metadata) != dict(membership.metadata),
                "joined_at_ms": membership.joined_at_ms,
                "source": "client_api",
            },
        )
        self._invalidate_agent_access_caches(agent_id)
        return _ok(
            {
                "membership": {
                    "agent_id": membership.agent_id,
                    "principal_id": membership.principal_id,
                    "relation": membership.relation,
                    "joined_at_ms": membership.joined_at_ms,
                    "metadata": dict(membership.metadata),
                }
            }
        )

    def delete_agent_membership(
        self,
        agent_id: str,
        principal_id: str,
        *,
        user_id: str = "ppx-client-user",
    ) -> dict[str, Any]:
        """Delete one agent membership through the managed access layer."""

        requester = self._ensure_requester_principal(user_id)
        _config_path, decision, denied = self._validate_membership_management(
            agent_id=agent_id,
            requester=requester,
            denied_action="delete_membership",
            denied_target_principal_id=str(principal_id or "").strip(),
            denied_details={"source": "client_api"},
        )
        if denied is not None:
            return denied

        normalized_principal_id = str(principal_id or "").strip()
        if not normalized_principal_id:
            return _error("INVALID_REQUEST", "Field 'principal_id' is required.")
        previous_membership = self._agent_access_store.get_membership(
            agent_id=agent_id,
            principal_id=normalized_principal_id,
        )

        deleted = self._agent_access_store.delete_membership(
            agent_id=agent_id,
            principal_id=normalized_principal_id,
        )
        self._agent_access_store.record_audit(
            agent_id=agent_id,
            actor_principal_id=requester.principal_id,
            actor_relation=decision.relation_to_agent,
            action="delete_membership",
            target_principal_id=normalized_principal_id,
            details={
                "allowed": True,
                "reason": decision.reason,
                "deleted": deleted,
                "previous_relation": previous_membership.relation if previous_membership is not None else None,
                "source": "client_api",
            },
        )
        self._invalidate_agent_access_caches(agent_id)
        return _ok({"deleted": deleted, "principal_id": normalized_principal_id})

    def batch_add_participants(
        self,
        agent_id: str,
        principal_ids: list[str] | tuple[str, ...],
        *,
        user_id: str = "ppx-client-user",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Add multiple participant memberships in one managed operation."""
        return self._batch_manage_participants(
            agent_id=agent_id,
            principal_ids=principal_ids,
            operation="add",
            user_id=user_id,
            dry_run=dry_run,
        )

    def batch_remove_participants(
        self,
        agent_id: str,
        principal_ids: list[str] | tuple[str, ...],
        *,
        user_id: str = "ppx-client-user",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Remove multiple participant memberships in one managed operation."""
        return self._batch_manage_participants(
            agent_id=agent_id,
            principal_ids=principal_ids,
            operation="remove",
            user_id=user_id,
            dry_run=dry_run,
        )

    def sync_participants(
        self,
        agent_id: str,
        principal_ids: list[str] | tuple[str, ...],
        *,
        user_id: str = "ppx-client-user",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Synchronize participant memberships to exactly the requested set."""
        return self._batch_manage_participants(
            agent_id=agent_id,
            principal_ids=principal_ids,
            operation="sync",
            user_id=user_id,
            dry_run=dry_run,
        )

    def _batch_manage_participants(
        self,
        *,
        agent_id: str,
        principal_ids: list[str] | tuple[str, ...],
        operation: str,
        user_id: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Apply one batch participant management operation with one summary audit row."""
        requester = self._ensure_requester_principal(user_id)
        normalized_principal_ids = _normalize_principal_id_list(principal_ids)
        if not normalized_principal_ids:
            return _error("INVALID_REQUEST", "Field 'principal_ids' must contain at least one principal id.")

        action_name = {
            "add": "batch_add_participants",
            "remove": "batch_remove_participants",
            "sync": "sync_participants",
        }.get(operation, "")
        if not action_name:
            return _error("INVALID_REQUEST", f"Unsupported batch operation '{operation}'.")

        _config_path, decision, denied = self._validate_membership_management(
            agent_id=agent_id,
            requester=requester,
            denied_action=action_name,
            denied_details={
                "source": "client_api",
                "dry_run": bool(dry_run),
                "requested_principal_ids": normalized_principal_ids,
            },
        )
        if denied is not None:
            return denied

        current_memberships = self._agent_access_store.list_memberships(
            agent_id=agent_id,
            relations=("participant",),
        )
        current_ids = {membership.principal_id for membership in current_memberships}
        requested_ids = set(normalized_principal_ids)

        if operation == "add":
            added_ids = [principal_id for principal_id in normalized_principal_ids if principal_id not in current_ids]
            removed_ids: list[str] = []
            unchanged_ids = [principal_id for principal_id in normalized_principal_ids if principal_id in current_ids]
        elif operation == "remove":
            added_ids = []
            removed_ids = [principal_id for principal_id in normalized_principal_ids if principal_id in current_ids]
            unchanged_ids = [principal_id for principal_id in normalized_principal_ids if principal_id not in current_ids]
        else:
            added_ids = [principal_id for principal_id in normalized_principal_ids if principal_id not in current_ids]
            removed_ids = sorted(principal_id for principal_id in current_ids if principal_id not in requested_ids)
            unchanged_ids = [principal_id for principal_id in normalized_principal_ids if principal_id in current_ids]

        if not dry_run:
            for principal_id in added_ids:
                principal = ensure_access_principal(
                    self._identity_store,
                    principal_id=principal_id,
                    source="client_api_access_mutation",
                    account_kind="managed_access",
                )
                if principal is None:
                    return _error("RUNTIME_UNAVAILABLE", f"Could not ensure principal '{principal_id}'.")
                self._agent_access_store.upsert_membership(
                    AgentMembership(
                        agent_id=agent_id,
                        principal_id=principal.principal_id,
                        relation="participant",
                        metadata={"source": "client_api_batch"},
                    )
                )
            for principal_id in removed_ids:
                self._agent_access_store.delete_membership(agent_id=agent_id, principal_id=principal_id)
            if added_ids or removed_ids:
                self._invalidate_agent_access_caches(agent_id)

        audit_details = {
            "allowed": True,
            "reason": decision.reason,
            "source": "client_api",
            "dry_run": bool(dry_run),
            "applied": not dry_run,
            "requested_principal_ids": normalized_principal_ids,
            "added_principal_ids": added_ids,
            "removed_principal_ids": removed_ids,
            "unchanged_principal_ids": unchanged_ids,
            "requested_count": len(normalized_principal_ids),
            "added_count": len(added_ids),
            "removed_count": len(removed_ids),
            "unchanged_count": len(unchanged_ids),
        }
        self._record_admin_audit(
            agent_id=agent_id,
            requester=requester,
            action=action_name,
            relation_to_agent=decision.relation_to_agent,
            details=audit_details,
        )
        return _ok(
            {
                "operation": action_name,
                "dry_run": bool(dry_run),
                "applied": not dry_run,
                "requested_principal_ids": normalized_principal_ids,
                "added_principal_ids": added_ids,
                "removed_principal_ids": removed_ids,
                "unchanged_principal_ids": unchanged_ids,
                "summary": {
                    "requested_count": len(normalized_principal_ids),
                    "added_count": len(added_ids),
                    "removed_count": len(removed_ids),
                    "unchanged_count": len(unchanged_ids),
                },
            }
        )

    def _artifact_scope(
        self,
        agent_id: str,
        session_id: str,
        *,
        user_id: str,
    ) -> tuple[Any, ResolvedPrincipal] | dict[str, Any]:
        """Resolve an authorized ADK artifact service for one Session."""
        requester = self._ensure_requester_principal(user_id)
        if self._ensure_agent_access_state(agent_id) is None:
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        if self._runtime_supervisor is None:
            return _error("RUNTIME_UNAVAILABLE", "The Node Runtime Supervisor is not attached.")
        location = self._find_session_owner(
            session_id=session_id,
            requester_principal_id=requester.principal_id,
        )
        if isinstance(location, dict):
            return location
        located_agent_id, subject_principal_id = location
        if located_agent_id != agent_id or subject_principal_id != requester.principal_id:
            return _error("ACCESS_DENIED", "Artifact access requires the owning Session principal.")
        runtime = self._runtime_supervisor.runtime_for(agent_id)
        if runtime.artifact_service is None:
            return _error("ARTIFACTS_UNAVAILABLE", "Artifact storage is not enabled for this Node.")
        return runtime, requester

    def upload_artifact(
        self,
        agent_id: str,
        session_id: str,
        *,
        file_name: object,
        mime_type: object,
        data_base64: object,
        user_id: str = "ppx-client-user",
    ) -> dict[str, Any]:
        """Validate and persist one bounded upload in the Session artifact scope."""
        scope = self._artifact_scope(agent_id, session_id, user_id=user_id)
        if isinstance(scope, dict):
            return scope
        runtime, requester = scope
        try:
            resolved_name = _safe_artifact_name(file_name)
            encoded = str(data_base64 or "")
            if len(encoded) > ((MAX_ATTACHMENT_BYTES + 2) // 3) * 4 + 16:
                raise ValueError(f"Artifact exceeds the {MAX_ATTACHMENT_BYTES // 1024 // 1024} MB limit.")
            data = base64.b64decode(encoded, validate=True)
            prepared = prepare_attachment(
                file_name=resolved_name,
                mime_type=str(mime_type or "application/octet-stream"),
                data=data,
            )
        except (AttachmentValidationError, ValueError, binascii.Error) as exc:
            return _error("INVALID_ARTIFACT", str(exc))
        artifact_id = f"artifact_{os.urandom(8).hex()}"
        storage_key = f"uploads/{artifact_id}/{prepared.file_name}"
        created_at = _iso_now()
        try:
            version = asyncio.run(
                runtime.artifact_service.save_artifact(
                    app_name=runtime.agent.name,
                    user_id=requester.principal_id,
                    session_id=session_id,
                    filename=storage_key,
                    artifact=types.Part(
                        inline_data=types.Blob(
                            data=prepared.data,
                            mime_type=prepared.mime_type,
                            display_name=prepared.file_name,
                        )
                    ),
                    custom_metadata={
                        "artifact_id": artifact_id,
                        "source": "user_upload",
                        "file_name": prepared.file_name,
                        "size_bytes": len(prepared.data),
                        "created_at": created_at,
                        **prepared.metadata,
                    },
                )
            )
        except Exception:
            return _error("ARTIFACT_SAVE_FAILED", "The attachment could not be saved.")
        return _ok(
            {
                "artifact": {
                    "id": artifact_id,
                    "key": storage_key,
                    "file_name": prepared.file_name,
                    "mime_type": prepared.mime_type,
                    "size_bytes": len(prepared.data),
                    "version": int(version),
                    "source": "user_upload",
                    "created_at": created_at,
                }
            }
        )

    def list_artifacts(
        self,
        agent_id: str,
        session_id: str,
        *,
        user_id: str = "ppx-client-user",
    ) -> dict[str, Any]:
        """List Session-scoped ADK artifacts without exposing filesystem paths."""
        scope = self._artifact_scope(agent_id, session_id, user_id=user_id)
        if isinstance(scope, dict):
            return scope
        runtime, requester = scope
        try:
            keys = asyncio.run(
                runtime.artifact_service.list_artifact_keys(
                    app_name=runtime.agent.name,
                    user_id=requester.principal_id,
                    session_id=session_id,
                )
            )
            items: list[dict[str, Any]] = []
            for key in keys:
                versions = asyncio.run(
                    runtime.artifact_service.list_artifact_versions(
                        app_name=runtime.agent.name,
                        user_id=requester.principal_id,
                        session_id=session_id,
                        filename=key,
                    )
                )
                if not versions:
                    continue
                latest = versions[-1]
                metadata = dict(latest.custom_metadata or {})
                items.append(
                    {
                        "id": str(metadata.get("artifact_id") or key),
                        "key": key,
                        "file_name": str(metadata.get("file_name") or key.rsplit("/", 1)[-1]),
                        "mime_type": str(latest.mime_type or "application/octet-stream"),
                        "size_bytes": int(metadata.get("size_bytes") or 0),
                        "version": int(latest.version),
                        "source": str(metadata.get("source") or "agent_output"),
                        "created_at": str(metadata.get("created_at") or dt.datetime.fromtimestamp(latest.create_time).astimezone().isoformat()),
                    }
                )
        except Exception:
            return _error("ARTIFACT_LIST_FAILED", "Artifacts could not be listed.")
        items.sort(key=lambda item: item["created_at"], reverse=True)
        return _ok({"items": items})

    def load_artifact(
        self,
        agent_id: str,
        session_id: str,
        *,
        key: str,
        version: int | None = None,
        user_id: str = "ppx-client-user",
    ) -> tuple[dict[str, Any], bytes | None]:
        """Load one authorized Session artifact for download or model input."""
        scope = self._artifact_scope(agent_id, session_id, user_id=user_id)
        if isinstance(scope, dict):
            return scope, None
        runtime, requester = scope
        try:
            keys = asyncio.run(
                runtime.artifact_service.list_artifact_keys(
                    app_name=runtime.agent.name,
                    user_id=requester.principal_id,
                    session_id=session_id,
                )
            )
            if key not in keys:
                return _error("ARTIFACT_NOT_FOUND", "Artifact was not found in this Session."), None
            part = asyncio.run(
                runtime.artifact_service.load_artifact(
                    app_name=runtime.agent.name,
                    user_id=requester.principal_id,
                    session_id=session_id,
                    filename=key,
                    version=version,
                )
            )
        except Exception:
            return _error("ARTIFACT_LOAD_FAILED", "The Artifact could not be loaded."), None
        blob = getattr(part, "inline_data", None) if part is not None else None
        data = getattr(blob, "data", None) if blob is not None else None
        if not isinstance(data, bytes):
            return _error("ARTIFACT_NOT_FOUND", "Artifact content is unavailable."), None
        metadata: dict[str, Any] = {}
        try:
            versions = asyncio.run(
                runtime.artifact_service.list_artifact_versions(
                    app_name=runtime.agent.name,
                    user_id=requester.principal_id,
                    session_id=session_id,
                    filename=key,
                )
            )
            selected = next(
                (
                    item
                    for item in reversed(versions)
                    if version is None or int(item.version) == version
                ),
                None,
            )
            metadata = dict(getattr(selected, "custom_metadata", None) or {})
        except Exception:
            metadata = {}
        return _ok(
            {
                "mime_type": str(getattr(blob, "mime_type", None) or "application/octet-stream"),
                "file_name": str(metadata.get("file_name") or key.rsplit("/", 1)[-1]),
                "source": str(metadata.get("source") or "agent_output"),
            }
        ), data

    def _resolve_artifact_parts(
        self,
        agent_id: str,
        session_id: str,
        artifact_refs: list[dict[str, Any]],
        *,
        user_id: str,
    ) -> tuple[tuple[types.Part, ...], dict[str, Any] | None]:
        """Resolve opaque ArtifactRefs into ADK Parts after Session authorization."""
        parts: list[types.Part] = []
        if len(artifact_refs) > MAX_MESSAGE_ATTACHMENTS:
            return (), _error(
                "INVALID_ARTIFACT",
                f"A message can reference at most {MAX_MESSAGE_ATTACHMENTS} artifacts.",
            )
        total_bytes = 0
        for reference in artifact_refs:
            key = str(reference.get("key") or "")
            raw_version = reference.get("version")
            version = int(raw_version) if isinstance(raw_version, int) and raw_version >= 0 else None
            payload, data = self.load_artifact(
                agent_id,
                session_id,
                key=key,
                version=version,
                user_id=user_id,
            )
            if data is None:
                return (), payload
            total_bytes += len(data)
            if total_bytes > MAX_MESSAGE_ATTACHMENT_BYTES:
                return (), _error(
                    "INVALID_ARTIFACT",
                    f"Attachments for one message cannot exceed {MAX_MESSAGE_ATTACHMENT_BYTES // 1024 // 1024} MB in total.",
                )
            metadata = payload.get("data", {})
            mime_type = str(metadata.get("mime_type") or "application/octet-stream")
            file_name = str(metadata.get("file_name") or key.rsplit("/", 1)[-1])
            try:
                prepared = prepare_attachment(file_name=file_name, mime_type=mime_type, data=data)
            except AttachmentValidationError as exc:
                return (), _error("INVALID_ARTIFACT", str(exc))
            if prepared.model_text is not None:
                parts.append(types.Part(text=prepared.model_text))
            else:
                parts.append(
                    types.Part(
                        inline_data=types.Blob(
                            data=prepared.data,
                            mime_type=prepared.mime_type,
                            display_name=prepared.file_name,
                        )
                    )
                )
        return tuple(parts), None

    def create_run(
        self,
        agent_id: str,
        session_id: str,
        text: str,
        *,
        artifact_refs: list[dict[str, Any]] | None = None,
        user_id: str = "ppx-client-user",
    ) -> dict[str, Any]:
        """Create one streaming Run inside the shared Node Runtime Supervisor."""

        requester = self._ensure_requester_principal(user_id)
        resolved_artifact_parts, artifact_error = self._resolve_artifact_parts(
            agent_id,
            session_id,
            list(artifact_refs or []),
            user_id=requester.principal_id,
        )
        if artifact_error is not None:
            return artifact_error
        if self._ensure_agent_access_state(agent_id) is None:
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        if self._runtime_supervisor is None:
            return _error("RUNTIME_UNAVAILABLE", "The Node Runtime Supervisor is not attached.")
        location = self._find_session_owner(
            session_id=session_id,
            requester_principal_id=requester.principal_id,
        )
        if isinstance(location, dict):
            return location
        located_agent_id, subject_principal_id = location
        if located_agent_id != agent_id:
            return _error("SESSION_NOT_FOUND", f"Session '{session_id}' was not found for agent '{agent_id}'.")
        if subject_principal_id != requester.principal_id:
            return _error(
                "ACCESS_DENIED",
                f"Principal '{requester.principal_id}' cannot start a run in session '{session_id}'.",
                {"reason": "run_requires_session_owner"},
            )
        run_id = f"run_{os.urandom(8).hex()}"
        handle = RunHandle(run_id=run_id, agent_id=agent_id, session_id=session_id)
        with self._lock:
            self._runs[run_id] = handle
        self._session_agents[session_id] = agent_id
        self._session_owners[session_id] = requester.principal_id
        self._invalidate_agent_cache(agent_id, user_id=requester.principal_id)
        self._invalidate_session_cache(session_id, user_id=requester.principal_id)
        _debug(
            "client_api.create_run",
            {
                "run_id": run_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "user_id": requester.principal_id,
                "text_preview": text[:240] + ("..." if len(text) > 240 else ""),
                "runtime": "node-in-process",
            },
        )
        handle.publish(
            "run.started",
            {
                "run_id": run_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "created_at": _iso_now(),
            },
        )
        handle.publish(
            "message.created",
            {
                "run_id": run_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "message_id": handle.assistant_message_id,
                "message": _message_payload(
                    message_id=handle.assistant_message_id,
                    session_id=session_id,
                    run_id=run_id,
                    role="assistant",
                    parts=[],
                    status="streaming",
                ),
            },
        )
        try:
            session_metadata = self._session_metadata.get(session_id)
            run_snapshot = self._runtime_supervisor.start_run(
                run_id=run_id,
                agent_id=agent_id,
                session_id=session_id,
                user_id=requester.principal_id,
                text=text,
                run_override=(
                    session_metadata.model_profile_id
                    if session_metadata is not None
                    else None
                ),
                artifact_parts=resolved_artifact_parts,
                on_event=lambda event: self._publish_adk_run_event(handle, event),
                on_text_update=lambda merged, _delta: self._publish_run_text(handle, merged),
                on_complete=lambda final_text: self._complete_node_run(handle, final_text),
                on_error=lambda error: self._fail_node_run(handle, error),
                on_cancelled=lambda: self._cancel_node_run(handle),
            )
            self._record_goal_fact(
                "record_run_fact",
                session_id=session_id,
                run_id=run_id,
                status="running",
                correlation_id=run_id,
                snapshot={
                    "snapshotRevision": run_snapshot.snapshot_revision,
                    "modelProfileId": run_snapshot.model_profile_id,
                    "modelProfileRevision": run_snapshot.model_profile_revision,
                    "provider": run_snapshot.provider,
                    "model": run_snapshot.model,
                },
            )
        except Exception as exc:
            self._record_goal_fact(
                "record_run_fact",
                session_id=session_id,
                run_id=run_id,
                status="failed",
                correlation_id=run_id,
                snapshot={"startFailure": type(exc).__name__},
            )
            self._record_goal_fact(
                "block_current_goal",
                session_id=session_id,
                reason=f"The initial ADK Run could not start: {type(exc).__name__}",
                correlation_id=run_id,
            )
            with self._lock:
                self._runs.pop(run_id, None)
            return _error("RUNTIME_UNAVAILABLE", str(exc))
        return _ok(
            {
                "run": {
                    "id": run_id,
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "status": "running",
                    "events_url": f"/api/v1/runs/{run_id}/events",
                }
            }
        )

    def search_memory(self, agent_id: str, query: str, *, user_id: str = "ppx-client-user") -> dict[str, Any]:
        """Run one explicit memory query through the access-controlled query layer."""
        requester = self._ensure_requester_principal(user_id)
        if self._ensure_agent_access_state(agent_id) is None:
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        try:
            result = asyncio.run(
                self._memory_query_service.search(
                    agent_id=agent_id,
                    requester_principal_id=requester.principal_id,
                    query=query,
                )
            )
        except Exception as exc:
            return _error("RUNTIME_UNAVAILABLE", str(exc))
        if not result.decision.allow:
            return _error(
                "ACCESS_DENIED",
                f"Principal '{requester.principal_id}' cannot query memory for agent '{agent_id}'.",
                {"reason": result.decision.reason},
            )
        return _ok(
            {
                "items": [
                    {
                        "id": memory.id,
                        "author": memory.author,
                        "timestamp": memory.timestamp,
                        "text": memory_entry_text(memory),
                        "subject_principal_id": memory.custom_metadata.get("subject_principal_id"),
                        "metadata": dict(memory.custom_metadata),
                    }
                    for memory in result.memories
                ]
            }
        )

    def get_memory_audit(
        self,
        agent_id: str,
        *,
        user_id: str = "ppx-client-user",
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return visible explicit-memory audit rows for one requester and agent."""
        requester = self._ensure_requester_principal(user_id)
        if self._ensure_agent_access_state(agent_id) is None:
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        try:
            result = self._memory_query_service.list_audit(
                agent_id=agent_id,
                requester_principal_id=requester.principal_id,
                limit=limit,
            )
        except Exception as exc:
            return _error("RUNTIME_UNAVAILABLE", str(exc))
        if not result.decision.allow:
            self._record_admin_audit(
                agent_id=agent_id,
                requester=requester,
                action="read_memory_audit",
                relation_to_agent=result.decision.relation_to_agent,
                details={"allowed": False, "reason": result.decision.reason, "limit": limit},
            )
            return _error(
                "ACCESS_DENIED",
                f"Principal '{requester.principal_id}' cannot read memory audit for agent '{agent_id}'.",
                {"reason": result.decision.reason},
            )
        payload = _ok(
            {
                "items": result.rows,
                "requester": {
                    "principal_id": requester.principal_id,
                    "relation": result.decision.relation_to_agent,
                    "reason": result.decision.reason,
                    "scope_kind": result.decision.scope_kind,
                },
            }
        )
        self._record_admin_audit(
            agent_id=agent_id,
            requester=requester,
            action="read_memory_audit",
            relation_to_agent=result.decision.relation_to_agent,
            details={
                "allowed": True,
                "reason": result.decision.reason,
                "limit": limit,
                "result_count": len(result.rows),
            },
        )
        return payload

    def get_access_audit(
        self,
        agent_id: str,
        *,
        user_id: str = "ppx-client-user",
        limit: int = 50,
        category: str = "all",
    ) -> dict[str, Any]:
        """Return visible admin-audit rows for one requester and agent."""
        requester = self._ensure_requester_principal(user_id)
        if self._ensure_agent_access_state(agent_id) is None:
            return _error("AGENT_NOT_FOUND", f"Agent '{agent_id}' was not found.")
        try:
            normalized_category = _normalize_access_audit_category(category)
        except ValueError as exc:
            return _error("INVALID_REQUEST", str(exc))
        decision = self._access_policy.decide_agent_management(
            requester_principal_id=requester.principal_id,
            agent_id=agent_id,
            access_kind="access_audit_read",
        )
        if not decision.allow:
            self._record_admin_audit(
                agent_id=agent_id,
                requester=requester,
                action="read_admin_audit",
                relation_to_agent=decision.relation_to_agent,
                details={
                    "allowed": False,
                    "reason": decision.reason,
                    "limit": limit,
                    "category": normalized_category,
                },
            )
            return _error(
                "ACCESS_DENIED",
                f"Principal '{requester.principal_id}' cannot read access audit for agent '{agent_id}'.",
                {"reason": decision.reason},
            )
        rows = self._agent_access_store.list_audit(
            agent_id=agent_id,
            limit=limit,
            actions=_actions_for_access_audit_category(normalized_category),
        )
        payload = _ok(
            {
                "items": [
                    {
                        "audit_id": row.audit_id,
                        "agent_id": row.agent_id,
                        "actor_principal_id": row.actor_principal_id,
                        "actor_relation": row.actor_relation,
                        "action": row.action,
                        "target_principal_id": row.target_principal_id,
                        "details": dict(row.details),
                        "created_at_ms": row.created_at_ms,
                    }
                    for row in rows
                ],
                "requester": {
                    "principal_id": requester.principal_id,
                    "relation": decision.relation_to_agent,
                    "reason": decision.reason,
                    "scope_kind": decision.scope_kind,
                },
                "category": normalized_category,
            }
        )
        self._record_admin_audit(
            agent_id=agent_id,
            requester=requester,
            action="read_admin_audit",
            relation_to_agent=decision.relation_to_agent,
            details={
                "allowed": True,
                "reason": decision.reason,
                "limit": limit,
                "category": normalized_category,
                "result_count": len(rows),
            },
        )
        return payload

    def _publish_adk_run_event(self, handle: RunHandle, event: Any) -> None:
        """Project one raw ADK event into transport-stable tool-step events."""
        payload = event.model_dump(mode="json")
        handle.observe_invocation(str(payload.get("invocation_id") or ""))
        actions = payload.get("actions") if isinstance(payload.get("actions"), dict) else {}
        artifact_delta = actions.get("artifact_delta")
        if isinstance(artifact_delta, dict):
            for artifact_ref, raw_version in artifact_delta.items():
                version = raw_version if isinstance(raw_version, int) else None
                self._record_goal_fact(
                    "record_artifact_fact",
                    session_id=handle.session_id,
                    run_id=handle.run_id,
                    artifact_ref=str(artifact_ref),
                    version=version,
                    correlation_id=handle.run_id,
                )
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        raw_parts = content.get("parts") if isinstance(content.get("parts"), list) else []
        has_function_call = any(
            isinstance(raw_part, dict) and isinstance(raw_part.get("function_call"), dict)
            for raw_part in raw_parts
        )
        raw_long_running_ids = payload.get("long_running_tool_ids") or []
        long_running_ids = {str(item) for item in raw_long_running_ids if item is not None}
        for raw_part in raw_parts:
            if not isinstance(raw_part, dict):
                continue
            if not bool(raw_part.get("thought")) and has_function_call:
                text = raw_part.get("text")
                if isinstance(text, str):
                    normalized_text = _strip_request_time_prefix(text).strip()
                    if normalized_text:
                        handle.publish(
                            "message.delta",
                            {
                                "run_id": handle.run_id,
                                "agent_id": handle.agent_id,
                                "session_id": handle.session_id,
                                "message_id": handle.assistant_message_id,
                                "status": "streaming",
                                "part": {"type": "commentary", "text": normalized_text},
                            },
                        )
            function_call = raw_part.get("function_call")
            if isinstance(function_call, dict):
                step_id = str(function_call.get("id") or "step")
                detail = _preview_value(function_call.get("args"), "No tool arguments")
                if step_id in long_running_ids:
                    detail = "Background task is running.\n\n" + detail
                handle.publish(
                    "step.updated",
                    {
                        "run_id": handle.run_id,
                        "agent_id": handle.agent_id,
                        "session_id": handle.session_id,
                        "message_id": handle.assistant_message_id,
                        "step": _step_ref_payload(
                            step_id=step_id,
                            title=str(function_call.get("name") or "tool"),
                            status="running",
                            detail=detail,
                        ),
                    },
                )
            function_response = raw_part.get("function_response")
            if isinstance(function_response, dict):
                handle.publish(
                    "step.updated",
                    {
                        "run_id": handle.run_id,
                        "agent_id": handle.agent_id,
                        "session_id": handle.session_id,
                        "message_id": handle.assistant_message_id,
                        "step": _step_ref_payload(
                            step_id=str(function_response.get("id") or "step"),
                            title=str(function_response.get("name") or "tool"),
                            status="completed",
                            detail=_preview_value(
                                function_response.get("response"),
                                "Tool returned without a payload",
                            ),
                        ),
                    },
                )

    def _publish_run_text(self, handle: RunHandle, merged: str) -> None:
        """Publish one merged assistant-text snapshot for SSE consumers."""
        handle.publish(
            "message.delta",
            {
                "run_id": handle.run_id,
                "agent_id": handle.agent_id,
                "session_id": handle.session_id,
                "message_id": handle.assistant_message_id,
                "status": "streaming",
                "part": {"type": "markdown", "text": merged},
            },
        )

    def _complete_node_run(self, handle: RunHandle, final_text: str) -> None:
        """Publish one successful terminal state from the Node Runtime."""
        if not final_text.strip():
            self._fail_node_run(handle, RuntimeError("Run finished without returning a final reply."))
            return
        handle.publish(
            "message.completed",
            {
                "run_id": handle.run_id,
                "agent_id": handle.agent_id,
                "session_id": handle.session_id,
                "message_id": handle.assistant_message_id,
                "status": "completed",
                "message": _message_payload(
                    message_id=handle.assistant_message_id,
                    session_id=handle.session_id,
                    run_id=handle.run_id,
                    role="assistant",
                    parts=[{"type": "markdown", "text": final_text}],
                    status="completed",
                ),
            },
        )
        self._finish_node_run(handle, status="completed")

    def _fail_node_run(self, handle: RunHandle, error: BaseException) -> None:
        """Publish one redacted failed terminal state from the Node Runtime."""
        handle.failed = True
        message = str(error).strip() or "The Run failed."
        handle.publish(
            "message.failed",
            {
                "run_id": handle.run_id,
                "agent_id": handle.agent_id,
                "session_id": handle.session_id,
                "message_id": handle.assistant_message_id,
                "status": "failed",
                "error": _error_part_payload(code="RUN_FAILED", text=message),
            },
        )
        handle.publish(
            "error",
            {"run_id": handle.run_id, "code": "RUN_FAILED", "message": message},
        )
        self._record_goal_fact(
            "block_current_goal",
            session_id=handle.session_id,
            reason=f"The current ADK Run failed: {message}"[:2_000],
            correlation_id=handle.run_id,
        )
        self._finish_node_run(handle, status="failed")

    def _cancel_node_run(self, handle: RunHandle) -> None:
        """Publish one cooperative cancellation terminal state."""
        self._record_goal_fact(
            "pause_current_goal",
            session_id=handle.session_id,
            reason="The current ADK Run was stopped before the Goal completed.",
            correlation_id=handle.run_id,
        )
        handle.publish(
            "message.cancelled",
            {
                "run_id": handle.run_id,
                "agent_id": handle.agent_id,
                "session_id": handle.session_id,
                "message_id": handle.assistant_message_id,
                "status": "cancelled",
            },
        )
        handle.publish(
            "run.cancelled",
            {
                "run_id": handle.run_id,
                "agent_id": handle.agent_id,
                "session_id": handle.session_id,
                "message_id": handle.assistant_message_id,
                "status": "cancelled",
            },
        )
        self._finish_node_run(handle, status="cancelled")

    def _finish_node_run(self, handle: RunHandle, *, status: str) -> None:
        """Close one Run stream after publishing its common terminal event."""
        self._record_goal_fact(
            "record_run_fact",
            session_id=handle.session_id,
            run_id=handle.run_id,
            status=status,
            correlation_id=handle.run_id,
            invocation_id=handle.invocation_id,
        )
        handle.publish(
            "run.finished",
            {
                "run_id": handle.run_id,
                "agent_id": handle.agent_id,
                "session_id": handle.session_id,
                "message_id": handle.assistant_message_id,
                "status": status,
            },
        )
        handle.finish()

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        """Cancel one active Run through the shared Control Plane Action."""

        handle = self._runs.get(run_id)
        if handle is None:
            return _error("RUN_NOT_FOUND", f"Run '{run_id}' was not found.")
        outcome = self._invoke_control("run.stop", {"runId": run_id})
        if not outcome.ok:
            error = outcome.error
            return _error(
                str(error.code if error is not None else "run_not_active").upper(),
                error.message if error is not None else "The Run is not active.",
                error.details if error is not None else None,
            )
        _debug("client_api.cancel_run", {"run_id": run_id})
        return _ok({"run": {"id": run_id, "status": "cancelled"}})

    def stream_run_events(self, run_id: str, *, last_event_id: str | None = None) -> queue.Queue[RunEnvelope | None] | None:
        """Return one subscriber queue for SSE streaming."""

        handle = self._runs.get(run_id)
        if handle is None:
            return None
        return handle.subscribe(last_event_id=last_event_id)


class _ClientApiHandler(BaseHTTPRequestHandler):
    """HTTP request handler bound to one coordinator instance."""

    server_version = "OpenPpxClientApi/1"

    @property
    def coordinator(self) -> ClientApiCoordinator:
        return self.server.coordinator  # type: ignore[attr-defined]

    @property
    def auth_policy(self) -> ClientApiAuthPolicy:
        return self.server.auth_policy  # type: ignore[attr-defined]

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, data: bytes, *, mime_type: str) -> None:
        """Send one authorized binary artifact without exposing its storage path."""
        self.send_response(status)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, status: int, body: str) -> None:
        """Send one small no-store OAuth completion page."""
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _require_authorization(self) -> bool:
        """Authorize one protected request or emit a standard 401 response."""

        if self.auth_policy.authorizes(self.headers.get("Authorization")):
            return True
        _debug(
            "client_api.auth_failed",
            {
                "remote_address": str(self.client_address[0]),
                "path": urllib.parse.urlparse(self.path).path,
                "timestamp": _iso_now(),
            },
        )
        self._send_json(
            401,
            _error("UNAUTHORIZED", "A valid Client API bearer token is required."),
            extra_headers={"WWW-Authenticate": 'Bearer realm="openppx-client-api"'},
        )
        return False

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        if length > _MAX_JSON_BODY_BYTES:
            raise ValueError("Request body exceeds the 30 MB limit.")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}

    def _parse(self) -> tuple[str, list[str], dict[str, str]]:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"
        segments = [segment for segment in path.split("/") if segment]
        query = {key: values[-1] for key, values in urllib.parse.parse_qs(parsed.query).items() if values}
        return path, segments, query

    def do_GET(self) -> None:  # noqa: N802
        path, segments, query = self._parse()
        if path == CALLBACK_PATH:
            code = str(query.get("code") or "")
            state = str(query.get("state") or "") or None
            error = str(query.get("error") or "")
            accepted = bool((code or error) and self.coordinator.deliver_mcp_oauth_callback(
                code=code,
                state=state,
                error=error,
            ))
            if accepted:
                self._send_html(
                    200,
                    "<!doctype html><meta charset='utf-8'><title>OpenPPX connected</title>"
                    "<main><h1>Connected to OpenPPX</h1><p>You can close this window and return to the Desktop app.</p></main>",
                )
            else:
                self._send_html(
                    400,
                    "<!doctype html><meta charset='utf-8'><title>OpenPPX sign-in expired</title>"
                    "<main><h1>Sign-in could not be completed</h1><p>Return to OpenPPX and start Connect again.</p></main>",
                )
            return
        if path == "/api/v1/health":
            authenticated = self.auth_policy.authorizes(self.headers.get("Authorization"))
            self._send_json(200, self.coordinator.health(public=self.auth_policy.required and not authenticated))
            return
        if not self._require_authorization():
            return
        if path == "/api/v1/node":
            self._send_json(200, self.coordinator.node_info(authentication_required=self.auth_policy.required))
            return
        if path == "/api/v1/agents":
            self._send_json(200, self.coordinator.list_agents())
            return
        if path == "/api/v1/runtime/status":
            self._send_json(200, self.coordinator.runtime_status())
            return
        if path == "/api/v1/actions":
            self._send_json(
                200,
                self.coordinator.action_catalog(
                    namespace=query.get("namespace"),
                    projection=query.get("projection"),
                    request_id=self.headers.get("X-Request-ID"),
                    correlation_id=self.headers.get("X-Correlation-ID"),
                ),
            )
            return
        if len(segments) == 7 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "sessions" and segments[6] == "artifacts":
            user_id = str(query.get("user_id") or "ppx-client-user")
            payload = self.coordinator.list_artifacts(segments[3], segments[5], user_id=user_id)
            self._send_json(200 if payload.get("ok") else 404, payload)
            return
        if len(segments) == 7 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "sessions" and segments[6] == "artifact-content":
            user_id = str(query.get("user_id") or "ppx-client-user")
            key = str(query.get("key") or "")
            try:
                version = int(query["version"]) if "version" in query else None
            except ValueError:
                self._send_json(400, _error("INVALID_REQUEST", "Artifact version must be an integer."))
                return
            payload, data = self.coordinator.load_artifact(
                segments[3],
                segments[5],
                key=key,
                version=version,
                user_id=user_id,
            )
            if data is None:
                self._send_json(404, payload)
                return
            self._send_bytes(200, data, mime_type=str(payload["data"]["mime_type"]))
            return
        if len(segments) == 5 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "sessions":
            user_id = str(query.get("user_id") or "ppx-client-user")
            payload = self.coordinator.list_sessions(segments[3], user_id=user_id)
            self._send_json(200 if payload.get("ok") else 404, payload)
            return
        if len(segments) == 5 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "access":
            user_id = str(query.get("user_id") or "ppx-client-user")
            payload = self.coordinator.get_agent_access(segments[3], user_id=user_id)
            self._send_json(200 if payload.get("ok") else 404, payload)
            return
        if len(segments) == 6 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "access" and segments[5] == "audit":
            user_id = str(query.get("user_id") or "ppx-client-user")
            raw_limit = str(query.get("limit") or "50").strip()
            category = str(query.get("category") or "all")
            try:
                limit = int(raw_limit)
            except ValueError:
                self._send_json(400, _error("INVALID_REQUEST", "Query parameter 'limit' must be an integer."))
                return
            payload = self.coordinator.get_access_audit(
                segments[3],
                user_id=user_id,
                limit=limit,
                category=category,
            )
            status = 200 if payload.get("ok") else 403
            if not payload.get("ok") and payload.get("error", {}).get("code") == "AGENT_NOT_FOUND":
                status = 404
            if not payload.get("ok") and payload.get("error", {}).get("code") == "INVALID_REQUEST":
                status = 400
            self._send_json(status, payload)
            return
        if len(segments) == 5 and segments[:3] == ["api", "v1", "sessions"] and segments[4] == "messages":
            user_id = str(query.get("user_id") or "ppx-client-user")
            payload = self.coordinator.get_session_messages(segments[3], user_id=user_id)
            self._send_json(200 if payload.get("ok") else 404, payload)
            return
        if len(segments) == 6 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "memory" and segments[5] == "search":
            query_text = str(query.get("q") or "").strip()
            user_id = str(query.get("user_id") or "ppx-client-user")
            if not query_text:
                self._send_json(400, _error("INVALID_REQUEST", "Query parameter 'q' is required."))
                return
            payload = self.coordinator.search_memory(segments[3], query_text, user_id=user_id)
            self._send_json(200 if payload.get("ok") else 404, payload)
            return
        if len(segments) == 6 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "memory" and segments[5] == "audit":
            user_id = str(query.get("user_id") or "ppx-client-user")
            raw_limit = str(query.get("limit") or "50").strip()
            try:
                limit = int(raw_limit)
            except ValueError:
                self._send_json(400, _error("INVALID_REQUEST", "Query parameter 'limit' must be an integer."))
                return
            payload = self.coordinator.get_memory_audit(segments[3], user_id=user_id, limit=limit)
            self._send_json(200 if payload.get("ok") else 404, payload)
            return
        if len(segments) == 5 and segments[:3] == ["api", "v1", "runs"] and segments[4] == "events":
            run_id = segments[3]
            subscriber = self.coordinator.stream_run_events(run_id, last_event_id=self.headers.get("Last-Event-ID"))
            if subscriber is None:
                self._send_json(404, _error("RUN_NOT_FOUND", f"Run '{run_id}' was not found."))
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            while True:
                item = subscriber.get()
                if item is None:
                    break
                self.wfile.write(f"id: {item.event_id}\n".encode("utf-8"))
                self.wfile.write(f"event: {item.event}\n".encode("utf-8"))
                self.wfile.write(f"data: {json.dumps(item.payload, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
            return
        self._send_json(404, _error("NOT_FOUND", f"Unknown path: {path}"))

    def do_POST(self) -> None:  # noqa: N802
        path, segments, _query = self._parse()
        if not self._require_authorization():
            return
        try:
            body = self._read_json_body()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(413 if "exceeds" in str(exc) else 400, _error("INVALID_REQUEST", str(exc)))
            return
        if path == "/api/v1/actions/invoke":
            try:
                request = ActionInvokeRequest.model_validate(body, strict=True)
            except ValidationError:
                request_id = _wire_id(body.get("requestId"), prefix="req")
                correlation_id = _wire_id(body.get("correlationId") or request_id, prefix="corr")
                failure = ActionOutcome.failure(
                    "system.transport",
                    ActionError("invalid_request", "The Action request does not match its schema."),
                )
                envelope = ContractMapper().from_outcome(
                    failure,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
                self._send_json(400, envelope.model_dump(mode="json", by_alias=True))
                return
            payload = self.coordinator.invoke_action(
                request.action_id,
                request.input,
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                confirmed=request.confirmed,
            )
            error_code = str(payload.get("error", {}).get("code") or "") if not payload.get("ok") else ""
            status = {
                "action_not_found": 404,
                "capability_required": 403,
                "permission_denied": 403,
                "confirmation_required": 409,
                "revision_conflict": 409,
                "resource_not_found": 404,
                "invalid_action_input": 400,
                "invalid_request": 400,
                "extension_not_found": 404,
                "invalid_extension_kind": 400,
                "invalid_manifest": 400,
                "invalid_source": 400,
                "source_changed": 409,
                "extension_conflict": 409,
                "extension_in_use": 409,
                "dependency_missing": 409,
            }.get(error_code, 200 if payload.get("ok") else 500)
            self._send_json(status, payload)
            return
        if len(segments) == 6 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "access" and segments[5] == "owner":
            user_id = str(body.get("user_id") or "ppx-client-user")
            owner_principal_id = str(body.get("owner_principal_id") or "").strip()
            if not owner_principal_id:
                self._send_json(400, _error("INVALID_REQUEST", "Field 'owner_principal_id' is required."))
                return
            payload = self.coordinator.set_agent_owner(segments[3], owner_principal_id, user_id=user_id)
            self._send_json(200 if payload.get("ok") else 403, payload)
            return
        if len(segments) == 6 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "access" and segments[5] == "memberships":
            user_id = str(body.get("user_id") or "ppx-client-user")
            principal_id = str(body.get("principal_id") or "").strip()
            relation = str(body.get("relation") or "participant").strip()
            if not principal_id:
                self._send_json(400, _error("INVALID_REQUEST", "Field 'principal_id' is required."))
                return
            payload = self.coordinator.upsert_agent_membership(
                segments[3],
                principal_id,
                relation=relation,
                user_id=user_id,
            )
            self._send_json(200 if payload.get("ok") else 403, payload)
            return
        if len(segments) == 7 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "access" and segments[5] == "memberships" and segments[6] == "batch":
            user_id = str(body.get("user_id") or "ppx-client-user")
            operation = str(body.get("operation") or "").strip().lower()
            dry_run = bool(body.get("dry_run"))
            raw_principal_ids = body.get("principal_ids")
            if not isinstance(raw_principal_ids, list):
                self._send_json(400, _error("INVALID_REQUEST", "Field 'principal_ids' must be a JSON array."))
                return
            principal_ids = [str(item or "") for item in raw_principal_ids]
            if operation == "add":
                payload = self.coordinator.batch_add_participants(
                    segments[3],
                    principal_ids,
                    user_id=user_id,
                    dry_run=dry_run,
                )
            elif operation == "remove":
                payload = self.coordinator.batch_remove_participants(
                    segments[3],
                    principal_ids,
                    user_id=user_id,
                    dry_run=dry_run,
                )
            elif operation == "sync":
                payload = self.coordinator.sync_participants(
                    segments[3],
                    principal_ids,
                    user_id=user_id,
                    dry_run=dry_run,
                )
            else:
                self._send_json(400, _error("INVALID_REQUEST", "Field 'operation' must be add, remove, or sync."))
                return
            status = 200 if payload.get("ok") else 403
            if not payload.get("ok") and payload.get("error", {}).get("code") == "AGENT_NOT_FOUND":
                status = 404
            if not payload.get("ok") and payload.get("error", {}).get("code") == "INVALID_REQUEST":
                status = 400
            self._send_json(status, payload)
            return
        if len(segments) == 5 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "sessions":
            user_id = str(body.get("user_id") or "ppx-client-user")
            payload = self.coordinator.create_session(segments[3], user_id=user_id)
            self._send_json(200 if payload.get("ok") else 404, payload)
            return
        if len(segments) == 7 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "sessions" and segments[6] == "artifacts":
            user_id = str(body.get("user_id") or "ppx-client-user")
            payload = self.coordinator.upload_artifact(
                segments[3],
                segments[5],
                file_name=body.get("file_name"),
                mime_type=body.get("mime_type"),
                data_base64=body.get("data_base64"),
                user_id=user_id,
            )
            error_code = str(payload.get("error", {}).get("code") or "")
            status = 200 if payload.get("ok") else (400 if error_code == "INVALID_ARTIFACT" else 404)
            self._send_json(status, payload)
            return
        if len(segments) == 7 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "sessions" and segments[6] == "runs":
            text = str(body.get("text") or "").strip()
            user_id = str(body.get("user_id") or "ppx-client-user")
            raw_artifact_refs = body.get("artifact_refs") or []
            if not isinstance(raw_artifact_refs, list) or not all(isinstance(item, dict) for item in raw_artifact_refs):
                self._send_json(400, _error("INVALID_REQUEST", "Field 'artifact_refs' must be a JSON object array."))
                return
            if not text and not raw_artifact_refs:
                self._send_json(400, _error("INVALID_REQUEST", "A message requires text or an artifact reference."))
                return
            payload = self.coordinator.create_run(
                segments[3],
                segments[5],
                text or "Use the attached files to complete the task.",
                artifact_refs=raw_artifact_refs,
                user_id=user_id,
            )
            self._send_json(200 if payload.get("ok") else 404, payload)
            return
        if len(segments) == 5 and segments[:3] == ["api", "v1", "runs"] and segments[4] == "cancel":
            payload = self.coordinator.cancel_run(segments[3])
            self._send_json(200 if payload.get("ok") else 404, payload)
            return
        self._send_json(404, _error("NOT_FOUND", f"Unknown path: {path}"))

    def do_DELETE(self) -> None:  # noqa: N802
        path, segments, query = self._parse()
        if not self._require_authorization():
            return
        if len(segments) == 7 and segments[:3] == ["api", "v1", "agents"] and segments[4] == "access" and segments[5] == "memberships":
            user_id = str(query.get("user_id") or "ppx-client-user")
            payload = self.coordinator.delete_agent_membership(
                segments[3],
                segments[6],
                user_id=user_id,
            )
            self._send_json(200 if payload.get("ok") else 403, payload)
            return
        self._send_json(404, _error("NOT_FOUND", f"Unknown path: {path}"))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        """Silence default stdlib access logs for cleaner CLI output."""


class ClientApiHttpServer(ThreadingHTTPServer):
    """Threading HTTP server bound to one `ClientApiCoordinator`."""

    def __init__(
        self,
        server_address: tuple[str, int],
        coordinator: ClientApiCoordinator,
        *,
        access_token: str = "",
    ) -> None:
        super().__init__(server_address, _ClientApiHandler)
        self.coordinator = coordinator
        self.auth_policy = ClientApiAuthPolicy(access_token=access_token)
