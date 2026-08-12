"""Compile declarative permission templates and Config overlays into snapshots."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Mapping

from openppx.product import PRODUCT

from .models import (
    AgentWorkspaceBoundary,
    ExternalPathSelector,
    PermissionChange,
    PermissionRule,
    PermissionSource,
    PermissionTemplate,
    PermissionTemplateCatalog,
    ResolvedPermissionDefault,
    ResolvedPermissionRule,
    ResolvedPermissionRollout,
    ResolvedPermissionSnapshot,
    TemplatePermissionRule,
    allowed_actions_for,
)

if TYPE_CHECKING:
    from openppx.config.models import AgentConfig, NodeConfig


_PERMISSION_SCHEMA_VERSION = "openppx.permissions/v1alpha1"


def _canonical_json(value: object) -> bytes:
    """Serialize permission data deterministically for content addressing."""

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    """Return one canonical sha256 revision for permission content."""

    return f"sha256:{hashlib.sha256(_canonical_json(value)).hexdigest()}"


@lru_cache(maxsize=1)
def _load_permission_template_catalog() -> PermissionTemplateCatalog:
    """Load and strictly validate the packaged template catalog once."""

    resource = files("openppx.permissions").joinpath("presets", "v1alpha1.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    payload["templates"] = [
        _productize_permission_copy(template)
        for template in payload.get("templates", [])
    ]
    return PermissionTemplateCatalog.model_validate(payload)


def _productize_permission_copy(value: object) -> object:
    """Replace product copy while preserving shared permission identifiers."""
    if isinstance(value, str):
        return value.replace("OpenPPX", PRODUCT.display_name)
    if isinstance(value, list):
        return [_productize_permission_copy(item) for item in value]
    if isinstance(value, dict):
        return {key: _productize_permission_copy(item) for key, item in value.items()}
    return value


def load_permission_templates() -> Mapping[str, PermissionTemplate]:
    """Return isolated low/medium/high/root templates from the validated cache."""

    catalog = _load_permission_template_catalog()
    templates = {
        template.template_id: template.model_copy(deep=True)
        for template in catalog.templates
    }
    return MappingProxyType(templates)


def _template_revision(template: PermissionTemplate) -> str:
    """Return the content revision of one packaged permission template."""

    return _sha256(template.model_dump(mode="json", by_alias=True))


def _source(
    *,
    source_id: str,
    source_kind: Literal["node", "preset", "agent"],
    source_revisions: Mapping[str, str],
    fallback_revision: str,
) -> PermissionSource:
    """Build trusted provenance without making resource revisions semantic policy input."""

    return PermissionSource(
        source_id=source_id,
        source_kind=source_kind,
        revision=source_revisions.get(source_id, fallback_revision),
    )


def _expand_rule(
    rule: PermissionRule | TemplatePermissionRule,
    *,
    origin: PermissionSource,
    locked: bool,
) -> list[ResolvedPermissionRule]:
    """Expand one multi-action config rule into canonical single-action rules."""

    expanded: list[ResolvedPermissionRule] = []
    multiple_actions = len(rule.actions) > 1
    for action in sorted(rule.actions):
        resolved_id = f"{origin.source_id}:{rule.rule_id}"
        if multiple_actions:
            resolved_id = f"{resolved_id}.{action}"
        expanded.append(
            ResolvedPermissionRule(
                rule_id=resolved_id,
                source_rule_id=rule.rule_id,
                effect=rule.effect,
                object=rule.object,
                action=action,
                selector=rule.selector.model_copy(deep=True),
                constraints=rule.constraints.model_copy(deep=True),
                description=rule.description,
                origin=origin,
                locked=locked,
            )
        )
    return expanded


def _node_safe_root_rules(node: NodeConfig, *, preset: str) -> tuple[PermissionRule, ...]:
    """Materialize Node-owned safe/protected roots for the selected preset."""

    permissions = node.spec.permissions
    rules: list[PermissionRule] = []
    if preset == "medium" and permissions.safe_external_read_roots:
        rules.append(
            PermissionRule(
                rule_id="medium-safe-external-read-roots",
                effect="allow",
                object="external_path",
                actions=["list", "read", "search"],
                selector=ExternalPathSelector(paths=list(permissions.safe_external_read_roots)),
                description="Node-approved non-sensitive external read roots for medium Agents.",
            )
        )
    if preset == "high" and permissions.high_protected_write_roots:
        rules.append(
            PermissionRule(
                rule_id="high-protected-write-roots",
                effect="deny",
                object="external_path",
                actions=["create", "write", "edit", "rename", "delete", "execute"],
                selector=ExternalPathSelector(paths=list(permissions.high_protected_write_roots)),
                description=f"{PRODUCT.display_name} and OS roots that high Agents cannot modify or execute.",
            )
        )
    return tuple(rules)


def _permission_sources(
    node: NodeConfig,
    agent: AgentConfig,
    template: PermissionTemplate,
    revisions: Mapping[str, str],
) -> tuple[PermissionSource, PermissionSource, PermissionSource]:
    """Resolve Node, preset, and Agent provenance for one compilation."""

    node_source = _source(
        source_id=f"node/{node.metadata.name}",
        source_kind="node",
        source_revisions=revisions,
        fallback_revision="unversioned",
    )
    preset_source = PermissionSource(
        source_id=f"permission-preset/{template.template_id}",
        source_kind="preset",
        revision=_template_revision(template),
    )
    agent_source = _source(
        source_id=f"agent/{agent.metadata.name}",
        source_kind="agent",
        source_revisions=revisions,
        fallback_revision="unversioned",
    )
    return node_source, preset_source, agent_source


def _compile_defaults(
    template: PermissionTemplate,
    agent: AgentConfig,
    *,
    preset_source: PermissionSource,
    agent_source: PermissionSource,
) -> tuple[ResolvedPermissionDefault, ...]:
    """Expand template defaults and apply the Agent's more specific overlay."""

    values: dict[tuple[str, str], tuple[str, PermissionSource]] = {}
    for object_kind, effect in template.object_defaults.items():
        for action in allowed_actions_for(object_kind):
            values[(object_kind, action)] = (effect, preset_source)
    for object_kind, actions in template.defaults.items():
        for action, effect in actions.items():
            values[(object_kind, action)] = (effect, preset_source)

    overlay = agent.spec.permissions
    for object_kind, effect in overlay.object_defaults.items():
        for action in allowed_actions_for(object_kind):
            values[(object_kind, action)] = (effect, agent_source)
    for object_kind, actions in overlay.defaults.items():
        for action, effect in actions.items():
            values[(object_kind, action)] = (effect, agent_source)

    return tuple(
        ResolvedPermissionDefault(
            object=object_kind,
            action=action,
            effect=effect,
            origin=origin,
        )
        for (object_kind, action), (effect, origin) in sorted(values.items())
    )


