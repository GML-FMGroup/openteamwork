"""Secret-free observations for bounded Goal continuations.

The supervisor observes native Google ADK events. It does not schedule tools or
execute work; it only summarizes one invocation slice so the durable Goal store
can decide whether another bounded continuation is safe.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GoalSliceObservation:
    """One secret-free summary of actions emitted by an ADK invocation slice."""

    invocation_id: str
    action_names: tuple[str, ...]
    action_fingerprint: str


class GoalSliceObserver:
    """Collect function-call identities from native ADK events."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Start observing a fresh bounded ADK continuation slice."""
        self._invocation_id = ""
        self._actions: list[dict[str, str]] = []

    def observe(self, event: Any) -> None:
        """Observe one ADK event without retaining raw tool arguments."""
        payload = _event_payload(event)
        invocation_id = str(payload.get("invocation_id") or "").strip()
        if invocation_id:
            self._invocation_id = invocation_id
        content = payload.get("content")
        parts = content.get("parts") if isinstance(content, dict) else []
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, dict):
                continue
            call = part.get("function_call")
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "tool").strip() or "tool"
            args_digest = hashlib.sha256(
                json.dumps(
                    call.get("args") if isinstance(call.get("args"), dict) else {},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            self._actions.append({"name": name, "argsDigest": args_digest})

    def snapshot(self) -> GoalSliceObservation:
        """Return the current observation with only digests and action names."""
        if not self._actions:
            fingerprint = ""
        else:
            fingerprint = hashlib.sha256(
                json.dumps(
                    self._actions,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        return GoalSliceObservation(
            invocation_id=self._invocation_id,
            action_names=tuple(action["name"] for action in self._actions),
            action_fingerprint=fingerprint,
        )


def _event_payload(event: Any) -> dict[str, Any]:
    """Return one ADK event as a plain mapping for tolerant observation."""
    if isinstance(event, dict):
        return event
    model_dump = getattr(event, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
        return value if isinstance(value, dict) else {}
    return {}


__all__ = ["GoalSliceObservation", "GoalSliceObserver"]
