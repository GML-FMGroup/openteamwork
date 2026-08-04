"""Explicit Model Profile to ADK adapter boundary tests."""

from __future__ import annotations

import os

from openppx.config import InMemorySecretStore, SecretRef, SecretValue
from openppx.modeling import ModelProfile
from openppx.modeling.selection import ModelResolution
from openppx.runtime.model_adapter_factory import ModelAdapterFactory


def _resolution(
    *,
    provider: str = "openai",
    model: str = "openai/gpt-test",
    api_base: str | None = None,
) -> ModelResolution:
    profile = ModelProfile.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "ModelProfile",
            "metadata": {"name": "primary"},
            "spec": {
                "displayName": "Primary",
                "provider": provider,
                "model": model,
                "credential": {"store": "system", "name": "primary-key"},
                "executionLocation": "remote",
                "apiBase": api_base,
                "capabilities": ["text", "tool_calling"],
                "fallbackProfiles": [],
                "enabled": True,
            },
        }
    )
    return ModelResolution(
        profile_id="primary",
        profile=profile,
        revision="sha256:" + "1" * 64,
        provider=provider,
        model=model,
        secret_status=None,
        selection_source="agent_default",
    )


def test_litellm_adapter_uses_explicit_secret_without_mutating_environment() -> None:
    secrets = InMemorySecretStore()
    ref = SecretRef(store="system", name="primary-key")
    secrets.put(ref, SecretValue("model-secret-never-log"))
    before = dict(os.environ)

    model = ModelAdapterFactory(secrets).build(_resolution())

    assert model.model == "openai/gpt-test"
    assert dict(os.environ) == before
    assert "model-secret-never-log" not in repr(model)


def test_litellm_adapter_prefers_profile_api_base() -> None:
    secrets = InMemorySecretStore()
    ref = SecretRef(store="system", name="primary-key")
    secrets.put(ref, SecretValue("model-secret-never-log"))

    model = ModelAdapterFactory(secrets).build(
        _resolution(api_base="http://127.0.0.1:8000/v1")
    )

    assert model._additional_args["api_base"] == "http://127.0.0.1:8000/v1"


def test_google_adapter_injects_a_client_instead_of_provider_environment() -> None:
    secrets = InMemorySecretStore()
    ref = SecretRef(store="system", name="primary-key")
    secrets.put(ref, SecretValue("google-secret-never-log"))
    before = dict(os.environ)

    model = ModelAdapterFactory(secrets).build(
        _resolution(provider="google", model="gemini-test")
    )

    assert model.model == "gemini-test"
    assert "api_client" in model.__dict__
    assert dict(os.environ) == before
    assert "google-secret-never-log" not in repr(model)
