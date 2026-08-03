"""Deterministic JSON Schema and canonical fixture exporter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import (
    ActionCatalogItem,
    ActionCatalogPayload,
    ActionInvokeRequest,
    ClientContractBundle,
    ExtensionDetailPayload,
    ExtensionListPayload,
    ExtensionPreviewPayload,
    ExtensionSummaryItem,
    SlashCommandInvokeInput,
    SlashCommandInvokeResult,
    SlashCommandItem,
)


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
        fixtures_dir / "action-invoke-model-list.json": _invoke_fixture(
            request_id="req_model_list_fixture",
            action_id="model.list",
        ),
        fixtures_dir / "action-invoke-session-new.json": _invoke_fixture(
            request_id="req_session_new_fixture",
            action_id="session.new",
            input={"agentId": "writer", "userId": "user_fixture"},
        ),
        fixtures_dir / "action-invoke-run-stop.json": _invoke_fixture(
            request_id="req_run_stop_fixture",
            action_id="run.stop",
            input={"runId": "run_fixture"},
        ),
        fixtures_dir / "action-invoke-extension-list.json": _invoke_fixture(
            request_id="req_extension_list_fixture",
            action_id="extension.list",
            input={"kind": None, "agentId": None},
        ),
        fixtures_dir / "action-invoke-command.json": _invoke_fixture(
            request_id="req_command_status_fixture",
            action_id="system.command.invoke",
            input=_command_input_fixture(),
        ),
        fixtures_dir / "action-invoke-setup-status.json": _invoke_fixture(
            request_id="req_setup_status_fixture",
            action_id="setup.status",
        ),
        fixtures_dir / "action-invoke-setup-apply.json": _invoke_fixture(
            request_id="req_setup_apply_fixture",
            action_id="setup.apply",
            input={"request": _setup_request_fixture()},
        ),
        fixtures_dir / "action-invoke-setup-hello.json": _invoke_fixture(
            request_id="req_setup_hello_fixture",
            action_id="setup.hello",
            input={"agentId": "main", "userId": "user_fixture", "text": "Hello OpenPPX"},
        ),
        fixtures_dir / "envelope-model-list.json": _domain_success_fixture(
            request_id="req_model_list_fixture",
            result={"items": []},
        ),
        fixtures_dir / "envelope-session-new.json": _domain_success_fixture(
            request_id="req_session_new_fixture",
            result={
                "session": {
                    "id": "session_fixture",
                    "agentId": "writer",
                    "subjectPrincipalId": "user_fixture",
                    "title": "New chat",
                    "updatedAt": "2026-08-03T00:00:00+00:00",
                    "lastMessagePreview": "",
                    "archived": False,
                }
            },
        ),
        fixtures_dir / "envelope-run-stop.json": _domain_success_fixture(
            request_id="req_run_stop_fixture",
            result={
                "run": {
                    "id": "run_fixture",
                    "agentId": "writer",
                    "sessionId": "session_fixture",
                    "snapshotRevision": "sha256:snapshot-fixture",
                    "startedAt": "2026-08-03T00:00:00+00:00",
                    "state": "cancelling",
                }
            },
        ),
        fixtures_dir / "extension-list.json": _extension_list_fixture(),
        fixtures_dir / "extension-detail.json": _extension_detail_fixture(),
        fixtures_dir / "extension-preview.json": _extension_preview_fixture(),
        fixtures_dir / "envelope-extension-list.json": _domain_success_fixture(
            request_id="req_extension_list_fixture",
            result=_extension_list_fixture(),
        ),
        fixtures_dir / "envelope-command-status.json": _domain_success_fixture(
            request_id="req_command_status_fixture",
            result=_command_result_fixture(),
        ),
        fixtures_dir / "envelope-setup-status.json": _domain_success_fixture(
            request_id="req_setup_status_fixture",
            result=_setup_status_fixture(),
        ),
        fixtures_dir / "envelope-setup-apply.json": _domain_success_fixture(
            request_id="req_setup_apply_fixture",
            result={
                "state": "configured",
                "revisions": {
                    "node": "sha256:node-fixture",
                    "agent": "sha256:agent-fixture",
                    "profile": "sha256:profile-fixture",
                },
                "secretState": "available",
                "restartRequired": False,
            },
        ),
        fixtures_dir / "envelope-setup-hello.json": _domain_success_fixture(
            request_id="req_setup_hello_fixture",
            result={"state": "ready", "sessionId": "session_fixture", "reply": "Hello from OpenPPX"},
        ),
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
                slash_commands=[
                    SlashCommandItem(
                        command="/status",
                        title="Show status",
                        description="Display Node and Agent readiness.",
                        icon="activity",
                        lifecycle="side_channel",
                        accepts_args=False,
                        order=40,
                    )
                ],
                available=True,
            )
        ]
    ).model_dump(mode="json", by_alias=True)


def _domain_success_fixture(*, request_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Return one canonical successful domain Action envelope."""

    return {
        "protocolVersion": 1,
        "requestId": request_id,
        "correlationId": request_id,
        "ok": True,
        "result": result,
    }


