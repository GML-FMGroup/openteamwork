"""Export and evaluate revision-bound policies for the trusted egress proxy."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from openppx.permissions.models import ResolvedPermissionSnapshot


def write_egress_proxy_policy(
    snapshot: ResolvedPermissionSnapshot,
    *,
    policy_directory: Path,
) -> Path:
    """Atomically publish a non-secret proxy policy keyed by permission revision."""

    requested_directory = policy_directory.expanduser()
    if requested_directory.exists() and requested_directory.is_symlink():
        raise PermissionError("Egress proxy policy directory cannot be a symlink.")
    directory = requested_directory.resolve(strict=False)
    _ensure_policy_directory_is_outside_agent_workspaces(snapshot, directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = directory.stat(follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o077:
        raise PermissionError("Egress proxy policy directory permissions are unsafe.")
    credential = _load_or_create_credential(directory, snapshot.revision)
    payload = proxy_policy_payload(
        snapshot,
        credential_digest=hashlib.sha256(credential.encode("utf-8")).hexdigest(),
    )
    destination = directory / f"{snapshot.revision.replace(':', '-')}.json"
    fd, temporary = tempfile.mkstemp(prefix=".openppx-egress-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return destination


def load_egress_proxy_credential(*, policy_directory: Path, permission_revision: str) -> str:
    """Read the high-entropy credential bound to one published policy revision."""

    directory = policy_directory.expanduser().resolve(strict=True)
    if directory.is_symlink():
        raise PermissionError("Egress proxy policy directory cannot be a symlink.")
    directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        fd = os.open(
            _credential_name(permission_revision),
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                raise PermissionError("Egress proxy credential file permissions are unsafe.")
            value = os.read(fd, 4096).decode("ascii").strip()
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)
    if len(value) < 43 or any(character.isspace() for character in value):
        raise PermissionError("Egress proxy credential is invalid.")
    return value


def proxy_policy_payload(
    snapshot: ResolvedPermissionSnapshot,
    *,
    credential_digest: str,
) -> dict[str, Any]:
    """Return the network-only policy consumed by the standalone proxy."""

    if len(credential_digest) != 64 or any(
        character not in "0123456789abcdef" for character in credential_digest
    ):
        raise ValueError("credential_digest must be a lowercase SHA-256 digest")

    defaults = {
        item.action: item.effect
        for item in snapshot.defaults
        if item.object == "network"
    }
    rules = [
        {
            "ruleId": item.rule_id,
            "effect": item.effect,
            "action": item.action,
            "selector": item.selector.model_dump(mode="json", by_alias=True),
            "constraints": item.constraints.model_dump(mode="json", by_alias=True),
        }
        for item in snapshot.rules
        if item.object == "network"
    ]
    return {
        "schemaVersion": "openppx.egress-policy/v1alpha1",
        "permissionRevision": snapshot.revision,
        "agentId": snapshot.agent_id,
        "credentialDigest": credential_digest,
        "defaults": defaults,
        "rules": rules,
    }


def proxy_policy_credential_matches(policy: Mapping[str, Any], credential: str) -> bool:
    """Verify a proxy credential without retaining plaintext policy secrets."""

    expected = policy.get("credentialDigest")
    if not isinstance(expected, str) or len(expected) != 64:
        return False
    actual = hashlib.sha256(credential.encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, actual)


def _load_or_create_credential(directory: Path, permission_revision: str) -> str:
    """Create a revision credential once so active containers remain valid."""

    directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(
                _credential_name(permission_revision),
                flags,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            return load_egress_proxy_credential(
                policy_directory=directory,
                permission_revision=permission_revision,
            )
        credential = secrets.token_urlsafe(32)
        try:
            os.write(fd, credential.encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(directory_fd)
        return credential
    finally:
        os.close(directory_fd)


def _credential_name(permission_revision: str) -> str:
    if not permission_revision.startswith("sha256:") or len(permission_revision) != 71:
        raise ValueError("invalid permission revision")
    return f"{permission_revision.replace(':', '-')}.credential"


def _ensure_policy_directory_is_outside_agent_workspaces(
    snapshot: ResolvedPermissionSnapshot,
    directory: Path,
) -> None:
    """Keep policy credentials outside every Agent-controlled Workspace."""

    workspaces = {Path(snapshot.workspace).expanduser().resolve(strict=False)}
    workspaces.update(
        Path(item.workspace).expanduser().resolve(strict=False)
        for item in snapshot.agent_workspaces
    )
    if any(directory == workspace or directory.is_relative_to(workspace) for workspace in workspaces):
        raise PermissionError("Egress proxy policy directory cannot be inside an Agent Workspace.")


def proxy_policy_allows(
    policy: Mapping[str, Any],
    *,
    scheme: str,
    host: str,
    port: int,
    resolved_ips: tuple[str, ...],
    visibility: str,
    method: str,
) -> tuple[bool, str]:
    """Evaluate proxy request facts with deny precedence and per-action defaults."""

    actions = ["connect"]
    if method.upper() == "CONNECT":
        # TLS hides the later HTTP method from a tunnel proxy. Requiring every
        # possible tunneled action makes an action-specific deny fail closed.
        actions.extend(("read", "write", "upload"))
    else:
        actions.append("read" if method.upper() in {"GET", "HEAD", "OPTIONS"} else "write")
        if actions[-1] == "write":
            actions.append("upload")
    if visibility != "public":
        actions.append("private_access")
    for action in actions:
        matching = [
            rule
            for rule in policy.get("rules", [])
            if isinstance(rule, dict)
            and rule.get("action") == action
            and _selector_matches(
                rule.get("selector"),
                scheme=scheme,
                host=host,
                port=port,
                resolved_ips=resolved_ips,
                visibility=visibility,
            )
        ]
        if any(rule.get("effect") == "deny" for rule in matching):
            return False, f"explicit_deny:{action}"
        if any(
            rule.get("effect") == "allow"
            and _proxy_constraints_match(rule.get("constraints"), action=action)
            for rule in matching
        ):
            continue
        if policy.get("defaults", {}).get(action) != "allow":
            return False, f"default_deny:{action}"
    return True, "allowed"


def _proxy_constraints_match(raw_constraints: object, *, action: str) -> bool:
    """Apply allow-rule obligations that a standalone code proxy can prove."""

    if not isinstance(raw_constraints, dict) or raw_constraints.get("kind") in {None, "none"}:
        return True
    if raw_constraints.get("kind") != "network":
        return False
    if raw_constraints.get("managedWebOnly") is True:
        return False
    if raw_constraints.get("readOnly") is True and action in {"write", "upload"}:
        return False
    return raw_constraints.get("maxResponseBytes") is None


def classify_proxy_visibility(host: str, resolved_ips: tuple[str, ...]) -> str:
    """Classify final DNS results without trusting the requested hostname alone."""

    normalized = host.lower().rstrip(".")
    addresses = tuple(ipaddress.ip_address(value) for value in resolved_ips)
    if normalized in {"localhost", "metadata.google.internal"} or normalized.endswith(".localhost"):
        return "control_plane"
    if any(str(address) in {"169.254.169.254", "100.100.100.200"} for address in addresses):
        return "control_plane"
    if any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        return "private"
    return "public"


def _selector_matches(
    raw_selector: object,
    *,
    scheme: str,
    host: str,
    port: int,
    resolved_ips: tuple[str, ...],
    visibility: str,
) -> bool:
    if not isinstance(raw_selector, dict):
        return False
    kind = raw_selector.get("kind")
    if kind == "all":
        return True
    if kind != "network":
        return False
    domains = raw_selector.get("domains") or []
    if domains and not any(_domain_matches(str(value), host) for value in domains):
        return False
    schemes = {str(value).lower() for value in raw_selector.get("schemes") or []}
    if schemes and scheme.lower() not in schemes:
        return False
    ports = {int(value) for value in raw_selector.get("ports") or []}
    if ports and port not in ports:
        return False
    visibilities = {str(value) for value in raw_selector.get("visibility") or []}
    if visibilities and visibility not in visibilities:
        return False
    origins = {str(value).lower().rstrip("/") for value in raw_selector.get("origins") or []}
    if origins and f"{scheme.lower()}://{host.lower().rstrip('.')}:{port}" not in origins:
        return False
    cidrs = raw_selector.get("cidrs") or []
    if cidrs:
        networks = tuple(ipaddress.ip_network(str(value), strict=False) for value in cidrs)
        addresses = tuple(ipaddress.ip_address(value) for value in resolved_ips)
        if not any(address in network for address in addresses for network in networks):
            return False
    return True


def _domain_matches(pattern: str, host: str) -> bool:
    normalized_pattern = pattern.lower().rstrip(".")
    normalized_host = host.lower().rstrip(".")
    if normalized_pattern.startswith("*."):
        suffix = normalized_pattern[2:]
        return normalized_host == suffix or normalized_host.endswith(f".{suffix}")
    return normalized_host == normalized_pattern


__all__ = [
    "classify_proxy_visibility",
    "load_egress_proxy_credential",
    "proxy_policy_allows",
    "proxy_policy_credential_matches",
    "proxy_policy_payload",
    "write_egress_proxy_policy",
]
