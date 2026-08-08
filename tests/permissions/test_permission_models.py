"""Validation tests for runtime-neutral permission contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openppx.permissions import ExternalPathSelector, PermissionRequest, PermissionRule, WorkspacePathSelector


def test_rule_rejects_an_action_from_another_permission_object() -> None:
    with pytest.raises(ValidationError, match="invalid for object 'tool'"):
        PermissionRule.model_validate(
            {
                "ruleId": "invalid-tool-read",
                "effect": "allow",
                "object": "tool",
                "actions": ["read"],
                "selector": {"kind": "tool", "toolIds": ["builtin.files"]},
            }
        )


def test_rule_rejects_a_selector_from_another_permission_object() -> None:
    with pytest.raises(ValidationError, match="selector kind 'network'"):
        PermissionRule.model_validate(
            {
                "ruleId": "invalid-workspace-selector",
                "effect": "allow",
                "object": "workspace",
                "actions": ["read"],
                "selector": {"kind": "network", "domains": ["example.com"]},
            }
        )


def test_workspace_selector_rejects_absolute_and_parent_traversal_patterns() -> None:
    for pattern in ("/etc/**", "drafts/../private/**"):
        with pytest.raises(ValidationError, match="relative"):
            WorkspacePathSelector(patterns=[pattern])


def test_external_path_selector_requires_absolute_roots() -> None:
    with pytest.raises(ValidationError, match="absolute"):
        ExternalPathSelector(paths=["shared/reference"])


def test_permission_collections_accept_json_arrays_and_are_immutable() -> None:
    selector = WorkspacePathSelector.model_validate({"patterns": ["docs/**"]})

    assert selector.patterns == ("docs/**",)
    with pytest.raises(ValidationError, match="frozen"):
        selector.patterns += ("private/**",)


def test_network_selector_rejects_invalid_cidr() -> None:
    with pytest.raises(ValidationError, match="invalid network CIDR"):
        PermissionRule.model_validate(
            {
                "ruleId": "invalid-cidr",
                "effect": "deny",
                "object": "network",
                "actions": ["connect"],
                "selector": {"kind": "network", "cidrs": ["10.0.0.0/999"]},
            }
        )


def test_request_rejects_resource_facts_for_another_object() -> None:
    with pytest.raises(ValidationError, match="resource kind 'tool'"):
        PermissionRequest.model_validate(
            {
                "requestId": "request-1",
                "permissionRevision": "sha256:" + "0" * 64,
                "subject": {"agentId": "writer"},
                "object": "workspace",
                "action": "read",
                "resource": {
                    "kind": "tool",
                    "toolId": "builtin.files",
                    "operation": "read",
                    "source": "builtin",
                },
            }
        )
