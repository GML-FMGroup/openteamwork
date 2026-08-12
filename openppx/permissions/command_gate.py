"""Structured Command authorization shared by trusted execution adapters."""

from __future__ import annotations

import re
import shlex
import uuid
from dataclasses import dataclass
from pathlib import Path

from .audit import NullPermissionAuditSink, PermissionAuditSink, record_permission_audit
from .evaluator import evaluate_permission
from .models import CommandConstraints, PermissionDecision, PermissionRequest, ResolvedPermissionSnapshot


_LOW_EXECUTABLES = {"grep", "rg"}
_LOW_DENIED_OPTIONS = {
    "--pre",
    "--pre-glob",
    "--hostname-bin",
    "--file",
    "--exclude-from",
    "-f",
}
_SHELL_CONTROL_TOKENS = {"&&", "||", ";", "|"}
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*\Z")
_PYTHON_EXECUTABLE = re.compile(r"python(?:\d+(?:\.\d+)*)?\Z")
_PIP_EXECUTABLE = re.compile(r"pip(?:\d+(?:\.\d+)*)?\Z")
_RUNTIME_INSTALL_ERROR = (
    "Runtime package installation is not allowed for non-root Agents; "
    "ask an administrator to rebuild the reviewed sandbox image."
)


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
    runtime_install_denied = snapshot.preset != "root" and _contains_runtime_package_install(tuple(argv))
    if runtime_install_denied:
        decision = PermissionDecision(
            outcome="deny",
            reason_code="runtime_package_install_denied",
            permission_revision=snapshot.revision,
        )
    rollout_mode = snapshot.enforcement_mode_for("command")
    record_permission_audit(
        audit or NullPermissionAuditSink(),
        request,
        decision,
        rollout_mode=rollout_mode,
    )
    if rollout_mode == "enforce":
        if runtime_install_denied:
            raise PermissionError(_RUNTIME_INSTALL_ERROR)
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


def _contains_runtime_package_install(argv: tuple[str, ...], *, depth: int = 0) -> bool:
    """Recognize common package-manager mutation commands, including shell wrappers."""

    if not argv or depth > 3:
        return False
    segments: list[list[str]] = [[]]
    for token in argv:
        if token in _SHELL_CONTROL_TOKENS:
            segments.append([])
        else:
            segments[-1].append(token)
    return any(_segment_installs_packages(tuple(segment), depth=depth) for segment in segments if segment)


def _segment_installs_packages(argv: tuple[str, ...], *, depth: int) -> bool:
    """Return whether one command segment invokes a known runtime installer."""

    assignment_count = 0
    while assignment_count < len(argv) and _ENV_ASSIGNMENT.fullmatch(argv[assignment_count]):
        assignment_count += 1
    if assignment_count:
        return _contains_runtime_package_install(argv[assignment_count:], depth=depth + 1)

    executable = Path(argv[0]).name.casefold()
    args = tuple(item.casefold() for item in argv[1:])

    shell_command_option = next(
        (index for index, item in enumerate(args) if item.startswith("-") and "c" in item[1:]),
        None,
    )
    if executable in {"sh", "bash", "zsh", "dash"} and shell_command_option is not None:
        command_index = shell_command_option + 2
        if command_index < len(argv):
            try:
                nested = tuple(shlex.split(argv[command_index]))
            except ValueError:
                return True
            return _contains_runtime_package_install(nested, depth=depth + 1)

    if executable == "env":
        nested_index = 1
        while nested_index < len(argv):
            token = argv[nested_index]
            if token.startswith("-") or "=" in token:
                nested_index += 1
                continue
            break
        return _contains_runtime_package_install(argv[nested_index:], depth=depth + 1)

    if executable in {"command", "doas", "nohup", "sudo", "time"}:
        nested_index = 1
        while nested_index < len(argv) and argv[nested_index].startswith("-"):
            nested_index += 1
        return _contains_runtime_package_install(argv[nested_index:], depth=depth + 1)

    if executable in {"npx", "bunx", "corepack", "uvx"}:
        return True
    if executable in {"npm", "pnpm", "bun"}:
        return bool({"add", "ci", "dlx", "exec", "i", "install", "update", "upgrade"} & set(args))
    if executable == "yarn":
        return not args or bool({"add", "dlx", "install", "set", "up", "upgrade"} & set(args))
    if _PIP_EXECUTABLE.fullmatch(executable) or executable == "pipx":
        return bool({"inject", "install", "upgrade", "upgrade-all"} & set(args))
    if _PYTHON_EXECUTABLE.fullmatch(executable) and "-m" in args:
        module_index = args.index("-m") + 1
        if module_index < len(args):
            module = args[module_index]
            if module == "ensurepip":
                return True
            if module in {"pip", "pipx", "uv", "poetry", "pipenv"}:
                return _contains_runtime_package_install(
                    (module, *argv[module_index + 2 :]),
                    depth=depth + 1,
                )
    if executable == "uv":
        return bool({"add", "install", "sync", "upgrade"} & set(args))
    if executable in {"poetry", "pipenv"}:
        return bool({"add", "install", "sync", "update", "upgrade"} & set(args))
    if executable in {"conda", "mamba", "micromamba"}:
        return bool({"create", "install", "update", "upgrade"} & set(args))
    if executable in {"apt", "apt-get", "dnf", "yum", "brew", "port"}:
        return bool({"add", "install", "reinstall", "upgrade"} & set(args))
    if executable == "apk":
        return "add" in args
    if executable in {"cargo", "gem"}:
        return "install" in args
    if executable == "go":
        return bool({"get", "install"} & set(args))
    return False


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
