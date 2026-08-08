"""One explicit Action policy evaluator for every control surface."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openppx.actions.models import ActionContext, ActionSpec

from .models import PolicyContext, PolicyDecision


class ActionPolicy:
    """Authorize user-facing Actions independently of Agent execution presets.

    This policy evaluates authenticated caller capabilities, permissions, scope,
    and confirmation. Agent static permissions apply later when an Agent invokes
    a Tool or a Tool reaches a protected resource.
    """

    def evaluate(
        self,
        spec: "ActionSpec",
        context: "ActionContext",
        raw_input: Mapping[str, object],
    ) -> tuple[PolicyContext, PolicyDecision]:
        """Return immutable facts and one stable authorization decision."""
        policy_context = PolicyContext.from_action(spec, context, raw_input)
        missing_capabilities = sorted(spec.required_capabilities.difference(context.capabilities))
        if missing_capabilities:
            return policy_context, PolicyDecision(
                False,
                "capability_required",
                "The caller does not provide a required capability.",
                (("capabilities", missing_capabilities),),
            )
        if spec.permission not in context.permissions:
            return policy_context, PolicyDecision(
                False,
                "permission_denied",
                "The caller is not permitted to execute this Action.",
                (("permission", spec.permission),),
            )
        mismatch = _scope_mismatch(policy_context, context)
        if mismatch is not None:
            return policy_context, PolicyDecision(
                False,
                "scope_mismatch",
                "The requested resource is outside the caller's bound scope.",
                (("scope", mismatch),),
            )
        if spec.confirmation == "required" and not context.confirmed:
            return policy_context, PolicyDecision(
                False,
                "confirmation_required",
                "The Action requires explicit confirmation.",
                (("risk", spec.risk),),
            )
        return policy_context, PolicyDecision(True, "allowed", "Action policy allows execution.")


def _scope_mismatch(policy_context: PolicyContext, context: "ActionContext") -> str | None:
    requested = dict(policy_context.requested_scope)
    for field_name in ("node_id", "agent_id", "session_id", "run_id", "task_id"):
        bound = getattr(context, field_name)
        target = requested.get(field_name)
        if bound is not None and target is not None and bound != target:
            return field_name.removesuffix("_id")
    return None


__all__ = ["ActionPolicy"]
