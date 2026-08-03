"""Thin HTTP client for the local openppx client API service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request


@dataclass(slots=True)
class ClientApiClient:
    """Call the local HTTP + SSE client API with stable typed helpers."""

    base_url: str = "http://127.0.0.1:8765"
    timeout_seconds: float = 10.0
    access_token: str = ""

    def _build_url(self, path: str, *, query: dict[str, Any] | None = None) -> str:
        """Build one request URL from a relative API path and query params."""
        normalized_path = "/" + str(path or "").lstrip("/")
        base = self.base_url.rstrip("/")
        if not query:
            return f"{base}{normalized_path}"
        encoded_query = parse.urlencode(
            {
                key: str(value)
                for key, value in query.items()
                if value is not None and str(value).strip()
            }
        )
        return f"{base}{normalized_path}?{encoded_query}" if encoded_query else f"{base}{normalized_path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one JSON request and return the parsed response envelope."""
        payload = None
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if json_body is not None:
            payload = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        req = request.Request(
            self._build_url(path, query=query),
            data=payload,
            headers=headers,
            method=method.upper(),
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read()
        except error.HTTPError as exc:
            # The Client API returns the same structured envelope for non-2xx
            # responses. Preserve it so CLI and GUI render identical failures.
            raw = exc.read()
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}

    def invoke_action(
        self,
        action_id: str,
        raw_input: dict[str, object] | None = None,
        *,
        confirmed: bool = False,
        request_id: str,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Invoke one product Action through the shared Client API contract."""
        resolved_correlation_id = correlation_id or request_id
        return self._request(
            "POST",
            "/api/v1/actions/invoke",
            json_body={
                "actionId": action_id,
                "input": raw_input or {},
                "confirmed": confirmed,
                "requestId": request_id,
                "correlationId": resolved_correlation_id,
            },
        )

    def action_catalog(
        self,
        *,
        namespace: str | None = None,
        projection: str | None = None,
    ) -> dict[str, Any]:
        """Read the caller-aware Action catalog through the shared HTTP contract."""
        return self._request(
            "GET",
            "/api/v1/actions",
            query={"namespace": namespace, "projection": projection},
        )

    def get_agent_access(self, agent_id: str, *, user_id: str = "ppx-client-user") -> dict[str, Any]:
        """Fetch one agent access snapshot."""
        return self._request(
            "GET",
            f"/api/v1/agents/{agent_id}/access",
            query={"user_id": user_id},
        )

    def list_memory_audit(
        self,
        agent_id: str,
        *,
        user_id: str = "ppx-client-user",
        limit: int = 50,
    ) -> dict[str, Any]:
        """Fetch visible explicit-memory audit rows for one agent."""
        return self._request(
            "GET",
            f"/api/v1/agents/{agent_id}/memory/audit",
            query={
                "user_id": user_id,
                "limit": limit,
            },
        )

    def list_access_audit(
        self,
        agent_id: str,
        *,
        user_id: str = "ppx-client-user",
        limit: int = 50,
        category: str = "all",
    ) -> dict[str, Any]:
        """Fetch visible admin-audit rows for one agent."""
        return self._request(
            "GET",
            f"/api/v1/agents/{agent_id}/access/audit",
            query={
                "user_id": user_id,
                "limit": limit,
                "category": category,
            },
        )

    def set_agent_owner(
        self,
        agent_id: str,
        owner_principal_id: str,
        *,
        user_id: str = "ppx-client-user",
    ) -> dict[str, Any]:
        """Set one agent owner through the HTTP client API."""
        return self._request(
            "POST",
            f"/api/v1/agents/{agent_id}/access/owner",
            json_body={
                "user_id": user_id,
                "owner_principal_id": owner_principal_id,
            },
        )

    def upsert_agent_membership(
        self,
        agent_id: str,
        principal_id: str,
        *,
        relation: str = "participant",
        user_id: str = "ppx-client-user",
    ) -> dict[str, Any]:
        """Create or update one agent membership through the HTTP client API."""
        return self._request(
            "POST",
            f"/api/v1/agents/{agent_id}/access/memberships",
            json_body={
                "user_id": user_id,
                "principal_id": principal_id,
                "relation": relation,
            },
        )

    def delete_agent_membership(
        self,
        agent_id: str,
        principal_id: str,
        *,
        user_id: str = "ppx-client-user",
    ) -> dict[str, Any]:
        """Delete one agent membership through the HTTP client API."""
        return self._request(
            "DELETE",
            f"/api/v1/agents/{agent_id}/access/memberships/{principal_id}",
            query={"user_id": user_id},
        )

    def batch_add_participants(
        self,
        agent_id: str,
        principal_ids: list[str] | tuple[str, ...],
        *,
        user_id: str = "ppx-client-user",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Add multiple participant memberships through the HTTP client API."""
        return self._request(
            "POST",
            f"/api/v1/agents/{agent_id}/access/memberships/batch",
            json_body={
                "user_id": user_id,
                "operation": "add",
                "principal_ids": list(principal_ids),
                "dry_run": bool(dry_run),
            },
        )

    def batch_remove_participants(
        self,
        agent_id: str,
        principal_ids: list[str] | tuple[str, ...],
        *,
        user_id: str = "ppx-client-user",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Remove multiple participant memberships through the HTTP client API."""
        return self._request(
            "POST",
            f"/api/v1/agents/{agent_id}/access/memberships/batch",
            json_body={
                "user_id": user_id,
                "operation": "remove",
                "principal_ids": list(principal_ids),
                "dry_run": bool(dry_run),
            },
        )

    def sync_participants(
        self,
        agent_id: str,
        principal_ids: list[str] | tuple[str, ...],
        *,
        user_id: str = "ppx-client-user",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Synchronize participant memberships through the HTTP client API."""
        return self._request(
            "POST",
            f"/api/v1/agents/{agent_id}/access/memberships/batch",
            json_body={
                "user_id": user_id,
                "operation": "sync",
                "principal_ids": list(principal_ids),
                "dry_run": bool(dry_run),
            },
        )