def _invoke_fixture(
    *,
    request_id: str = "req_status_fixture",
    action_id: str = "system.status",
    input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one canonical Action invocation request."""

    return ActionInvokeRequest(
        request_id=request_id,
        correlation_id=request_id,
        action_id=action_id,
        input=input or {},
        confirmed=False,
    ).model_dump(mode="json", by_alias=True)


def _command_input_fixture() -> dict[str, Any]:
    return SlashCommandInvokeInput(
        raw_command="/status",
        user_id="user_fixture",
        agent_id="writer",
        session_id="session_fixture",
        run_id=None,
    ).model_dump(mode="json", by_alias=True)


def _command_result_fixture() -> dict[str, Any]:
    return SlashCommandInvokeResult(
        command="/status",
        lifecycle="side_channel",
        target_action_id="system.status",
        result={"state": "ready"},
    ).model_dump(mode="json", by_alias=True)


def _setup_request_fixture() -> dict[str, Any]:
    """Return one complete non-sensitive setup request except for a fixture credential."""

    return {
        "node": {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "local-node"},
            "spec": {
                "displayName": "Local Node",
                "enabledAgents": ["main"],
                "clientApi": {"listenHost": "127.0.0.1", "port": 18765, "authentication": "disabled"},
            },
        },
        "agent": {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AgentConfig",
            "metadata": {"name": "main"},
            "spec": {
                "displayName": "Main",
                "workspace": "/workspace",
                "ownerPrincipalId": "user_fixture",
                "privilegeLevel": "medium",
                "modelPolicy": {"defaultProfile": "primary"},
            },
        },
        "profile": {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "ModelProfile",
            "metadata": {"name": "primary"},
            "spec": {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "credential": {"store": "system", "name": "primary-model-api-key"},
                "executionLocation": "remote",
                "capabilities": ["text", "tool_calling"],
            },
        },
        "secret": {
            "ref": {"store": "system", "name": "primary-model-api-key"},
            "value": "fixture-api-key",
        },
        "expectedRevisions": {"node": None, "agent": None, "profile": None},
    }


def _setup_status_fixture() -> dict[str, Any]:
    """Return the canonical empty-Node setup projection used by every client."""

    return {
        "state": "needs_configuration",
        "steps": {
            "node": "missing",
            "agent": "missing",
            "model": "missing",
            "credential": "not_required",
            "hello": "not_started",
        },
        "revisions": {"node": None, "agent": None, "profile": None},
        "recommendedWorkspace": "/workspace",
        "diagnostic": None,
        "current": {"node": None, "agent": None, "profile": None},
        "providers": [
            {
                "id": "google",
                "displayName": "Google Gemini",
                "runtime": "google_adk",
                "credentialMode": "api_key",
                "credentialRequired": True,
                "defaultModel": "gemini-2.5-flash",
            }
        ],
    }


def _extension_item_fixture() -> dict[str, Any]:
    return ExtensionSummaryItem(
        kind="skill",
        id="fixture-skill",
        display_name="Fixture Skill",
        description="A deterministic fixture Skill.",
        version="1.0.0",
        status="disabled",
        revision="sha256:fixture-skill-revision",
        source={"type": "local_archive", "trust": "local"},
        risk="low",
        enabled_agent_ids=[],
        readiness={"ready": True, "issues": []},
        managed_by=None,
    ).model_dump(mode="json", by_alias=True)


def _extension_list_fixture() -> dict[str, Any]:
    return ExtensionListPayload(items=[ExtensionSummaryItem.model_validate(_extension_item_fixture())]).model_dump(
        mode="json",
        by_alias=True,
    )


def _extension_detail_fixture() -> dict[str, Any]:
    return ExtensionDetailPayload.model_validate(
        {
            **_extension_item_fixture(),
            "details": {
                "builtin": False,
                "capabilities": ["documents.read"],
                "dependencies": {"executables": [], "environment": []},
            },
        }
    ).model_dump(mode="json", by_alias=True)


def _extension_preview_fixture() -> dict[str, Any]:
    return ExtensionPreviewPayload(
        kind="skill",
        preview={
            "skillId": "fixture-skill",
            "description": "A deterministic fixture Skill.",
            "version": "1.0.0",
            "digest": "sha256:" + "a" * 64,
            "risk": "low",
        },
    ).model_dump(mode="json", by_alias=True)
