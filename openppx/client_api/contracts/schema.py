"""Deterministic JSON Schema and canonical fixture exporter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ActionCatalogItem, ActionCatalogPayload, ActionInvokeRequest, ClientContractBundle


def export_client_contract(output_dir: Path) -> tuple[Path, ...]:
    """Write deterministic Increment 3 schema and fixtures below one explicit directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir = output_dir / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    documents: dict[Path, dict[str, Any]] = {
        output_dir / "schema.json": ClientContractBundle.model_json_schema(by_alias=True),
        fixtures_dir / "envelope-success.json": _success_fixture(),
        fixtures_dir / "envelope-error.json": _error_fixture(),
        fixtures_dir / "action-catalog.json": _catalog_fixture(),
        fixtures_dir / "action-invoke-status.json": _invoke_fixture(),
    }
    for path, document in documents.items():
        path.write_text(_canonical_json(document), encoding="utf-8")
    return tuple(sorted(documents))


def _canonical_json(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _success_fixture() -> dict[str, Any]:
    return {
        "protocolVersion": 1,
        "requestId": "req_status_fixture",
        "correlationId": "corr_status_fixture",
        "ok": True,
        "result": {
            "state": "ready",
            "capabilities": ["actions.catalog", "actions.invoke", "system.status"],
        },
    }


def _error_fixture() -> dict[str, Any]:
    return {
        "protocolVersion": 1,
        "requestId": "req_conflict_fixture",
        "correlationId": "corr_conflict_fixture",
        "ok": False,
        "error": {
            "code": "revision_conflict",
            "message": "The resource changed since it was read.",
            "retryable": True,
            "details": {
                "expectedRevision": "sha256:expected",
                "actualRevision": "sha256:actual",
            },
        },
    }


def _catalog_fixture() -> dict[str, Any]:
    return ActionCatalogPayload(
        items=[
            ActionCatalogItem(
                action_id="system.status",
                namespace="system",
                title="System status",
                description="Return Node configuration and capability readiness.",
                scope="node",
                input_schema={"additionalProperties": False, "properties": {}, "type": "object"},
                required_capabilities=["system.read"],
                permission="system.read",
                risk="low",
                confirmation="never",
                execution="sync",
                projections=["cli", "slash", "desktop", "mobile"],
                available=True,
            )
        ]
    ).model_dump(mode="json", by_alias=True)


def _invoke_fixture() -> dict[str, Any]:
    return ActionInvokeRequest(
        request_id="req_status_fixture",
        correlation_id="corr_status_fixture",
        action_id="system.status",
        input={},
        confirmed=False,
    ).model_dump(mode="json", by_alias=True)
