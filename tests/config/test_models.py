"""Tests for strict Node and Agent configuration resources."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from openppx.config import AgentConfig, NodeConfig, export_config_schemas


def node_document() -> dict[str, object]:
    """Return one minimal valid NodeConfig document."""
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "NodeConfig",
        "metadata": {"name": "local-node"},
        "spec": {
            "displayName": "Local Node",
            "enabledAgents": ["low-main"],
            "clientApi": {
                "listenHost": "127.0.0.1",
                "port": 18765,
                "authentication": "required",
            },
        },
    }


def agent_document() -> dict[str, object]:
    """Return one minimal valid AgentConfig document."""
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "AgentConfig",
        "metadata": {"name": "low-main"},
        "spec": {
            "displayName": "Low Main",
            "workspace": "workspace/low-main",
            "ownerPrincipalId": "local:owner",
            "privilegeLevel": "low",
            "controls": {},
        },
    }


def test_node_config_parses_aliases_and_typed_defaults() -> None:
    resource = NodeConfig.model_validate(node_document())

    assert resource.metadata.name == "local-node"
    assert resource.spec.enabled_agents == ["low-main"]
    assert resource.spec.client_api.port == 18765
    assert resource.spec.operations.task_scheduler_enabled is True
    assert resource.spec.operations.cron_enabled is True
    assert resource.spec.operations.heartbeat.enabled is False
    assert resource.spec.runtime.context_compaction.enabled is True
    assert resource.spec.runtime.context_compaction.threshold_percent == 70
    assert resource.model_dump(mode="json", by_alias=True)["apiVersion"] == "openppx.io/v1alpha1"


def test_node_context_compaction_percent_is_bounded() -> None:
    document = deepcopy(node_document())
    document["spec"]["runtime"] = {  # type: ignore[index]
        "contextCompaction": {"enabled": True, "thresholdPercent": 91}
    }

    with pytest.raises(ValidationError, match="less than or equal to 90"):
        NodeConfig.model_validate(document)


def test_node_operations_config_is_strict_and_validates_heartbeat_window() -> None:
    document = deepcopy(node_document())
    document["spec"]["operations"] = {  # type: ignore[index]
        "taskSchedulerEnabled": False,
        "cronEnabled": True,
        "heartbeat": {
            "enabled": True,
            "everySeconds": 120,
            "prompt": "Inspect pending work.",
            "activeHours": {"start": "08:30", "end": "20:00", "timezone": "Asia/Shanghai"},
        },
    }

    resource = NodeConfig.model_validate(document)

    assert resource.spec.operations.task_scheduler_enabled is False
    assert resource.spec.operations.heartbeat.every_seconds == 120
    assert resource.spec.operations.heartbeat.active_hours.timezone == "Asia/Shanghai"

    document["spec"]["operations"]["heartbeat"]["activeHours"] = {  # type: ignore[index]
        "start": "08:30",
        "end": None,
        "timezone": "user",
    }
    with pytest.raises(ValidationError, match="configured together"):
        NodeConfig.model_validate(document)


def test_agent_config_parses_minimal_resource() -> None:
    resource = AgentConfig.model_validate(agent_document())

    assert resource.metadata.name == "low-main"
    assert resource.spec.workspace == "workspace/low-main"
    assert resource.spec.privilege_level == "low"


def test_agent_permissions_accept_optional_global_rollout_mode() -> None:
    document = deepcopy(agent_document())
    document["spec"]["permissions"] = {  # type: ignore[index]
        "rolloutMode": "enforce",
        "rolloutModes": {"network": "observe"},
    }

    resource = AgentConfig.model_validate(document)
    serialized = resource.model_dump(mode="json", by_alias=True)

    assert resource.spec.permissions.rollout_mode == "enforce"
    assert resource.spec.permissions.rollout_modes == {"network": "observe"}
    assert serialized["spec"]["permissions"]["rolloutMode"] == "enforce"


def test_agent_permissions_reject_invalid_global_rollout_mode() -> None:
    document = deepcopy(agent_document())
    document["spec"]["permissions"] = {"rolloutMode": False}  # type: ignore[index]

    with pytest.raises(ValidationError):
        AgentConfig.model_validate(document)


@pytest.mark.parametrize(
    ("document", "location"),
    [
        ({**node_document(), "unexpected": True}, "unexpected"),
        (
            {
                **node_document(),
                "metadata": {"name": "local-node", "unexpected": True},
            },
            "metadata.unexpected",
        ),
        (
            {
                **node_document(),
                "spec": {**node_document()["spec"], "unexpected": True},  # type: ignore[dict-item]
            },
            "spec.unexpected",
        ),
        (
            {
                **node_document(),
                "spec": {
                    **node_document()["spec"],  # type: ignore[dict-item]
                    "clientApi": {
                        **node_document()["spec"]["clientApi"],  # type: ignore[index]
                        "unexpected": True,
                    },
                },
            },
            "spec.clientApi.unexpected",
        ),
    ],
)
def test_node_config_forbids_unknown_fields(document: dict[str, object], location: str) -> None:
    with pytest.raises(ValidationError) as raised:
        NodeConfig.model_validate(document)

    assert location in str(raised.value)


def test_node_port_rejects_boolean() -> None:
    document = deepcopy(node_document())
    document["spec"]["clientApi"]["port"] = True  # type: ignore[index]

    with pytest.raises(ValidationError):
        NodeConfig.model_validate(document)


@pytest.mark.parametrize("listen_host", [" ", "bad host", "bad_host", "-node.lan", "node..lan"])
def test_node_rejects_malformed_listen_host(listen_host: str) -> None:
    document = deepcopy(node_document())
    document["spec"]["clientApi"]["listenHost"] = listen_host  # type: ignore[index]

    with pytest.raises(ValidationError):
        NodeConfig.model_validate(document)


def test_node_rejects_duplicate_enabled_agents() -> None:
    document = deepcopy(node_document())
    document["spec"]["enabledAgents"] = ["low-main", "low-main"]  # type: ignore[index]

    with pytest.raises(ValidationError, match="unique"):
        NodeConfig.model_validate(document)


@pytest.mark.parametrize("listen_host", ["0.0.0.0", "192.168.1.10", "node.lan"])
def test_node_non_loopback_requires_authentication(listen_host: str) -> None:
    document = deepcopy(node_document())
    document["spec"]["clientApi"] = {  # type: ignore[index]
        "listenHost": listen_host,
        "port": 18765,
        "authentication": "disabled",
    }

    with pytest.raises(ValidationError, match="authentication"):
        NodeConfig.model_validate(document)


@pytest.mark.parametrize("name", ["Upper", "-bad", "bad-", "bad_name", "a" * 64])
def test_resource_name_is_strict(name: str) -> None:
    document = deepcopy(node_document())
    document["metadata"]["name"] = name  # type: ignore[index]

    with pytest.raises(ValidationError):
        NodeConfig.model_validate(document)


def test_metadata_annotations_are_the_only_open_namespace() -> None:
    document = deepcopy(agent_document())
    document["metadata"] = {  # type: ignore[assignment]
        "name": "low-main",
        "labels": {"team": "personal"},
        "annotations": {"example.io/note": "safe metadata"},
    }

    resource = AgentConfig.model_validate(document)

    assert resource.metadata.annotations == {"example.io/note": "safe metadata"}


@pytest.mark.parametrize(
    ("field", "value"),
    [("displayName", "   "), ("workspace", "\x00bad"), ("ownerPrincipalId", "\n")],
)
def test_agent_rejects_blank_or_control_bearing_text(field: str, value: str) -> None:
    document = deepcopy(agent_document())
    document["spec"][field] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        AgentConfig.model_validate(document)


def test_removed_execution_permission_overrides_are_not_accepted() -> None:
    document = deepcopy(agent_document())
    document["spec"]["permissionOverrides"] = {"filesystemAccess": "read_write"}  # type: ignore[index]

    with pytest.raises(ValidationError, match="filesystemAccess"):
        AgentConfig.model_validate(document)


def test_high_agent_controls_can_narrow_non_execution_capabilities() -> None:
    document = deepcopy(agent_document())
    document["spec"]["privilegeLevel"] = "high"  # type: ignore[index]
    document["spec"]["controls"] = {  # type: ignore[index]
        "secretAccess": "none",
        "canApprovePrivilegeEscalation": False,
    }

    resource = AgentConfig.model_validate(document)

    assert resource.spec.controls.secret_access == "none"


def test_exported_json_schemas_are_strict() -> None:
    schemas = export_config_schemas()

    assert set(schemas) == {"AgentConfig", "NodeConfig"}
    assert schemas["NodeConfig"]["additionalProperties"] is False
    assert schemas["AgentConfig"]["additionalProperties"] is False
    assert schemas["NodeConfig"]["properties"]["kind"]["const"] == "NodeConfig"
