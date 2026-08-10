"""Thin HTTP client for the local openppx client API service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from ..product import PRODUCT


@dataclass(slots=True)
class ClientApiClient:
    """Call the local HTTP + SSE client API with stable typed helpers."""

    base_url: str = f"http://127.0.0.1:{PRODUCT.default_client_api_port}"
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
