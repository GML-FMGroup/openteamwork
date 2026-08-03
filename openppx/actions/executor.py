"""Caller-aware validation and execution for registered product Actions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from openppx.governance import ActionAuditSink, ActionPolicy, NullActionAuditSink, PolicyDecision

from .models import ActionContext, ActionError, ActionFailure, ActionOutcome
from .registry import ActionRegistry


class ActionExecutor:
    """Validate, authorize, confirm, and invoke Actions in one deterministic order."""

    def __init__(
        self,
        registry: ActionRegistry,
        *,
        policy: ActionPolicy | None = None,
        audit: ActionAuditSink | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or ActionPolicy()
        self.audit = audit or NullActionAuditSink()

    def execute(
        self,
        action_id: str,
        raw_input: Mapping[str, object],
        context: ActionContext,
    ) -> ActionOutcome:
        """Execute one Action and convert every failure to a redacted stable result."""
        registered = self.registry.resolve(action_id)
        if registered is None:
            return ActionOutcome.failure(
                action_id,
                ActionError("action_not_found", "The requested Action is not registered."),
            )
        spec = registered.spec
        availability_reason = self.registry.availability_reason(registered, context)
        if availability_reason is not None and availability_reason not in {
            "capability_required",
            "permission_denied",
        }:
            policy_context, _ = self.policy.evaluate(spec, context, raw_input)
            outcome = ActionOutcome.failure(
                action_id,
                ActionError(
                    "action_unavailable",
                    "The requested Action is not currently available.",
                    details={"reason": availability_reason},
                ),
            )
            self._audit_denial(
                policy_context,
                PolicyDecision(False, "action_unavailable", "The Action is not currently available."),
                outcome,
            )
            return outcome
        policy_context, decision = self.policy.evaluate(spec, context, raw_input)
        if not decision.allow:
            outcome = ActionOutcome.failure(
                action_id,
                ActionError(
                    decision.code,
                    decision.reason,
                    details=decision.detail_dict(),
                ),
            )
            self._audit_denial(policy_context, decision, outcome)
            return outcome
        audit_id = self._begin_audit(policy_context, decision)
        if audit_id is _AUDIT_FAILED and spec.risk == "high":
            return ActionOutcome.failure(
                action_id,
                ActionError(
                    "audit_unavailable",
                    "The high-risk Action cannot run while audit storage is unavailable.",
                ),
            )
        resolved_audit_id = audit_id if isinstance(audit_id, str) else None
        try:
            input_data = spec.input_model.model_validate(dict(raw_input), strict=True)
        except ValidationError as exc:
            outcome = ActionOutcome.failure(
                action_id,
                ActionError(
                    "invalid_action_input",
                    "The Action input does not match its schema.",
                    details={"issues": self._validation_issues(exc)},
                ),
            )
            self._complete_audit(resolved_audit_id, outcome)
            return outcome
        try:
            raw_result = registered.handler(context, input_data)
            result = self._json_object(raw_result)
        except ActionFailure as exc:
            outcome = ActionOutcome.failure(action_id, exc.error)
        except Exception:
            outcome = ActionOutcome.failure(
                action_id,
                ActionError("internal_error", "The Action could not be completed."),
            )
        else:
            outcome = ActionOutcome.success(action_id, result)
        self._complete_audit(resolved_audit_id, outcome)
        return outcome

    def _audit_denial(self, policy_context, decision: PolicyDecision, outcome: ActionOutcome) -> None:
        """Best-effort persist a denied Action without changing its stable denial."""
        audit_id = self._begin_audit(policy_context, decision)
        if isinstance(audit_id, str):
            self._complete_audit(audit_id, outcome)

    def _begin_audit(self, policy_context, decision: PolicyDecision) -> str | object | None:
        """Begin an audit record while isolating storage failures from normal reads."""
        try:
            return self.audit.begin(policy_context, decision)
        except Exception:
            return _AUDIT_FAILED

    def _complete_audit(self, audit_id: str | None, outcome: ActionOutcome) -> None:
        """Complete an audit fact without replacing the business outcome on failure."""
        try:
            self.audit.complete(audit_id, outcome)
        except Exception:
            return

    @staticmethod
    def _validation_issues(exc: ValidationError) -> list[dict[str, object]]:
        """Project validation errors without rejected values or exception context."""
        return [
            {
                "path": list(error["loc"]),
                "code": str(error["type"]),
                "message": str(error["msg"]),
            }
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        ]

    @staticmethod
    def _json_object(value: object) -> dict[str, Any]:
        """Normalize handler output while requiring an object-shaped result."""
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json", by_alias=True)
        if isinstance(value, Mapping):
            return dict(value)
        raise TypeError("Action handlers must return an object-shaped result")


_AUDIT_FAILED = object()
