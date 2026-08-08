"""Pure static permission evaluation with no Runtime or I/O side effects."""

from __future__ import annotations

import fnmatch
import ipaddress
from pathlib import Path
from typing import Iterable

from .models import (
    AgentWorkspaceSelector,
    CommandResource,
    CommandSelector,
    ExternalPathResource,
    ExternalPathSelector,
    MatchAllSelector,
    NetworkConstraints,
    NetworkResource,
    NetworkSelector,
    PermissionDecision,
    PermissionRequest,
    PermissionSubject,
    ProcessResource,
    ProcessSelector,
    ResolvedPermissionRule,
    ResolvedPermissionSnapshot,
    ToolResource,
    ToolSelector,
    WorkspacePathResource,
    WorkspacePathSelector,
)


def _workspace_selector_matches(selector: WorkspacePathSelector, resource: WorkspacePathResource) -> bool:
    """Match a trusted Workspace-relative path against configured glob patterns."""

    normalized = resource.path.replace("\\", "/").lstrip("./")
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in selector.patterns)


def _external_path_selector_matches(selector: ExternalPathSelector, resource: ExternalPathResource) -> bool:
    """Match a canonical external path against an equal or ancestor root."""

    target = Path(resource.path)
    for configured in selector.paths:
        root = Path(configured)
        if target == root or target.is_relative_to(root):
            return True
    return False


def _agent_workspace_selector_matches(selector: AgentWorkspaceSelector, resource: ExternalPathResource) -> bool:
    """Match trusted Workspace ownership facts supplied by Config resolution."""

    if selector.privilege_levels and resource.owner_privilege_level not in selector.privilege_levels:
        return False
    if selector.agent_ids and resource.owner_agent_id not in selector.agent_ids:
        return False
    return True


def _command_selector_matches(selector: CommandSelector, resource: CommandResource) -> bool:
    """Match structured executable, subcommand, profile, and shell facts."""

    if selector.executables:
        executable_name = Path(resource.executable).name
        if resource.executable not in selector.executables and executable_name not in selector.executables:
            return False
    if selector.subcommands:
        subcommand = resource.argv[0] if resource.argv else ""
        if subcommand not in selector.subcommands:
            return False
    if selector.execution_profiles and resource.execution_profile not in selector.execution_profiles:
        return False
    if selector.shell is not None and resource.shell != selector.shell:
        return False
    return True


def _process_selector_matches(
    selector: ProcessSelector,
    resource: ProcessResource,
    subject: PermissionSubject,
) -> bool:
    """Match Node-owned process provenance without trusting a PID list."""

    if selector.created_by:
        provenance_matches = {
            "allowed_command": resource.created_by_allowed_command,
            "current_task": bool(subject.task_id and resource.created_by_task_id == subject.task_id),
            "current_agent": resource.created_by_agent_id == subject.agent_id,
        }
        if not any(provenance_matches[item] for item in selector.created_by):
            return False
    if selector.protected is not None and resource.protected != selector.protected:
        return False
    if selector.system_process is not None and resource.system_process != selector.system_process:
        return False
    return True


def _domain_matches(rule_domain: str, request_host: str) -> bool:
    """Match exact domains or explicit ``*.suffix`` entries without substring rules."""

    normalized_rule = rule_domain.lower().rstrip(".")
    normalized_host = request_host.lower().rstrip(".")
    if normalized_rule.startswith("*."):
        suffix = normalized_rule[2:]
        return normalized_host == suffix or normalized_host.endswith(f".{suffix}")
    return normalized_host == normalized_rule


def _network_selector_matches(selector: NetworkSelector, resource: NetworkResource) -> bool:
    """Match normalized origin, host, IP, protocol, port, and visibility facts."""

    origin = f"{resource.scheme.lower()}://{resource.host.lower().rstrip('.')}:{resource.port}"
    if selector.origins and origin not in {item.lower().rstrip("/") for item in selector.origins}:
        return False
    if selector.domains and not any(_domain_matches(domain, resource.host) for domain in selector.domains):
        return False
    if selector.cidrs:
        networks = [ipaddress.ip_network(cidr, strict=False) for cidr in selector.cidrs]
        addresses = [ipaddress.ip_address(address) for address in resource.resolved_ips]
        if not any(address in network for address in addresses for network in networks):
            return False
    if selector.schemes and resource.scheme.lower() not in {item.lower() for item in selector.schemes}:
        return False
    if selector.ports and resource.port not in selector.ports:
        return False
    if selector.visibility and resource.visibility not in selector.visibility:
        return False
    return True


def _tool_selector_matches(selector: ToolSelector, resource: ToolResource) -> bool:
    """Match stable Tool registration facts."""

    if selector.tool_ids and resource.tool_id not in selector.tool_ids:
        return False
    if selector.operations and resource.operation not in selector.operations:
        return False
    if selector.sources and resource.source not in selector.sources:
        return False
    return True


