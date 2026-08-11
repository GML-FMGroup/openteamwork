"""Authorized, bounded historical Session reads over the authoritative ADK store."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .adk_identity import adk_app_name_for_agent_id
from .agent_access_store import AgentAccessStore, AgentRecord
from .history_access import HistoryAccessDecision, HistoryAccessPolicy, HistoryAgentResolver
from .identity_store import IdentityStore
from .session_history import project_searchable_history
from .session_metadata_store import SessionMetadataStore


_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50
_TERMINAL_HISTORY_GUIDANCE = (
    "A denial is terminal for this target. Do not retry through shell, files, "
    "Skills, APIs, memory, or alternate tools. Explain the permission limit or "
    "ask the user to use an Agent with sufficient privilege."
)


def _error(
    code: str,
    message: str,
    *,
    reason: str = "",
    terminal: bool = False,
    guidance: str = "",
) -> dict[str, Any]:
    """Return a stable tool-facing error envelope."""
    error: dict[str, Any] = {"code": code, "message": message, "reason": reason}
    if terminal:
        error["terminal"] = True
        error["guidance"] = guidance or _TERMINAL_HISTORY_GUIDANCE
    return {"ok": False, "error": error}


def _parse_time(value: str | None) -> datetime | None:
    """Parse an optional ISO 8601 boundary and normalize it to UTC."""
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _time_in_range(timestamp: str | None, start: datetime | None, end: datetime | None) -> bool:
    """Return whether one projected message timestamp falls inside the range."""
    if timestamp is None:
        return start is None and end is None
    parsed = _parse_time(timestamp)
    assert parsed is not None
    return (start is None or parsed >= start) and (end is None or parsed < end)


def _fingerprint(payload: dict[str, Any]) -> str:
    """Return a stable cursor fingerprint for one immutable query shape."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _encode_cursor(*, offset: int, fingerprint: str) -> str:
    """Encode an opaque, non-authoritative pagination cursor."""
    raw = json.dumps({"offset": offset, "fingerprint": fingerprint}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None, *, fingerprint: str) -> int:
    """Decode and bind one cursor to the current authorized query shape."""
    if not cursor:
        return 0
    try:
        padded = str(cursor) + "=" * (-len(str(cursor)) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        offset = int(payload["offset"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid_cursor") from exc
    if payload.get("fingerprint") != fingerprint or offset < 0:
        raise ValueError("invalid_cursor")
    return offset


def _page(
    items: list[dict[str, Any]],
    *,
    limit: int,
    cursor: str | None,
    fingerprint: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Return one deterministic page and its next cursor."""
    offset = _decode_cursor(cursor, fingerprint=fingerprint)
    page = items[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = (
        _encode_cursor(offset=next_offset, fingerprint=fingerprint)
        if next_offset < len(items)
        else None
    )
    return page, next_cursor


class HistoricalSessionService:
    """Read and search retained Sessions after a trusted hierarchical ACL check."""

    def __init__(
        self,
        *,
        session_service: Any,
        identity_store: IdentityStore,
        agent_access_store: AgentAccessStore,
        session_metadata: SessionMetadataStore,
        catalog_refresher: Callable[[], None] | None = None,
    ) -> None:
        self._session_service = session_service
        self._identity_store = identity_store
        self._agent_access_store = agent_access_store
        self._session_metadata = session_metadata
        self._catalog_refresher = catalog_refresher
        self._policy = HistoryAccessPolicy(
            identity_store=identity_store,
            agent_access_store=agent_access_store,
        )
        self._resolver = HistoryAgentResolver(
            identity_store=identity_store,
            agent_access_store=agent_access_store,
            policy=self._policy,
        )

    def resolve_agent(
        self,
        *,
        source_user_id: str,
        source_agent_id: str,
        display_name: str,
        source_agent_privilege_level: str | None = None,
    ) -> dict[str, Any]:
        """Resolve one display name inside the caller's authorized Agent scope."""
        refresh_error = self._refresh_catalog()
        if refresh_error is not None:
            return refresh_error
        result = self._resolver.resolve(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            display_name=display_name,
            source_agent_privilege_level=source_agent_privilege_level,
        )
        display_name_fingerprint = _fingerprint(
            {"displayName": str(display_name or "").strip().casefold()}
        )
        for denied in result.denied_matches:
            target = self._agent_access_store.get_agent_record(denied.agent_id)
            if target is None:
                continue
            self._audit(
                source_user_id=source_user_id,
                source_agent_id=source_agent_id,
                target=target,
                action="history.resolve",
                decision=HistoryAccessDecision(False, denied.reason),
                query={"displayNameFingerprint": display_name_fingerprint},
                citations=[],
            )
        response = {
            "ok": result.status in {"resolved", "ambiguous"},
            "status": result.status,
            "agentId": result.agent_id,
            "ownerPrincipalId": result.owner_principal_id,
            "candidates": [
                {
                    "agentId": candidate.agent_id,
                    "displayName": candidate.display_name,
                    "ownerPrincipalId": candidate.owner_principal_id,
                    "ownerDisplayName": candidate.owner_display_name,
                    "privilegeLevel": candidate.privilege_level,
                    "shortId": candidate.short_id,
                }
                for candidate in result.candidates
            ],
        }
        if result.status == "not_found":
            response["terminal"] = True
            response["guidance"] = _TERMINAL_HISTORY_GUIDANCE
        return response

    async def list_sessions(
        self,
        *,
        source_user_id: str,
        source_agent_id: str,
        target_agent_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        source_agent_privilege_level: str | None = None,
    ) -> dict[str, Any]:
        """List retained target Sessions with bounded cursor-based pagination."""
        authorized = self._authorize(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            action="history.list_sessions",
            source_agent_privilege_level=source_agent_privilege_level,
        )
        if isinstance(authorized, dict):
            return authorized
        target, decision = authorized
        try:
            start, end = self._validated_range(start_time, end_time)
            bounded_limit = self._validated_limit(limit)
            rows = await self._session_rows(target, start=start, end=end)
            fingerprint = _fingerprint(
                {"op": "list", "target": target.agent_id, "start": start_time, "end": end_time}
            )
            page, next_cursor = _page(
                rows,
                limit=bounded_limit,
                cursor=cursor,
                fingerprint=fingerprint,
            )
        except ValueError as exc:
            return _error("invalid_request", str(exc), reason=str(exc))
        if not self._audit(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            target=target,
            action="history.list_sessions",
            decision=decision,
            query={"startTime": start_time, "endTime": end_time},
            citations=[item["citation"] for item in page],
        ):
            return self._audit_unavailable()
        return {"ok": True, "items": page, "nextCursor": next_cursor}

    async def search(
        self,
        *,
        source_user_id: str,
        source_agent_id: str,
        target_agent_id: str,
        query: str,
        match_mode: str = "and",
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        source_agent_privilege_level: str | None = None,
    ) -> dict[str, Any]:
        """Search message text and attachment markers using exact substrings."""
        authorized = self._authorize(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            action="history.search",
            source_agent_privilege_level=source_agent_privilege_level,
        )
        if isinstance(authorized, dict):
            return authorized
        target, decision = authorized
        normalized_query = " ".join(str(query or "").split())
        if not normalized_query or len(normalized_query) > 256:
            return _error("invalid_request", "query must contain between 1 and 256 characters")
        normalized_mode = str(match_mode or "and").strip().lower()
        if normalized_mode not in {"and", "or"}:
            return _error("invalid_request", "match_mode must be 'and' or 'or'")
        try:
            start, end = self._validated_range(start_time, end_time)
            bounded_limit = self._validated_limit(limit)
            rows = await self._search_rows(
                target,
                query=normalized_query,
                match_mode=normalized_mode,
                start=start,
                end=end,
            )
            fingerprint = _fingerprint(
                {
                    "op": "search",
                    "target": target.agent_id,
                    "query": normalized_query.casefold(),
                    "mode": normalized_mode,
                    "start": start_time,
                    "end": end_time,
                }
            )
            page, next_cursor = _page(
                rows,
                limit=bounded_limit,
                cursor=cursor,
                fingerprint=fingerprint,
            )
        except ValueError as exc:
            return _error("invalid_request", str(exc), reason=str(exc))
        if not self._audit(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            target=target,
            action="history.search",
            decision=decision,
            query={
                "query": normalized_query,
                "matchMode": normalized_mode,
                "startTime": start_time,
                "endTime": end_time,
            },
            citations=[item["citation"] for item in page],
        ):
            return self._audit_unavailable()
        return {"ok": True, "items": page, "nextCursor": next_cursor}

    async def read(
        self,
        *,
        source_user_id: str,
        source_agent_id: str,
        target_agent_id: str,
        session_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        source_agent_privilege_level: str | None = None,
    ) -> dict[str, Any]:
        """Read one retained Session as bounded text-only message excerpts."""
        authorized = self._authorize(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            action="history.read",
            source_agent_privilege_level=source_agent_privilege_level,
        )
        if isinstance(authorized, dict):
            return authorized
        target, decision = authorized
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return _error("invalid_request", "session_id is required")
        metadata = self._session_metadata.get(normalized_session_id)
        if metadata is not None and (
            metadata.agent_id != target.agent_id
            or metadata.principal_id != target.owner_principal_id
        ):
            return _error("session_not_found", "The requested Session was not found.")
        session = await self._session_service.get_session(
            app_name=adk_app_name_for_agent_id(target.agent_id),
            user_id=target.owner_principal_id,
            session_id=normalized_session_id,
        )
        if session is None:
            return _error("session_not_found", "The requested Session was not found.")
        try:
            start, end = self._validated_range(start_time, end_time)
            bounded_limit = self._validated_limit(limit)
            messages = [
                self._message_row(target, normalized_session_id, message, metadata=metadata)
                for message in project_searchable_history(session)
                if _time_in_range(message.get("timestamp"), start, end)
            ]
            fingerprint = _fingerprint(
                {
                    "op": "read",
                    "target": target.agent_id,
                    "session": normalized_session_id,
                    "start": start_time,
                    "end": end_time,
                }
            )
            page, next_cursor = _page(
                messages,
                limit=bounded_limit,
                cursor=cursor,
                fingerprint=fingerprint,
            )
        except ValueError as exc:
            return _error("invalid_request", str(exc), reason=str(exc))
        if not self._audit(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            target=target,
            action="history.read",
            decision=decision,
            query={"sessionId": normalized_session_id, "startTime": start_time, "endTime": end_time},
            citations=[item["citation"] for item in page],
        ):
            return self._audit_unavailable()
        return {"ok": True, "items": page, "nextCursor": next_cursor}

    def _authorize(
        self,
        *,
        source_user_id: str,
        source_agent_id: str,
        target_agent_id: str,
        action: str,
        source_agent_privilege_level: str | None,
    ) -> tuple[AgentRecord, HistoryAccessDecision] | dict[str, Any]:
        """Resolve the target owner and evaluate the trusted four-party scope."""
        refresh_error = self._refresh_catalog()
        if refresh_error is not None:
            return refresh_error
        target = self._agent_access_store.get_agent_record(str(target_agent_id or "").strip())
        if target is None:
            return _error(
                "agent_not_found",
                "The target Agent was not found.",
                terminal=True,
            )
        decision = self._policy.decide(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            target_user_id=target.owner_principal_id,
            target_agent_id=target.agent_id,
            source_agent_privilege_level=source_agent_privilege_level,
        )
        if decision.allow:
            return target, decision
        self._audit(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            target=target,
            action=action,
            decision=decision,
            query={},
            citations=[],
        )
        return _error(
            "access_denied",
            "The invoking Agent cannot access this history.",
            reason=decision.reason,
            terminal=True,
        )

    def _refresh_catalog(self) -> dict[str, Any] | None:
        """Refresh Config-derived Agent identity facts before every ACL decision."""
        if self._catalog_refresher is None:
            return None
        try:
            self._catalog_refresher()
        except Exception:
            return _error(
                "history_catalog_unavailable",
                "The Agent history catalog is temporarily unavailable.",
            )
        return None

    async def _sessions_for_target(self, target: AgentRecord) -> list[Any]:
        """Load authoritative retained ADK Sessions for one Agent owner scope."""
        response = await self._session_service.list_sessions(
            app_name=adk_app_name_for_agent_id(target.agent_id),
            user_id=target.owner_principal_id,
        )
        sessions: list[Any] = []
        for summary in list(response.sessions):
            detail = await self._session_service.get_session(
                app_name=adk_app_name_for_agent_id(target.agent_id),
                user_id=target.owner_principal_id,
                session_id=str(summary.id),
            )
            if detail is not None:
                sessions.append(detail)
        return sessions

    async def _session_rows(
        self,
        target: AgentRecord,
        *,
        start: datetime | None,
        end: datetime | None,
    ) -> list[dict[str, Any]]:
        """Project retained Session summaries whose messages intersect the range."""
        rows: list[dict[str, Any]] = []
        for session in await self._sessions_for_target(target):
            session_id = str(session.id)
            metadata = self._session_metadata.get(session_id)
            messages = project_searchable_history(session)
            ranged = [item for item in messages if _time_in_range(item.get("timestamp"), start, end)]
            if (start is not None or end is not None) and not ranged:
                continue
            title = metadata.title if metadata is not None and metadata.title else ""
            if not title:
                title = next((str(item["text"]) for item in messages if item["role"] == "user"), "")[:120]
            updated_at = datetime.fromtimestamp(
                float(getattr(session, "last_update_time", 0) or 0),
                tz=timezone.utc,
            ).isoformat()
            rows.append(
                {
                    "sessionId": session_id,
                    "title": title or f"Session {session_id[:8]}",
                    "state": self._state(metadata),
                    "updatedAt": updated_at,
                    "messageCount": len(ranged if start is not None or end is not None else messages),
                    "citation": self._citation(target, session_id),
                }
            )
        rows.sort(key=lambda item: (item["updatedAt"], item["sessionId"]), reverse=True)
        return rows

    async def _search_rows(
        self,
        target: AgentRecord,
        *,
        query: str,
        match_mode: str,
        start: datetime | None,
        end: datetime | None,
    ) -> list[dict[str, Any]]:
        """Return deterministic exact-substring hits over authorized message rows."""
        terms = [term.casefold() for term in query.split()]
        rows: list[dict[str, Any]] = []
        for session in await self._sessions_for_target(target):
            session_id = str(session.id)
            metadata = self._session_metadata.get(session_id)
            for message in project_searchable_history(session):
                if not _time_in_range(message.get("timestamp"), start, end):
                    continue
                folded = str(message["text"]).casefold()
                matched = all(term in folded for term in terms) if match_mode == "and" else any(
                    term in folded for term in terms
                )
                if not matched:
                    continue
                row = self._message_row(target, session_id, message, metadata=metadata)
                row["text"] = str(row["text"])[:1000]
                rows.append(row)
        rows.sort(
            key=lambda item: (
                str(item.get("timestamp") or ""),
                item["citation"]["sessionId"],
                item["citation"]["messageId"],
            ),
            reverse=True,
        )
        return rows

    @staticmethod
    def _validated_limit(limit: int) -> int:
        """Validate a caller-visible page size without silently widening it."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}")
        return limit

    @staticmethod
    def _validated_range(
        start_time: str | None,
        end_time: str | None,
    ) -> tuple[datetime | None, datetime | None]:
        """Validate one half-open timestamp range."""
        try:
            start = _parse_time(start_time)
            end = _parse_time(end_time)
        except ValueError as exc:
            raise ValueError("start_time and end_time must be ISO 8601 timestamps") from exc
        if start is not None and end is not None and start >= end:
            raise ValueError("start_time must be earlier than end_time")
        return start, end

    @staticmethod
    def _state(metadata: Any | None) -> str:
        """Project the retained Session lifecycle state."""
        if metadata is not None and metadata.removed:
            return "removed"
        if metadata is not None and metadata.archived:
            return "archived"
        return "active"

    def _message_row(
        self,
        target: AgentRecord,
        session_id: str,
        message: dict[str, object],
        *,
        metadata: Any | None,
    ) -> dict[str, Any]:
        """Attach Session lifecycle and a stable citation to one message."""
        return {
            "role": message["role"],
            "text": message["text"],
            "timestamp": message["timestamp"],
            "attachmentNames": message["attachmentNames"],
            "sessionState": self._state(metadata),
            "citation": self._citation(
                target,
                session_id,
                message_id=str(message["messageId"]),
            ),
        }

    @staticmethod
    def _citation(
        target: AgentRecord,
        session_id: str,
        *,
        message_id: str | None = None,
    ) -> dict[str, str]:
        """Build one stable historical reference without embedding content."""
        citation = {
            "agentId": target.agent_id,
            "ownerPrincipalId": target.owner_principal_id,
            "sessionId": session_id,
        }
        if message_id is not None:
            citation["messageId"] = message_id
        return citation

    def _audit(
        self,
        *,
        source_user_id: str,
        source_agent_id: str,
        target: AgentRecord,
        action: str,
        decision: HistoryAccessDecision,
        query: dict[str, Any],
        citations: list[dict[str, str]],
    ) -> bool:
        """Persist a cross-Agent decision and report whether durable audit succeeded."""
        if source_agent_id == target.agent_id:
            return True
        try:
            self._agent_access_store.record_audit(
                agent_id=target.agent_id,
                actor_principal_id=source_user_id,
                actor_relation="history_reader",
                action=action,
                target_principal_id=target.owner_principal_id,
                details={
                    "allowed": decision.allow,
                    "reason": decision.reason,
                    "sourceAgentId": source_agent_id,
                    "query": query,
                    "returnedCitations": citations[:_MAX_LIMIT],
                },
            )
        except Exception:
            return False
        return True

    @staticmethod
    def _audit_unavailable() -> dict[str, Any]:
        """Fail closed when a required cross-Agent audit record cannot be persisted."""
        return _error(
            "history_audit_unavailable",
            "Cross-Agent history is unavailable because its audit record could not be saved.",
        )


__all__ = ["HistoricalSessionService"]