def _compile_rules(
    node: NodeConfig,
    agent: AgentConfig,
    template: PermissionTemplate,
    *,
    node_source: PermissionSource,
    preset_source: PermissionSource,
    agent_source: PermissionSource,
) -> tuple[ResolvedPermissionRule, ...]:
    """Expand and order preset, Node, Agent, and hard-deny rules."""

    rules: list[ResolvedPermissionRule] = []
    for rule in template.rules:
        rules.extend(_expand_rule(rule, origin=preset_source, locked=rule.locked))
    for rule in _node_safe_root_rules(node, preset=template.template_id):
        rules.extend(_expand_rule(rule, origin=node_source, locked=rule.effect == "deny"))
    for rule in agent.spec.permissions.rules:
        rules.extend(_expand_rule(rule, origin=agent_source, locked=False))
    for rule in node.spec.permissions.hard_rules:
        rules.extend(_expand_rule(rule, origin=node_source, locked=True))
    return tuple(sorted(rules, key=lambda item: (item.object, item.action, item.effect, item.rule_id)))


def _blocking_gates(
    node: NodeConfig,
    template: PermissionTemplate,
) -> tuple[str, ...]:
    """Return unresolved deployment Gates that prohibit enforcement."""

    gates = set(template.pending_gates)
    # Offline Docker execution supersedes the former mandatory egress-proxy Gates.
    gates.discard("medium-code-egress-proxy")
    gates.discard("high-code-egress-proxy")
    if template.template_id == "high" and not node.spec.permissions.high_protected_write_roots:
        gates.add("high-protected-write-roots")
    return tuple(sorted(gates))


def _semantic_payload(
    agent: AgentConfig,
    template: PermissionTemplate,
    agent_workspaces: tuple[AgentWorkspaceBoundary, ...],
    defaults: tuple[ResolvedPermissionDefault, ...],
    rules: tuple[ResolvedPermissionRule, ...],
    blocking_gates: tuple[str, ...],
    rollout_modes: tuple[ResolvedPermissionRollout, ...],
    code_egress_proxy: object,
) -> dict[str, object]:
    """Return only effective permission content used by the revision hash."""

    return {
        "schemaVersion": _PERMISSION_SCHEMA_VERSION,
        "agentId": agent.metadata.name,
        "workspace": agent.spec.workspace,
        "preset": template.template_id,
        "agentWorkspaces": [
            item.model_dump(mode="json", by_alias=True) for item in agent_workspaces
        ],
        "defaults": [
            {"object": item.object, "action": item.action, "effect": item.effect}
            for item in defaults
        ],
        "rules": [
            {
                "ruleId": item.rule_id,
                "effect": item.effect,
                "object": item.object,
                "action": item.action,
                "selector": item.selector.model_dump(mode="json", by_alias=True),
                "constraints": item.constraints.model_dump(mode="json", by_alias=True),
                "locked": item.locked,
            }
            for item in rules
        ],
        "blockingGates": list(blocking_gates),
        "rolloutModes": [item.model_dump(mode="json", by_alias=True) for item in rollout_modes],
        "codeEgressProxy": (
            code_egress_proxy.model_dump(mode="json", by_alias=True)
            if code_egress_proxy is not None
            else None
        ),
    }


