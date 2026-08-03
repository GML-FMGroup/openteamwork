"""Caller-aware validation and execution for registered product Actions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from .models import ActionContext, ActionError, ActionFailure, ActionOutcome
from .registry import ActionRegistry


class ActionExecutor:
    """Validate, authorize, confirm, and invoke Actions in one deterministic order."""

    def __init__(self, registry: ActionRegistry) -> None:
        self.registry = registry

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
            return ActionOutcome.failure(
                action_id,
                ActionError(
                    "action_unavailable",
                    "The requested Action is not currently available.",
                    details={"reason": availability_reason},
                ),
            )
        missing_capabilities = sorted(spec.required_capabilities.difference(context.capabilities))
        if missing_capabilities:
            return ActionOutcome.failure(
                action_id,
                ActionError(
                    "capability_required",
                    "The caller does not provide a required capability.",
                    details={"capabilities": missing_capabilities},
                ),
            )
        if spec.permission not in context.permissions:
            return ActionOutcome.failure(
                action_id,
                ActionError(
                    "permission_denied",
                    "The caller is not permitted to execute this Action.",
                    details={"permission": spec.permission},
                ),
            )
        if spec.confirmation == "required" and not context.confirmed:
            return ActionOutcome.failure(
                action_id,
                ActionError(
                    "confirmation_required",
                    "The Action requires explicit confirmation.",
                    details={"risk": spec.risk},
                ),
            )
        try:
            input_data = spec.input_model.model_validate(dict(raw_input), strict=True)
        except ValidationError as exc:
            return ActionOutcome.failure(
                action_id,
                ActionError(
                    "invalid_action_input",
                    "The Action input does not match its schema.",
                    details={"issues": self._validation_issues(exc)},
                ),
            )
        try:
            raw_result = registered.handler(context, input_data)
            result = self._json_object(raw_result)
        except ActionFailure as exc:
            return ActionOutcome.failure(action_id, exc.error)
        except Exception:
            return ActionOutcome.failure(
                action_id,
                ActionError("internal_error", "The Action could not be completed."),
            )
        return ActionOutcome.success(action_id, result)

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
