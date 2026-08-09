"""First-run setup and protected Secret Actions."""

from __future__ import annotations

import logging
from typing import cast

from openppx.actions import ActionError, ActionFailure, ActionRegistry, ActionSpec
from openppx.config import ConfigError, SecretError, SecretValue
from openppx.setup import SetupError, SetupService

from .errors import raise_config_failure
from .input_models import EmptyInput, SecretPutInput, SecretStatusInput, SetupApplyInput, SetupHelloInput


LOGGER = logging.getLogger(__name__)


def register_setup_actions(registry: ActionRegistry, service: SetupService) -> None:
    """Register setup status/apply and Secret lifecycle before Runtime exists."""
    registry.register(
        _spec("setup.status", "Setup status", "Inspect first-run readiness and provider choices.", EmptyInput, "setup.read"),
        lambda _context, _input: service.status(),
    )
    registry.register(
        _spec("setup.apply", "Apply setup", "Apply one complete Node, Agent, Model, and Secret baseline.", SetupApplyInput, "setup.write", risk="medium"),
        lambda _context, input_data: _apply(service, cast(SetupApplyInput, input_data)),
    )
    registry.register(
        _spec("secret.status", "Secret status", "Inspect protected credential availability.", SecretStatusInput, "secret.read"),
        lambda _context, input_data: _secret_status(service, cast(SecretStatusInput, input_data)),
    )
    registry.register(
        _spec("secret.put", "Save Secret", "Persist protected credential material in the system credential store.", SecretPutInput, "secret.write", risk="medium"),
        lambda _context, input_data: _secret_put(service, cast(SecretPutInput, input_data)),
    )
    registry.register(
        _spec("secret.delete", "Delete Secret", "Remove protected credential material from the system credential store.", SecretStatusInput, "secret.write", risk="high", confirmation="required"),
        lambda _context, input_data: _secret_delete(service, cast(SecretStatusInput, input_data)),
    )


def register_setup_runtime_actions(registry: ActionRegistry, service: SetupService, supervisor) -> None:
    """Register the first real Hello only after the Runtime owner is attached."""
    registry.register(
        _spec("setup.hello", "Run first Hello", "Create a Session and complete one real ADK turn.", SetupHelloInput, "run.start", execution="long_running"),
        lambda _context, input_data: _hello(service, supervisor, cast(SetupHelloInput, input_data)),
    )


def _spec(
    action_id: str,
    title: str,
    description: str,
    input_model,
    permission: str,
    *,
    risk: str = "low",
    confirmation: str = "never",
    execution: str = "sync",
) -> ActionSpec:
    return ActionSpec(
        action_id=action_id,
        namespace=action_id.split(".", 1)[0],
        title=title,
        description=description,
        input_model=input_model,
        scope="agent" if action_id == "setup.hello" else "node",
        required_capabilities=frozenset({permission}),
        permission=permission,
        risk=risk,
        confirmation=confirmation,
        execution=execution,
        operation=(
            "mutation"
            if action_id in {"setup.apply", "setup.hello", "secret.put", "secret.delete"}
            else "read"
        ),
        projections=("cli", "desktop", "mobile"),
    )


def _apply(service: SetupService, input_data: SetupApplyInput) -> dict[str, object]:
    try:
        result = service.apply(input_data.request)
    except ConfigError as exc:
        raise_config_failure(exc)
    except SetupError as exc:
        raise ActionFailure(ActionError(exc.code, str(exc), details=exc.details)) from None
    except SecretError:
        raise ActionFailure(ActionError("secret_backend_unavailable", "The protected credential could not be persisted.")) from None
    return {
        "state": "configured",
        "revisions": {
            "node": result.node_revision,
            "agent": result.agent_revision,
            "profile": result.profile_revision,
        },
        "secretState": result.secret_state,
        "restartRequired": result.restart_required,
    }


def _secret_projection(status) -> dict[str, object]:
    return {
        "ref": status.ref.model_dump(mode="json", by_alias=True),
        "state": status.state,
        "backend": status.backend,
    }


def _secret_status(service: SetupService, input_data: SecretStatusInput) -> dict[str, object]:
    return _secret_projection(service.secrets.status(input_data.ref))


def _secret_put(service: SetupService, input_data: SecretPutInput) -> dict[str, object]:
    try:
        status = service.secrets.put(input_data.ref, SecretValue(input_data.value.get_secret_value()))
    except SecretError:
        raise ActionFailure(ActionError("secret_backend_unavailable", "The protected credential could not be persisted.")) from None
    return _secret_projection(status)


def _secret_delete(service: SetupService, input_data: SecretStatusInput) -> dict[str, object]:
    try:
        status = service.secrets.delete(input_data.ref)
    except SecretError:
        raise ActionFailure(ActionError("secret_backend_unavailable", "The protected credential could not be removed.")) from None
    return _secret_projection(status)


def _hello(service: SetupService, supervisor, input_data: SetupHelloInput) -> dict[str, object]:
    if service.status()["state"] not in {"configured", "ready"}:
        raise ActionFailure(ActionError("setup_incomplete", "Setup must be configured before the first Hello."))
    try:
        session = supervisor.create_session_sync(input_data.agent_id, user_id=input_data.user_id)
        session_id = str(session.id)
    except Exception:
        LOGGER.exception(
            "Setup Hello failed while initializing a Session for Agent %s.",
            input_data.agent_id,
        )
        raise ActionFailure(_runtime_initialization_failure()) from None
    try:
        reply = supervisor.hello_sync(
            input_data.agent_id,
            input_data.text,
            user_id=input_data.user_id,
            session_id=session_id,
        )
    except Exception as exc:
        LOGGER.exception(
            "Setup Hello failed during the first model turn for Agent %s.",
            input_data.agent_id,
        )
        raise ActionFailure(_hello_failure(exc)) from None
    if not reply.strip():
        raise ActionFailure(ActionError("hello_failed", "The first model turn returned no reply."))
    try:
        service.mark_verified(session_id=session_id)
    except (ConfigError, SetupError):
        raise ActionFailure(ActionError("setup_verification_failed", "The successful Hello could not be verified.")) from None
    return {"sessionId": session_id, "reply": reply, "state": "ready"}


def _runtime_initialization_failure() -> ActionError:
    """Project a safe, actionable failure for pre-model Runtime initialization."""
    return ActionError(
        "runtime_initialization_failed",
        "The Agent runtime could not create the first Session. Restart the Node and retry.",
        details={"phase": "session_initialization"},
        retryable=True,
    )


def _hello_failure(exc: Exception) -> ActionError:
    """Project known safe model failures without exposing provider payloads."""
    message = str(exc).removeprefix("CODEX_ERROR:").strip()
    normalized = message.lower()
    if "codex" in normalized and any(term in normalized for term in ("authentication", "signed in", "login")):
        return ActionError("provider_authentication_failed", message)
    if "codex model" in normalized and any(term in normalized for term in ("not available", "not allowed")):
        return ActionError("model_not_available", message)
    if "codex account" in normalized and "not allowed" in normalized:
        return ActionError("provider_access_denied", message)
    if "codex quota" in normalized or "rate limited" in normalized:
        return ActionError("provider_rate_limited", message, retryable=True)
    if message in {
        "The Codex service is temporarily unavailable. Try again later.",
        "Codex response failed",
    }:
        return ActionError("provider_unavailable", message, retryable=True)
    if message.startswith("Codex rejected the model request (HTTP "):
        return ActionError("provider_request_rejected", message)
    return ActionError("hello_failed", "The first model turn did not complete.")
