"""Managed network authorization with normalized URL and DNS/IP facts."""

from __future__ import annotations

import ipaddress
import socket
import uuid
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .audit import NullPermissionAuditSink, PermissionAuditSink, record_permission_audit
from .evaluator import combine_permission_decisions, evaluate_permission
from .models import PermissionAction, PermissionRequest, ResolvedPermissionSnapshot


DnsResolver = Callable[[str, int], Iterable[str]]
_CONTROL_PLANE_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}
_MANAGED_SCHEME_PORTS = {"http": 80, "https": 443, "imaps": 993}


@dataclass(frozen=True, slots=True)
class AuthorizedNetworkTarget:
    """Normalized target and DNS facts fixed for one managed request attempt."""

    url: str
    scheme: str
    host: str
    port: int
    resolved_ips: tuple[str, ...]
    visibility: str
    permission_revision: str


def authorize_network_url(
    snapshot: ResolvedPermissionSnapshot,
    url: str,
    *,
    method: str = "GET",
    actions: tuple[PermissionAction, ...] = ("connect", "read"),
    task_id: str | None = None,
    run_id: str | None = None,
    audit: PermissionAuditSink | None = None,
    resolver: DnsResolver | None = None,
) -> AuthorizedNetworkTarget:
    """Authorize a managed URL after strict normalization and current DNS resolution."""

    snapshot.assert_enforce_ready("network")
    parsed = _normalize_url(url)
    port = parsed.port or _MANAGED_SCHEME_PORTS[parsed.scheme]
    rollout_mode = snapshot.enforcement_mode_for("network")
    try:
        resolved_ips = _resolve_ips(parsed.hostname or "", port, resolver=resolver)
    except OSError as exc:
        if rollout_mode == "enforce":
            raise PermissionError("Network target DNS resolution failed closed.") from exc
        resolved_ips = ()
    visibility = _visibility(parsed.hostname or "", resolved_ips)
    decisions = []
    sink = audit or NullPermissionAuditSink()
    requested_actions = tuple(dict.fromkeys(actions))
    if visibility != "public" and "private_access" not in requested_actions:
        requested_actions = (*requested_actions, "private_access")
    for action in requested_actions:
        request = PermissionRequest.model_validate(
            {
                "requestId": f"network-{uuid.uuid4().hex}",
                "permissionRevision": snapshot.revision,
                "subject": {"agentId": snapshot.agent_id, "taskId": task_id, "runId": run_id},
                "object": "network",
                "action": action,
                "resource": {
                    "kind": "network",
                    "scheme": parsed.scheme,
                    "host": parsed.hostname,
                    "port": port,
                    "resolvedIps": list(resolved_ips),
                    "visibility": visibility,
                    "method": method.upper(),
                    "managed": True,
                },
            }
        )
        decision = evaluate_permission(snapshot, request)
        record_permission_audit(
            sink,
            request,
            decision,
            rollout_mode=rollout_mode,
        )
        decisions.append(decision)
    combined = combine_permission_decisions(decisions)
    if rollout_mode == "enforce" and combined.outcome != "allow":
        raise PermissionError(
            f"Network request is denied by Agent permissions ({combined.reason_code}, "
            f"revision {snapshot.revision})."
        )
    return AuthorizedNetworkTarget(
        url=urlunsplit(parsed),
        scheme=parsed.scheme,
        host=parsed.hostname or "",
        port=port,
        resolved_ips=resolved_ips,
        visibility=visibility,
        permission_revision=snapshot.revision,
    )


def _normalize_url(raw_url: str) -> SplitResult:
    try:
        parsed = urlsplit(raw_url.strip())
    except Exception as exc:
        raise PermissionError("Network URL is invalid.") from exc
    scheme = parsed.scheme.lower()
    if scheme not in _MANAGED_SCHEME_PORTS:
        raise PermissionError("The managed network target scheme is not supported.")
    if not parsed.hostname:
        raise PermissionError("Network URL must contain a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise PermissionError("Credentials embedded in network URLs are not allowed.")
    try:
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise PermissionError("Network hostname or port is invalid.") from exc
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return SplitResult(scheme, netloc, parsed.path or "/", parsed.query, "")


def _resolve_ips(host: str, port: int, *, resolver: DnsResolver | None) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        if resolver is not None:
            values = resolver(host, port)
        else:
            values = (
                str(info[4][0])
                for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                if isinstance(info[4], tuple) and info[4]
            )
        normalized = sorted({str(ipaddress.ip_address(value)) for value in values})
        if not normalized:
            raise OSError("DNS returned no addresses")
        return tuple(normalized)
    return (str(literal),)


def _visibility(host: str, resolved_ips: tuple[str, ...]) -> str:
    normalized_host = host.lower().rstrip(".")
    addresses = tuple(ipaddress.ip_address(value) for value in resolved_ips)
    if normalized_host in {"localhost", "metadata.google.internal"} or normalized_host.endswith(".localhost"):
        return "control_plane"
    if any(address in _CONTROL_PLANE_IPS for address in addresses):
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


__all__ = ["AuthorizedNetworkTarget", "DnsResolver", "authorize_network_url"]
