"""Persistent identity for one OpenPPX Node installation."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
        raise ValueError(f"Invalid OpenPPX Node identity file: {path}")
    node_id = str(payload.get("node_id") or "").strip()
    display_name = str(payload.get("display_name") or "").strip()
    if not node_id.startswith("node_") or len(node_id) <= len("node_") or not display_name:
        raise ValueError(f"Invalid OpenPPX Node identity file: {path}")
    return NodeIdentity(node_id=node_id, display_name=display_name)


def _read_identity(path: Path) -> NodeIdentity:
    return _parse_identity(json.loads(path.read_text(encoding="utf-8")), path=path)


def load_or_create_node_identity(data_dir: Path) -> NodeIdentity:
    """Load the stable Node identity, creating it atomically on first use."""

    path = data_dir / _NODE_IDENTITY_FILE
    if path.exists():
        return _read_identity(path)

    data_dir.mkdir(parents=True, exist_ok=True)
    identity = NodeIdentity(
        node_id=f"node_{uuid.uuid4().hex}",
        display_name=socket.gethostname().split(".", 1)[0] or "OpenPPX Node",
    )
    payload = json.dumps(identity.as_dict(), ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=data_dir,
        prefix=f".{_NODE_IDENTITY_FILE}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # A hard link publishes only the complete file and never overwrites a competing creator.
            os.link(temporary_path, path)
        except FileExistsError:
            return _read_identity(path)
        return identity
    finally:
        temporary_path.unlink(missing_ok=True)
