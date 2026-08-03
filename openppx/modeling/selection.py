"""Deterministic Model Profile readiness checks and fallback selection."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from openppx.config.diagnostics import ConfigLoadError
from openppx.config.models import AgentConfig, ResourceName
from openppx.config.secrets import SecretStatus, SecretStore

from .catalog import ModelCatalog
from .profiles import ModelProfile
from .repository import ModelProfileRepository


PrivacyRequirement = Literal["any", "local_only"]
SelectionSource = Literal["run_override", "agent_role", "agent_default", "fallback"]


@dataclass(frozen=True, slots=True)
class ModelRequirements:
    """Conservative task constraints applied before a profile is selected."""

    required_capabilities: frozenset[str] = frozenset()
    privacy: PrivacyRequirement = "any"
    min_context_tokens: int | None = None
    max_input_cost_per_million_usd: Decimal | None = None
    max_output_cost_per_million_usd: Decimal | None = None

    def __init__(
        self,
        required_capabilities: set[str] | frozenset[str] | None = None,
        privacy: PrivacyRequirement = "any",
        min_context_tokens: int | None = None,
        max_input_cost_per_million_usd: Decimal | None = None,
        max_output_cost_per_million_usd: Decimal | None = None,
    ) -> None:
        object.__setattr__(self, "required_capabilities", frozenset(required_capabilities or ()))
        object.__setattr__(self, "privacy", privacy)
        object.__setattr__(self, "min_context_tokens", min_context_tokens)
        object.__setattr__(self, "max_input_cost_per_million_usd", max_input_cost_per_million_usd)
        object.__setattr__(self, "max_output_cost_per_million_usd", max_output_cost_per_million_usd)
        if privacy not in {"any", "local_only"}:
            raise ValueError("privacy must be any or local_only")
        if min_context_tokens is not None and min_context_tokens < 1:
            raise ValueError("min_context_tokens must be positive")
        for cost in (max_input_cost_per_million_usd, max_output_cost_per_million_usd):
            if cost is not None and cost < 0:
                raise ValueError("maximum cost must be non-negative")


@dataclass(frozen=True, slots=True)
class ModelSelectionAttempt:
    """Non-sensitive reason one candidate could not be selected."""

    profile_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ModelResolution:
    """Selected profile plus provenance and prior redacted attempts."""

    profile_id: ResourceName
    profile: ModelProfile
    revision: str
    provider: str
    model: str
    secret_status: SecretStatus | None
    selection_source: SelectionSource
    attempts: tuple[ModelSelectionAttempt, ...] = ()


class ModelSelectionError(RuntimeError):
    """Raised when no configured profile satisfies readiness requirements."""

    def __init__(self, attempts: tuple[ModelSelectionAttempt, ...]) -> None:
        self.attempts = attempts
        reasons = ", ".join(f"{attempt.profile_id}:{attempt.reason}" for attempt in attempts)
        super().__init__(f"No ready Model Profile ({reasons})")


class ModelProfileSelector:
    """Resolve run/role/default assignments through explicit ordered fallbacks."""

    def __init__(
        self,
        repository: ModelProfileRepository,
        catalog: ModelCatalog,
        secrets: SecretStore,
    ) -> None:
        self.repository = repository
        self.catalog = catalog
        self.secrets = secrets

    def select(
        self,
        agent: AgentConfig,
        *,
        role: str | None = None,
        run_override: str | None = None,
        requirements: ModelRequirements | None = None,
    ) -> ModelResolution:
        """Select one ready profile without mutating Agent policy or process state."""
        profile_id, source = self._assignment(agent, role=role, run_override=run_override)
        if profile_id is None:
            raise ModelSelectionError((ModelSelectionAttempt("unassigned", "profile_missing"),))

        attempts: list[ModelSelectionAttempt] = []
        resolution = self._select_candidate(
            profile_id,
            source=source,
            requirements=requirements or ModelRequirements(),
            attempts=attempts,
            active_path=(),
            visited=set(),
        )
        if resolution is None:
            raise ModelSelectionError(tuple(attempts))
        return resolution

    @staticmethod
    def _assignment(
        agent: AgentConfig,
        *,
        role: str | None,
        run_override: str | None,
    ) -> tuple[str | None, SelectionSource]:
        if run_override is not None:
            return run_override, "run_override"
        role_profile = agent.spec.model_policy.role_profiles.get(role) if role is not None else None  # type: ignore[arg-type]
        if role_profile is not None:
            return role_profile, "agent_role"
        return agent.spec.model_policy.default_profile, "agent_default"

    def _select_candidate(
        self,
        profile_id: str,
        *,
        source: SelectionSource,
        requirements: ModelRequirements,
        attempts: list[ModelSelectionAttempt],
        active_path: tuple[str, ...],
        visited: set[str],
    ) -> ModelResolution | None:
        if profile_id in active_path:
            attempts.append(ModelSelectionAttempt(profile_id, "fallback_cycle"))
            return None
        if profile_id in visited:
            attempts.append(ModelSelectionAttempt(profile_id, "fallback_duplicate"))
            return None
        visited.add(profile_id)
        try:
            resource = self.repository.read_profile(profile_id)
        except ConfigLoadError as exc:
            if exc.kind == "not_found":
                attempts.append(ModelSelectionAttempt(profile_id, "profile_missing"))
                return None
            raise

        reason, secret_status = self._readiness(resource.document, requirements)
        if reason is None:
            return ModelResolution(
                profile_id=resource.document.metadata.name,
                profile=resource.document,
                revision=resource.revision,
                provider=resource.document.spec.provider,
                model=resource.document.spec.model,
                secret_status=secret_status,
                selection_source=source,
                attempts=tuple(attempts),
            )

        attempts.append(ModelSelectionAttempt(profile_id, reason))
        path = (*active_path, profile_id)
        for fallback_id in resource.document.spec.fallback_profiles:
            resolution = self._select_candidate(
                fallback_id,
                source="fallback",
                requirements=requirements,
                attempts=attempts,
                active_path=path,
                visited=visited,
            )
            if resolution is not None:
                return resolution
        return None

    def _readiness(
        self,
        profile: ModelProfile,
        requirements: ModelRequirements,
    ) -> tuple[str | None, SecretStatus | None]:
        spec = profile.spec
        if not spec.enabled:
            return "disabled", None
        provider = self.catalog.get(spec.provider)
        if provider is None:
            return "provider_unknown", None
        if provider.runtime == "unsupported":
            return "provider_unsupported", None
        missing = requirements.required_capabilities.difference(spec.capabilities)
        if missing:
            return "capability_missing", None
        if requirements.privacy == "local_only" and spec.execution_location != "local":
            return "privacy_mismatch", None
        if requirements.min_context_tokens is not None:
            if spec.context_window_tokens is None:
                return "context_unknown", None
            if spec.context_window_tokens < requirements.min_context_tokens:
                return "context_insufficient", None
        if requirements.max_input_cost_per_million_usd is not None:
            if spec.input_cost_per_million_usd is None:
                return "cost_unknown", None
            if spec.input_cost_per_million_usd > requirements.max_input_cost_per_million_usd:
                return "cost_exceeded", None
        if requirements.max_output_cost_per_million_usd is not None:
            if spec.output_cost_per_million_usd is None:
                return "cost_unknown", None
            if spec.output_cost_per_million_usd > requirements.max_output_cost_per_million_usd:
                return "cost_exceeded", None
        if spec.credential is None:
            return ("credential_missing", None) if provider.credential_required else (None, None)
        secret_status = self.secrets.status(spec.credential)
        if secret_status.state == "missing":
            return "secret_missing", secret_status
        if secret_status.state == "backend_unavailable":
            return "secret_backend_unavailable", secret_status
        return None, secret_status