def _compile_rollout_modes(node: NodeConfig, agent: AgentConfig) -> tuple[ResolvedPermissionRollout, ...]:
    """Resolve rollout with an optional Agent-wide override over per-object settings."""

    modes = {object_kind: "enforce" for object_kind in (
        "workspace", "external_path", "command", "process", "network", "tool"
    )}
    modes.update(node.spec.permissions.rollout_modes)
    agent_permissions = agent.spec.permissions
    if agent_permissions.rollout_mode is None:
        modes.update(agent_permissions.rollout_modes)
    else:
        modes = {
            object_kind: agent_permissions.rollout_mode
            for object_kind in modes
        }
    return tuple(
        ResolvedPermissionRollout(object=object_kind, mode=mode)
        for object_kind, mode in sorted(modes.items())
    )


def compile_permission_snapshot(
    *,
    node: NodeConfig,
    agent: AgentConfig,
    source_revisions: Mapping[str, str] | None = None,
    agent_workspaces: tuple[AgentWorkspaceBoundary, ...] = (),
) -> ResolvedPermissionSnapshot:
    """Compile Node ceilings, one preset, and one Agent overlay deterministically.

    The returned snapshot defaults to ``enforce``. Root operators may opt into
    observation explicitly; non-root Runtime gates retain their mandatory floor.
    """

    revisions = source_revisions or {}
    workspace_boundaries = tuple(
        sorted(agent_workspaces, key=lambda item: (item.agent_id, item.workspace))
    )
    template = load_permission_templates()[agent.spec.privilege_level]
    node_source, preset_source, agent_source = _permission_sources(
        node,
        agent,
        template,
        revisions,
    )
    defaults = _compile_defaults(template, agent, preset_source=preset_source, agent_source=agent_source)
    rules = _compile_rules(
        node,
        agent,
        template,
        node_source=node_source,
        preset_source=preset_source,
        agent_source=agent_source,
    )
    blocking_gates = _blocking_gates(node, template)
    rollout_modes = _compile_rollout_modes(node, agent)
    code_egress_proxy = node.spec.permissions.code_egress_proxy
    semantic_payload = _semantic_payload(
        agent,
        template,
        workspace_boundaries,
        defaults,
        rules,
        blocking_gates,
        rollout_modes,
        code_egress_proxy,
    )
    return ResolvedPermissionSnapshot(
        schema_version=_PERMISSION_SCHEMA_VERSION,
        agent_id=agent.metadata.name,
        workspace=agent.spec.workspace,
        preset=template.template_id,
        revision=_sha256(semantic_payload),
        sources=(node_source, preset_source, agent_source),
        agent_workspaces=workspace_boundaries,
        defaults=defaults,
        rules=rules,
        rollout_modes=rollout_modes,
        code_egress_proxy=code_egress_proxy,
        blocking_gates=blocking_gates,
    )


def diff_permission_snapshots(
    before: ResolvedPermissionSnapshot,
    after: ResolvedPermissionSnapshot,
) -> tuple[PermissionChange, ...]:
    """Return a deterministic semantic diff without retaining resource values."""

    changes: list[PermissionChange] = []
    before_defaults = {(item.object, item.action): item.effect for item in before.defaults}
    after_defaults = {(item.object, item.action): item.effect for item in after.defaults}
    for object_action in sorted(before_defaults.keys() | after_defaults.keys()):
        if before_defaults.get(object_action) != after_defaults.get(object_action):
            changes.append(
                PermissionChange(
                    change_kind="default_changed",
                    object=object_action[0],
                    action=object_action[1],
                )
            )

    before_rules = {rule.rule_id: rule for rule in before.rules}
    after_rules = {rule.rule_id: rule for rule in after.rules}
    for rule_id in sorted(before_rules.keys() - after_rules.keys()):
        changes.append(PermissionChange(change_kind="rule_removed", rule_id=rule_id))
    for rule_id in sorted(after_rules.keys() - before_rules.keys()):
        changes.append(PermissionChange(change_kind="rule_added", rule_id=rule_id))
    for rule_id in sorted(before_rules.keys() & after_rules.keys()):
        before_rule = before_rules[rule_id].model_dump(
            mode="json",
            by_alias=True,
            exclude={"origin", "description"},
        )
        after_rule = after_rules[rule_id].model_dump(
            mode="json",
            by_alias=True,
            exclude={"origin", "description"},
        )
        if before_rule != after_rule:
            changes.append(PermissionChange(change_kind="rule_changed", rule_id=rule_id))
    for gate in sorted(set(before.blocking_gates) - set(after.blocking_gates)):
        changes.append(PermissionChange(change_kind="gate_removed", gate=gate))
    for gate in sorted(set(after.blocking_gates) - set(before.blocking_gates)):
        changes.append(PermissionChange(change_kind="gate_added", gate=gate))
    return tuple(changes)
