"""Stable content revisions for validated configuration resources."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel


def config_revision(document: BaseModel) -> str:
    """Return a canonical SHA-256 revision for one validated resource."""
    payload = json.dumps(
        document.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
