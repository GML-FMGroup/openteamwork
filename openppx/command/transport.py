"""CLI transport and rendering helpers shared by product commands."""

from __future__ import annotations

import json
import uuid
from typing import Any

from openppx.product import PRODUCT
from openppx.runtime.client_api_client import ClientApiClient


def parse_json_object(raw: str, *, label: str = "JSON input") -> dict[str, object]:
    """Parse one strict JSON object for an Action input."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def read_json_object(path: str) -> dict[str, object]:
    """Read one strict JSON object from a candidate file."""
    from pathlib import Path

    try:
        return parse_json_object(Path(path).expanduser().read_text(encoding="utf-8"), label=path)
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def render_envelope(payload: dict[str, Any], *, output_json: bool) -> int:
    """Render one common Client API envelope and return a process exit code."""
    if output_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") is True else 1
    if payload.get("ok") is True:
        result = payload.get("result", payload.get("data", {}))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    message = str(error.get("message") or f"{PRODUCT.display_name} Node rejected the request.")
    code = str(error.get("code") or "unknown_error")
    print(f"Error [{code}]: {message}")
    return 1


def client_for(args: Any) -> ClientApiClient:
    """Construct the shared HTTP Action client from parsed transport options."""
    return ClientApiClient(base_url=args.client_api_url, access_token=args.access_token)


def invoke_action(
    args: Any,
    action_id: str,
    raw_input: dict[str, object],
    *,
    confirmed: bool = False,
) -> int:
    """Invoke one Action on a running Node and render its common envelope."""
    request_id = f"req_cli_{uuid.uuid4().hex}"
    try:
        payload = client_for(args).invoke_action(
            action_id,
            raw_input,
            confirmed=confirmed,
            request_id=request_id,
        )
    except (OSError, TimeoutError, ValueError) as exc:
        print(f"Error: unable to reach {PRODUCT.display_name} Node at {args.client_api_url}: {exc}")
        return 1
    return render_envelope(payload, output_json=args.output_json)


def action_catalog(args: Any) -> int:
    """Read and render the caller-visible Action catalog."""
    try:
        payload = client_for(args).action_catalog(namespace=args.namespace, projection=args.projection)
    except (OSError, TimeoutError, ValueError) as exc:
        print(f"Error: unable to reach {PRODUCT.display_name} Node at {args.client_api_url}: {exc}")
        return 1
    return render_envelope(payload, output_json=args.output_json)
