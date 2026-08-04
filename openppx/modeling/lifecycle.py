"""Product Model Profile persistence with safe credential rotation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from secrets import token_hex
from typing import Callable

from pydantic import SecretStr, ValidationError

from openppx.config.diagnostics import ConfigLoadError, ConfigRevisionConflict, ConfigWriteError
from openppx.config.repository import VersionedResource
from openppx.config.secrets import SecretError, SecretRef, SecretStore, SecretValue

from .catalog import ModelCatalog
from .profiles import ModelCapability, ModelProfile
from .repository import ModelProfileRepository


class ModelProfileLifecycleError(RuntimeError):
    """Stable product error raised before a Model Profile mutation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ModelProfileSaveResult:
    """Saved Profile plus its non-sensitive credential readiness."""

    profile: VersionedResource[ModelProfile]
    credential_state: str


class ModelProfileLifecycleService:
    """Create or edit Model Profiles without exposing unsafe Secret transitions."""

    def __init__(
        self,
        profiles: ModelProfileRepository,
        catalog: ModelCatalog,
        secrets: SecretStore,
        *,
        profile_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.profiles = profiles
        self.catalog = catalog
        self.secrets = secrets
        self.profile_id_factory = profile_id_factory or (lambda: f"model-{token_hex(10)}")

    def create(
        self,
        *,
        display_name: str,
        provider_id: str,
        model: str,
        execution_location: str,
        api_base: str | None,
        capabilities: list[ModelCapability],
        context_window_tokens: int | None,
        input_cost_per_million_usd: Decimal | None,
        output_cost_per_million_usd: Decimal | None,
        fallback_profile_ids: list[str],
        enabled: bool,
        api_key: SecretStr | None,
    ) -> ModelProfileSaveResult:
        """Generate an immutable Profile identity and publish one new named policy."""
        self._require_unique_display_name(display_name)
        profile_id = self._generate_profile_id()
        return self._persist(
            profile_id=profile_id,
            display_name=display_name,
            provider_id=provider_id,
            model=model,
            execution_location=execution_location,
            api_base=api_base,
            capabilities=capabilities,
            context_window_tokens=context_window_tokens,
            input_cost_per_million_usd=input_cost_per_million_usd,
            output_cost_per_million_usd=output_cost_per_million_usd,
            fallback_profile_ids=fallback_profile_ids,
            enabled=enabled,
            api_key=api_key,
            current=None,
            expected_revision=None,
        )

    def update(
        self,
        *,
        profile_id: str,
        display_name: str,
        provider_id: str,
        model: str,
        execution_location: str,
        api_base: str | None,
        capabilities: list[ModelCapability],
        context_window_tokens: int | None,
        input_cost_per_million_usd: Decimal | None,
        output_cost_per_million_usd: Decimal | None,
        fallback_profile_ids: list[str],
        enabled: bool,
        api_key: SecretStr | None,
        expected_revision: str,
    ) -> ModelProfileSaveResult:
        """Edit one named Profile while preserving its immutable resource identity."""
        current = self.profiles.read_profile(profile_id)
        if current.revision != expected_revision:
            raise ConfigRevisionConflict(
                self.profiles.paths.model_profile_file(profile_id),
                source=f"model-profile:{profile_id}",
                expected_revision=expected_revision,
                actual_revision=current.revision,
            )
        self._require_unique_display_name(display_name, excluding_profile_id=profile_id)
        return self._persist(
            profile_id=profile_id,
            display_name=display_name,
            provider_id=provider_id,
            model=model,
            execution_location=execution_location,
            api_base=api_base,
            capabilities=capabilities,
            context_window_tokens=context_window_tokens,
            input_cost_per_million_usd=input_cost_per_million_usd,
            output_cost_per_million_usd=output_cost_per_million_usd,
            fallback_profile_ids=fallback_profile_ids,
            enabled=enabled,
            api_key=api_key,
            current=current,
            expected_revision=expected_revision,
        )

    def _persist(
        self,
        *,
        profile_id: str,
        display_name: str,
        provider_id: str,
        model: str,
        execution_location: str,
        api_base: str | None,
        capabilities: list[ModelCapability],
        context_window_tokens: int | None,
        input_cost_per_million_usd: Decimal | None,
        output_cost_per_million_usd: Decimal | None,
        fallback_profile_ids: list[str],
        enabled: bool,
        api_key: SecretStr | None,
        current: VersionedResource[ModelProfile] | None,
        expected_revision: str | None,
    ) -> ModelProfileSaveResult:
        """Validate access, rotate credentials safely, and atomically publish one Profile."""

        provider = self.catalog.get(provider_id)
        if provider is None or provider.runtime == "unsupported":
            raise ModelProfileLifecycleError(
                "provider_not_supported",
                "The selected model provider is not supported.",
            )
        snapshot = self.catalog.list_models(provider_id)
        if snapshot.authoritative and model not in {item.model_id for item in snapshot.models}:
            raise ModelProfileLifecycleError(
                "model_not_available",
                "The selected model is not advertised by this provider on the Node.",
            )
        if api_base is not None and provider.runtime not in {"litellm", "codex"}:
            raise ModelProfileLifecycleError(
                "api_base_not_supported",
                "This provider does not support a custom API Base.",
            )
        for fallback_profile_id in fallback_profile_ids:
            if fallback_profile_id == profile_id:
                continue
            try:
                self.profiles.read_profile(fallback_profile_id)
            except ConfigLoadError as exc:
                if exc.kind == "not_found":
                    raise ModelProfileLifecycleError(
                        "fallback_profile_not_found",
                        "A selected fallback Model Profile does not exist.",
                    ) from None
                raise

        new_secret_ref: SecretRef | None = None
        credential = None
        provided_secret = api_key.get_secret_value() if api_key is not None else None
        if provided_secret is not None and not provided_secret.strip():
            provided_secret = None
        if provider.credential_mode == "api_key":
            if provided_secret:
                new_secret_ref = SecretRef(
                    store="system",
                    name=f"model-{profile_id[:38]}-{token_hex(8)}",
                )
                credential = new_secret_ref
            elif (
                current is not None
                and current.document.spec.provider == provider_id
                and current.document.spec.credential is not None
                and self.secrets.status(current.document.spec.credential).state == "available"
            ):
                credential = current.document.spec.credential
            else:
                raise ModelProfileLifecycleError(
                    "credential_required",
                    "The selected provider requires an API key.",
                )
        elif provided_secret:
            raise ModelProfileLifecycleError(
                "credential_not_supported",
                "This provider does not accept an API key in a Model Profile.",
            )

        try:
            candidate = ModelProfile.model_validate(
                {
                    "apiVersion": "openppx.io/v1alpha1",
                    "kind": "ModelProfile",
                    "metadata": {"name": profile_id},
                    "spec": {
                        "displayName": display_name,
                        "provider": provider_id,
                        "model": model,
                        "credential": credential.model_dump(mode="json", by_alias=True) if credential else None,
                        "executionLocation": execution_location,
                        "apiBase": api_base,
                        "capabilities": capabilities,
                        "contextWindowTokens": context_window_tokens,
                        "inputCostPerMillionUsd": (
                            str(input_cost_per_million_usd)
                            if input_cost_per_million_usd is not None
                            else None
                        ),
                        "outputCostPerMillionUsd": (
                            str(output_cost_per_million_usd)
                            if output_cost_per_million_usd is not None
                            else None
                        ),
                        "fallbackProfiles": fallback_profile_ids,
                        "enabled": enabled,
                    },
                }
            )
        except ValidationError:
            raise ModelProfileLifecycleError(
                "invalid_profile",
                "The Model Profile configuration is not valid.",
            ) from None

        if new_secret_ref is not None:
            try:
                self.secrets.put(new_secret_ref, SecretValue(provided_secret or ""))
            except SecretError:
                raise ModelProfileLifecycleError(
                    "secret_backend_unavailable",
                    "The protected credential could not be persisted.",
                ) from None
        try:
            resource = self.profiles.write_profile(
                profile_id,
                candidate,
                expected_revision=expected_revision,
            )
        except Exception as exc:
            if new_secret_ref is not None:
                self._discard_unpublished_secret(new_secret_ref)
            if isinstance(exc, ConfigWriteError) and exc.kind == "name_conflict":
                raise ModelProfileLifecycleError(
                    "profile_name_conflict",
                    "A Model Profile with this name already exists on the Node.",
                ) from None
            raise
        credential_state = (
            self.secrets.status(resource.document.spec.credential).state
            if resource.document.spec.credential is not None
            else "not_required"
        )
        return ModelProfileSaveResult(resource, credential_state)

    def _generate_profile_id(self) -> str:
        """Return a fresh Node-generated resource identifier with bounded collision retries."""
        for _attempt in range(8):
            profile_id = self.profile_id_factory()
            if self._optional_profile(profile_id) is None:
                return profile_id
        raise ModelProfileLifecycleError(
            "profile_id_generation_failed",
            "A unique Model Profile identifier could not be generated.",
        )

    def _require_unique_display_name(
        self,
        display_name: str,
        *,
        excluding_profile_id: str | None = None,
    ) -> None:
        """Keep user-facing Profile names unambiguous within one Node."""
        candidate = display_name.strip().casefold()
        for profile_id in self.profiles.list_profile_ids():
            if profile_id == excluding_profile_id:
                continue
            if self.profiles.read_profile(profile_id).document.spec.display_name.strip().casefold() == candidate:
                raise ModelProfileLifecycleError(
                    "profile_name_conflict",
                    "A Model Profile with this name already exists on the Node.",
                )

    def _optional_profile(self, profile_id: str) -> VersionedResource[ModelProfile] | None:
        try:
            return self.profiles.read_profile(profile_id)
        except ConfigLoadError as exc:
            if exc.kind == "not_found":
                return None
            raise

    def _discard_unpublished_secret(self, ref: SecretRef) -> None:
        """Best-effort cleanup of a fresh SecretRef that no Profile references."""
        try:
            self.secrets.delete(ref)
        except SecretError:
            pass