def _rule_matches(rule: ResolvedPermissionRule, request: PermissionRequest) -> bool:
    """Return whether one already object/action-filtered rule matches a request."""

    selector = rule.selector
    resource = request.resource
    if isinstance(resource, NetworkResource) and isinstance(rule.constraints, NetworkConstraints):
        if rule.constraints.managed_web_only and not resource.managed:
            return False
        if rule.constraints.read_only and request.action in {"write", "upload"}:
            return False
    if isinstance(selector, MatchAllSelector):
        return True
    if isinstance(selector, WorkspacePathSelector) and isinstance(resource, WorkspacePathResource):
        return _workspace_selector_matches(selector, resource)
    if isinstance(selector, ExternalPathSelector) and isinstance(resource, ExternalPathResource):
        return _external_path_selector_matches(selector, resource)
    if isinstance(selector, AgentWorkspaceSelector) and isinstance(resource, ExternalPathResource):
        return _agent_workspace_selector_matches(selector, resource)
    if isinstance(selector, CommandSelector) and isinstance(resource, CommandResource):
        return _command_selector_matches(selector, resource)
    if isinstance(selector, ProcessSelector) and isinstance(resource, ProcessResource):
        return _process_selector_matches(selector, resource, request.subject)
    if isinstance(selector, NetworkSelector) and isinstance(resource, NetworkResource):
        return _network_selector_matches(selector, resource)
    if isinstance(selector, ToolSelector) and isinstance(resource, ToolResource):
        return _tool_selector_matches(selector, resource)
    return False


def evaluate_permission(
    snapshot: ResolvedPermissionSnapshot,
    request: PermissionRequest,
) -> PermissionDecision:
    """Evaluate one static permission request using deny precedence.

    This function computes the shadow decision recorded by the Runtime. Callers
    must not enforce it until the corresponding object rollout enters enforce.
    """

    if request.permission_revision != snapshot.revision:
        return PermissionDecision(
            outcome="deny",
            reason_code="permission_revision_mismatch",
            permission_revision=snapshot.revision,
        )
    if request.subject.agent_id != snapshot.agent_id:
        return PermissionDecision(
            outcome="deny",
            reason_code="permission_subject_mismatch",
            permission_revision=snapshot.revision,
        )

    matching = tuple(
        rule
        for rule in snapshot.rules
        if rule.object == request.object and rule.action == request.action and _rule_matches(rule, request)
    )
    denied = tuple(rule.rule_id for rule in matching if rule.effect == "deny")
    allowed = tuple(rule.rule_id for rule in matching if rule.effect == "allow")
    obligations = tuple(
        f"constraint:{rule.rule_id}"
        for rule in matching
        if rule.effect == "allow" and rule.constraints.kind != "none"
    )
    if denied:
        return PermissionDecision(
            outcome="deny",
            reason_code="explicit_deny",
            permission_revision=snapshot.revision,
            matched_rule_ids=tuple(sorted((*denied, *allowed))),
        )
    if allowed:
        return PermissionDecision(
            outcome="allow",
            reason_code="explicit_allow",
            permission_revision=snapshot.revision,
            matched_rule_ids=tuple(sorted(allowed)),
            obligations=tuple(sorted(obligations)),
        )

    default = snapshot.default_for(request.object, request.action)
    return PermissionDecision(
        outcome=default,
        reason_code=f"default_{default}",
        permission_revision=snapshot.revision,
    )


def combine_permission_decisions(decisions: Iterable[PermissionDecision]) -> PermissionDecision:
    """Intersect multiple object decisions using the minimum-permission result."""

    items = tuple(decisions)
    if not items:
        raise ValueError("at least one permission decision is required")
    revisions = {item.permission_revision for item in items}
    if len(revisions) != 1:
        raise ValueError("permission decisions must use the same revision")
    revision = items[0].permission_revision
    matched_rule_ids = tuple(sorted({rule_id for item in items for rule_id in item.matched_rule_ids}))
    obligations = tuple(sorted({obligation for item in items for obligation in item.obligations}))
    if any(item.outcome == "deny" for item in items):
        return PermissionDecision(
            outcome="deny",
            reason_code="intersection_denied",
            permission_revision=revision,
            matched_rule_ids=matched_rule_ids,
        )
    if any(item.outcome == "requires_approval" for item in items):
        return PermissionDecision(
            outcome="requires_approval",
            reason_code="intersection_requires_approval",
            permission_revision=revision,
            matched_rule_ids=matched_rule_ids,
            obligations=obligations,
        )
    return PermissionDecision(
        outcome="allow",
        reason_code="intersection_allowed",
        permission_revision=revision,
        matched_rule_ids=matched_rule_ids,
        obligations=obligations,
    )
