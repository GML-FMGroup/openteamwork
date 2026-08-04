"""Safe product lifecycle tests for Model Profile creation and editing."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from openppx.config import ConfigRevisionConflict, InMemorySecretStore, SecretRef, SecretValue
from openppx.modeling import ModelCatalog, ModelProfileLifecycleError, ModelProfileLifecycleService, ModelProfileRepository


class RecordingSecretStore(InMemorySecretStore):
    """In-memory SecretStore that records mutation boundaries without values."""

    def __init__(self) -> None:
        super().__init__()
        self.put_refs: list[SecretRef] = []
        self.deleted_refs: list[SecretRef] = []

    def put(self, ref: SecretRef, value: SecretValue):
        self.put_refs.append(ref)
        return super().put(ref, value)

    def delete(self, ref: SecretRef):
        self.deleted_refs.append(ref)
        return super().delete(ref)


def service(
    tmp_path: Path,
    *,
    profile_ids: list[str] | None = None,
) -> tuple[ModelProfileLifecycleService, RecordingSecretStore]:
    secrets = RecordingSecretStore()
    generated_ids = iter(profile_ids or ["model-generated-primary", "model-generated-secondary"])
    return (
        ModelProfileLifecycleService(
            ModelProfileRepository(tmp_path),
            ModelCatalog(codex_home=tmp_path / "codex-home"),
            secrets,
            profile_id_factory=lambda: next(generated_ids),
        ),
        secrets,
    )


def profile_fields(
    *,
    display_name: str = "Primary",
    provider_id: str = "openai",
    api_key: str | None = "secret-never-project",
    model: str = "openai/gpt-5.4",
) -> dict[str, object]:
    return {
        "display_name": display_name,
        "provider_id": provider_id,
        "model": model,
        "execution_location": "remote",
        "api_base": "http://127.0.0.1:8000/v1" if provider_id == "openai" else None,
        "capabilities": ["text", "tool_calling"],
        "context_window_tokens": 128_000,
        "input_cost_per_million_usd": None,
        "output_cost_per_million_usd": None,
        "fallback_profile_ids": [],
        "enabled": True,
        "api_key": SecretStr(api_key) if api_key is not None else None,
    }


def create_profile(
    lifecycle: ModelProfileLifecycleService,
    **overrides: object,
):
    fields = profile_fields(**overrides)
    return lifecycle.create(**fields)


def update_profile(lifecycle: ModelProfileLifecycleService, created, **overrides: object):
    fields = profile_fields(**overrides)
    return lifecycle.update(
        profile_id=created.profile.document.metadata.name,
        expected_revision=created.profile.revision,
        **fields,
    )


def test_create_rotates_fresh_secret_and_returns_only_readiness(tmp_path: Path) -> None:
    lifecycle, secrets = service(tmp_path)

    result = create_profile(lifecycle)

    credential = result.profile.document.spec.credential
    assert credential is not None
    assert result.profile.document.metadata.name == "model-generated-primary"
    assert result.profile.document.spec.display_name == "Primary"
    assert credential.name.startswith("model-model-generated-primary-")
    assert result.profile.document.spec.api_base == "http://127.0.0.1:8000/v1"
    assert result.credential_state == "available"
    assert secrets.resolve(credential).reveal() == "secret-never-project"


def test_edit_without_new_key_reuses_ready_provider_credential(tmp_path: Path) -> None:
    lifecycle, secrets = service(tmp_path)
    created = create_profile(lifecycle)
    original_ref = created.profile.document.spec.credential

    updated = update_profile(
        lifecycle,
        created,
        api_key=None,
        model="openai/gpt-5.5",
        display_name="Daily work",
    )

    assert updated.profile.document.metadata.name == created.profile.document.metadata.name
    assert updated.profile.document.spec.display_name == "Daily work"
    assert updated.profile.document.spec.credential == original_ref
    assert len(secrets.put_refs) == 1


def test_revision_conflict_precedes_secret_mutation(tmp_path: Path) -> None:
    lifecycle, secrets = service(tmp_path)
    created = create_profile(lifecycle)
    before = list(secrets.put_refs)

    with pytest.raises(ConfigRevisionConflict):
        lifecycle.update(
            profile_id=created.profile.document.metadata.name,
            expected_revision="sha256:" + "0" * 64,
            **profile_fields(api_key=None),
        )

    assert secrets.put_refs == before


def test_invalid_profile_precedes_secret_mutation(tmp_path: Path) -> None:
    lifecycle, secrets = service(tmp_path)

    with pytest.raises(ModelProfileLifecycleError) as rejected:
        lifecycle.create(
            **{
                **profile_fields(),
                "api_base": "https://api.example.com/v1?token=unsafe",
            }
        )

    assert rejected.value.code == "invalid_profile"
    assert secrets.put_refs == []


def test_failed_profile_publication_discards_fresh_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lifecycle, secrets = service(tmp_path)

    def fail_write(*_args, **_kwargs):
        raise RuntimeError("profile publication fixture")

    monkeypatch.setattr(lifecycle.profiles, "write_profile", fail_write)
    with pytest.raises(RuntimeError, match="publication fixture"):
        create_profile(lifecycle)

    assert len(secrets.put_refs) == 1
    assert secrets.deleted_refs == secrets.put_refs
    assert secrets.status(secrets.put_refs[0]).state == "missing"


def test_oauth_provider_rejects_api_key_and_persists_without_credential(tmp_path: Path) -> None:
    lifecycle, _secrets = service(tmp_path)

    with pytest.raises(ModelProfileLifecycleError) as rejected:
        create_profile(
            lifecycle,
            provider_id="openai_codex",
            api_key="wrong-boundary",
            model="openai-codex/gpt-5.5",
        )
    created = create_profile(
        lifecycle,
        provider_id="openai_codex",
        api_key=None,
        model="openai-codex/gpt-5.5",
    )

    assert rejected.value.code == "credential_not_supported"
    assert created.profile.document.spec.credential is None
    assert created.credential_state == "not_required"


def test_duplicate_display_name_is_case_insensitive_and_precedes_secret_mutation(tmp_path: Path) -> None:
    lifecycle, secrets = service(tmp_path, profile_ids=["model-one", "model-two"])
    create_profile(lifecycle, display_name="Coding")
    before = list(secrets.put_refs)

    with pytest.raises(ModelProfileLifecycleError) as rejected:
        create_profile(lifecycle, display_name="  coding  ")

    assert rejected.value.code == "profile_name_conflict"
    assert secrets.put_refs == before


def test_generated_id_collision_is_retried_without_overwriting(tmp_path: Path) -> None:
    lifecycle, _secrets = service(
        tmp_path,
        profile_ids=["model-collision", "model-collision", "model-distinct"],
    )
    first = create_profile(lifecycle, display_name="First")
    second = create_profile(lifecycle, display_name="Second")

    assert first.profile.document.metadata.name == "model-collision"
    assert second.profile.document.metadata.name == "model-distinct"
