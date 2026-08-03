"""Common Client API envelope, schema, and canonical fixture tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from openppx.actions import ActionError, ActionOutcome
from openppx.client_api.contracts import (
    ActionInvokeRequest,
    ClientEnvelope,
    ContractMapper,
    ErrorEnvelope,
    ExtensionDetailPayload,
    ExtensionListPayload,
    ExtensionPreviewPayload,
    SuccessEnvelope,
    export_client_contract,
)


def test_mapper_builds_strict_success_and_error_envelopes() -> None:
    mapper = ContractMapper(protocol_version=1)
    success = mapper.from_outcome(
        ActionOutcome.success("system.status", {"state": "ready"}),
        request_id="req_success",
        correlation_id="corr_success",
    )
    error = mapper.from_outcome(
        ActionOutcome.failure(
            "config.node.apply",
            ActionError("revision_conflict", "The resource changed since it was read.", retryable=True),
        ),
        request_id="req_error",
        correlation_id="corr_error",
    )

    assert isinstance(success, SuccessEnvelope)
    assert success.model_dump(mode="json", by_alias=True)["result"] == {"state": "ready"}
    assert isinstance(error, ErrorEnvelope)
    assert error.error.code == "revision_conflict"
    assert error.error.retryable is True


def test_envelope_union_forbids_extra_fields_and_mixed_payloads() -> None:
    adapter = TypeAdapter(ClientEnvelope)
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "protocolVersion": 1,
                "requestId": "req_bad",
                "correlationId": "corr_bad",
                "ok": True,
                "result": {},
                "error": {"code": "internal_error", "message": "bad", "retryable": False},
            }
        )
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "protocolVersion": 1,
                "requestId": "req_bad",
                "correlationId": "corr_bad",
                "ok": True,
                "result": {},
                "unexpected": True,
            }
        )


def test_contract_export_is_deterministic_and_fixtures_round_trip(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    export_client_contract(first)
    export_client_contract(second)

    first_files = {path.relative_to(first): path.read_bytes() for path in first.rglob("*") if path.is_file()}
    second_files = {path.relative_to(second): path.read_bytes() for path in second.rglob("*") if path.is_file()}
    assert first_files == second_files
    assert Path("schema.json") in first_files
    tracked_root = Path(__file__).parents[2] / "contracts" / "client-api" / "v1"
    tracked_files = {
        path.relative_to(tracked_root): path.read_bytes()
        for path in tracked_root.rglob("*")
        if path.is_file()
    }
    assert tracked_files == first_files

    adapter = TypeAdapter(ClientEnvelope)
    for fixture_name in (
        "envelope-success.json",
        "envelope-error.json",
        "envelope-model-list.json",
        "envelope-session-new.json",
        "envelope-run-stop.json",
        "envelope-extension-list.json",
    ):
        payload = json.loads((first / "fixtures" / fixture_name).read_text(encoding="utf-8"))
        assert adapter.validate_python(payload)

    invocation_adapter = TypeAdapter(ActionInvokeRequest)
    for fixture_name in (
        "action-invoke-status.json",
        "action-invoke-model-list.json",
        "action-invoke-session-new.json",
        "action-invoke-run-stop.json",
        "action-invoke-extension-list.json",
    ):
        payload = json.loads((first / "fixtures" / fixture_name).read_text(encoding="utf-8"))
        assert invocation_adapter.validate_python(payload)

    schema = json.loads((first / "schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    bundle = {
        "envelope": json.loads((first / "fixtures" / "envelope-success.json").read_text(encoding="utf-8")),
        "actionCatalog": json.loads((first / "fixtures" / "action-catalog.json").read_text(encoding="utf-8")),
        "actionInvoke": json.loads((first / "fixtures" / "action-invoke-status.json").read_text(encoding="utf-8")),
        "extensionList": json.loads((first / "fixtures" / "extension-list.json").read_text(encoding="utf-8")),
        "extensionDetail": json.loads((first / "fixtures" / "extension-detail.json").read_text(encoding="utf-8")),
        "extensionPreview": json.loads((first / "fixtures" / "extension-preview.json").read_text(encoding="utf-8")),
    }
    Draft202012Validator(schema).validate(bundle)

    assert ExtensionListPayload.model_validate(bundle["extensionList"])
    assert ExtensionDetailPayload.model_validate(bundle["extensionDetail"])
    assert ExtensionPreviewPayload.model_validate(bundle["extensionPreview"])
