"""Structured Command authorization shared by trusted execution adapters."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from .audit import NullPermissionAuditSink, PermissionAuditSink, record_permission_audit
from .evaluator import evaluate_permission
from .models import CommandConstraints, PermissionRequest, ResolvedPermissionSnapshot


_LOW_EXECUTABLES = {"grep", "rg"}
_LOW_DENIED_OPTIONS = {
    "--pre",
    "--pre-glob",
    "--hostname-bin",
    "--file",
    "--exclude-from",
    "-f",
}


@dataclass(frozen=True, slots=True)
class AuthorizedCommand:
    """Command facts and mandatory execution boundary chosen by policy."""

    argv: tuple[str, ...]
    cwd: Path
    execution_profile: str
    required_backend: str | None
    permission_revision: str
    timeout_seconds: int
    max_output_bytes: int
    allowed_by_policy: bool


def authorize_command(
    snapshot: ResolvedPermissionSnapshot,
    *,
    workspace_root: Path,
    argv: list[str] | tuple[str, ...],
    cwd: Path,
    shell: bool,
    background: bool,
    pty: bool,
    timeout_seconds: int,
    task_id: str | None = None,
    run_id: str | None = None,
    audit: PermissionAuditSink | None = None,
) -> AuthorizedCommand:
    """Authorize parsed argv and derive the non-model-selectable execution profile."""

    if not argv:
        raise PermissionError("Command argv must be non-empty.")
    snapshot.assert_enforce_ready("command")
    workspace = workspace_root.expanduser().resolve(strict=False)
    resolved_cwd = cwd.expanduser().resolve(strict=False)
    profile, backend, timeout_cap, output_cap = _profile_for(snapshot.preset)
    request = PermissionRequest.model_validate(
        {
            "requestId": f"command-{uuid.uuid4().hex}",
            "permissionRevision": snapshot.revision,
            "subject": {"agentId": snapshot.agent_id, "taskId": task_id, "runId": run_id},
            "object": "command",
            "action": "execute",
            "resource": {
                "kind": "command",
                "executable": argv[0],
                "argv": list(argv[1:]),
                "cwd": str(resolved_cwd),
                "shell": shell,
                "background": background,
                "pty": pty,
                "executionProfile": profile,
            },
        }
    )
    decision = evaluate_permission(snapshot, request)
    rollout_mode = snapshot.rollout_for("command")
    record_permission_audit(
        audit or NullPermissionAuditSink(),
        request,
        decision,
        rollout_mode=rollout_mode,
    )
    if rollout_mode == "enforce":
        if decision.outcome != "allow":
            raise PermissionError(
                f"Command is denied by Agent permissions ({decision.reason_code}, revision {snapshot.revision})."
            )
        if snapshot.preset == "low":
            _validate_low_command(
                argv=tuple(argv),
                workspace=workspace,
                cwd=resolved_cwd,
                shell=shell,
                background=background,
                pty=pty,
            )
        constraints = tuple(
            rule.constraints
            for rule in snapshot.rules
            if rule.rule_id in decision.matched_rule_ids
            and rule.effect == "allow"
            and isinstance(rule.constraints, CommandConstraints)
        )
        _validate_command_constraints(
            constraints,
            execution_profile=profile,
            shell=shell,
            background=background,
            pty=pty,
        )
        timeout_values = [item.timeout_seconds for item in constraints if item.timeout_seconds]
        output_values = [item.max_output_bytes for item in constraints if item.max_output_bytes]
        if timeout_values:
            timeout_cap = min(timeout_cap, *timeout_values)
        if output_values:
            output_cap = min(output_cap, *output_values)
    return AuthorizedCommand(
        argv=tuple(argv),
        cwd=resolved_cwd,
        execution_profile=profile,
        required_backend=backend if rollout_mode == "enforce" else None,
        permission_revision=snapshot.revision,
        timeout_seconds=(
            min(max(1, int(timeout_seconds)), timeout_cap)
            if rollout_mode == "enforce"
            else max(1, int(timeout_seconds))
        ),
        max_output_bytes=output_cap,
        allowed_by_policy=decision.outcome == "allow",
    )


def _validate_command_constraints(
    constraints: tuple[CommandConstraints, ...],
    *,
    execution_profile: str,
    shell: bool,
    background: bool,
    pty: bool,
) -> None:
    """Intersect every matched Command obligation before process creation."""

    for item in constraints:
        if item.execution_profile is not None and item.execution_profile != execution_profile:
            raise PermissionError("Command rule requires a different execution profile.")
        if shell and not item.allow_shell:
            raise PermissionError("Command rule does not allow Shell execution.")
        if background and not item.allow_background:
            raise PermissionError("Command rule does not allow background execution.")
        if pty and not item.allow_pty:
            raise PermissionError("Command rule does not allow PTY execution.")


def _profile_for(preset: str) -> tuple[str, str | None, int, int]:
    if preset == "low":
        return "low-workspace-readonly", "docker", 30, 1024 * 1024
    if preset == "medium":
        return "medium-task-sandbox", "docker", 300, 2 * 1024 * 1024
    if preset == "high":
        return "high-protected-sandbox", "docker", 600, 4 * 1024 * 1024
    return "root-host", None, 3600, 8 * 1024 * 1024


def _validate_low_command(
    *,
    argv: tuple[str, ...],
    workspace: Path,
    cwd: Path,
    shell: bool,
    background: bool,
    pty: bool,
) -> None:
    """Enforce the audited low grep/rg profile without trusting command names alone."""

    if shell or background or pty:
        raise PermissionError("Low command profiles forbid Shell, background execution, and PTY.")
    if not cwd.is_relative_to(workspace):
        raise PermissionError("Low command cwd must stay inside the Agent Workspace.")
    if Path(argv[0]).name not in _LOW_EXECUTABLES:
        raise PermissionError("Low command profile allows only grep and rg.")
    for token in argv[1:]:
        option = token.split("=", 1)[0]
        if option in _LOW_DENIED_OPTIONS:
            raise PermissionError(f"Low command option is not allowed: {option}")
        if token.startswith((">", "<")) or token in {"|", "||", "&&", ";", "&"}:
            raise PermissionError("Low command profiles forbid Shell operators and redirection.")
        candidate = token.split("=", 1)[1] if token.startswith("--") and "=" in token else token
        if not _looks_like_path(candidate):
            continue
        candidate_path = Path(candidate).expanduser()
        resolved = (
            candidate_path.resolve(strict=False)
            if candidate_path.is_absolute()
            else (cwd / candidate_path).resolve(strict=False)
        )
        if not resolved.is_relative_to(workspace):
            raise PermissionError(f"Low command path must stay inside the Agent Workspace: {candidate}")


def _looks_like_path(value: str) -> bool:
    return value.startswith((".", "/", "~")) or "/" in value or "\\" in value


__all__ = ["AuthorizedCommand", "authorize_command"]
