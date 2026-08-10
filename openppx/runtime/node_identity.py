"""Persistent identity for one OpenPPX Node installation."""

from __future__ import annotations

import ipaddress
import json
import os
import platform
import socket
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from subprocess import PIPE, Popen as _PlatformCommand, TimeoutExpired
from typing import Any

from openppx.product import PRODUCT


_NODE_IDENTITY_FILE = "node.json"


@dataclass(frozen=True)
class NodeIdentity:
    """Stable, non-secret identity advertised by one OpenPPX Node."""

    node_id: str
    display_name: str

    def as_dict(self) -> dict[str, str]:
        """Return the wire-safe identity representation."""

        return {"node_id": self.node_id, "display_name": self.display_name}


def _parse_identity(payload: Any, *, path: Path) -> NodeIdentity:
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid {PRODUCT.display_name} Node identity file: {path}")
    node_id = str(payload.get("node_id") or "").strip()
    display_name = str(payload.get("display_name") or "").strip()
    if not node_id.startswith("node_") or len(node_id) <= len("node_") or not display_name:
        raise ValueError(f"Invalid {PRODUCT.display_name} Node identity file: {path}")
    return NodeIdentity(node_id=node_id, display_name=display_name)


def _read_identity(path: Path) -> NodeIdentity:
    return _parse_identity(json.loads(path.read_text(encoding="utf-8")), path=path)


def _preferred_platform_display_name() -> str:
    """Return a human-readable computer name exposed by the host platform."""

    system_name = platform.system()
    if system_name == "Darwin":
        try:
            process = _PlatformCommand(
                ["/usr/sbin/scutil", "--get", "ComputerName"],
                stdout=PIPE,
                stderr=PIPE,
                text=True,
            )
            try:
                stdout, _ = process.communicate(timeout=1.0)
            except TimeoutExpired:
                process.kill()
                process.communicate()
                return ""
        except OSError:
            return ""
        if process.returncode == 0:
            return stdout.strip()
        return ""
    if system_name == "Windows":
        return os.getenv("COMPUTERNAME", "").strip()
    return ""


def _is_ip_address(value: str) -> bool:
    """Return whether a non-empty display-name candidate is an IP address."""

    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _default_node_display_name() -> str:
    """Resolve a stable human-facing default without exposing an IP hostname."""

    platform_name = _preferred_platform_display_name().strip()
    if platform_name and not _is_ip_address(platform_name):
        return platform_name

    hostname = socket.gethostname().strip().rstrip(".")
    if hostname and not _is_ip_address(hostname):
        short_hostname = hostname.split(".", 1)[0].strip()
        if short_hostname and not _is_ip_address(short_hostname):
            return short_hostname
    return f"{PRODUCT.display_name} Node"


def _is_legacy_ip_prefix_identity(identity: NodeIdentity) -> bool:
    """Detect display names produced by truncating an IPv4 hostname at its first dot."""

    hostname = socket.gethostname().strip()
    if not hostname or not _is_ip_address(hostname) or "." not in hostname:
        return False
    return identity.display_name == hostname.split(".", 1)[0]


def _write_identity(path: Path, identity: NodeIdentity, *, replace_existing: bool) -> NodeIdentity:
    """Publish an identity atomically while keeping the file private."""

    payload = json.dumps(identity.as_dict(), ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{_NODE_IDENTITY_FILE}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if replace_existing:
            os.replace(temporary_path, path)
        else:
            try:
                # A hard link publishes only the complete file and never overwrites a competing creator.
                os.link(temporary_path, path)
            except FileExistsError:
                return _read_identity(path)
        return identity
    finally:
        temporary_path.unlink(missing_ok=True)


def load_or_create_node_identity(data_dir: Path) -> NodeIdentity:
    """Load the stable Node identity, creating or repairing it atomically when needed."""

    path = data_dir / _NODE_IDENTITY_FILE
    if path.exists():
        identity = _read_identity(path)
        if _is_legacy_ip_prefix_identity(identity):
            migrated = NodeIdentity(
                node_id=identity.node_id,
                display_name=_default_node_display_name(),
            )
            if migrated.display_name != identity.display_name:
                return _write_identity(path, migrated, replace_existing=True)
        return identity

    data_dir.mkdir(parents=True, exist_ok=True)
    identity = NodeIdentity(
        node_id=f"node_{uuid.uuid4().hex}",
        display_name=_default_node_display_name(),
    )
    return _write_identity(path, identity, replace_existing=False)
