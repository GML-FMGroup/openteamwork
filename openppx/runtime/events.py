"""Transport-neutral runtime event values used by optional event sinks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RuntimeFeedbackEvent:
    """One runtime event addressed to an opaque transport route."""

    route: str
    scope_id: str
    content: str
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
