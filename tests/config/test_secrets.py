"""Tests for the protected SecretRef and SecretStore boundary."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openppx.config import (
    InMemorySecretStore,
    SecretBackendUnavailable,
    SecretNotFound,
    SecretRef,
    SecretValue,
    SystemCredentialSecretStore,
)


class FakeCredentialAdapter:
    """Small keyring-compatible adapter for deterministic tests."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.values: dict[tuple[str, str], str] = {}

    def is_available(self) -> bool:
        return self.available

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


def test_secret_ref_is_strict_and_contains_no_value() -> None:
    ref = SecretRef.model_validate({"store": "system", "name": "openai-primary"})

    assert ref.store == "system"
    assert ref.name == "openai-primary"
    assert json.dumps(ref.model_dump(mode="json")) == '{"store": "system", "name": "openai-primary"}'

    with pytest.raises(ValidationError):
        SecretRef.model_validate({"store": "file", "name": "openai-primary"})
    with pytest.raises(ValidationError):
        SecretRef.model_validate({"store": "system", "name": "openai-primary", "value": "secret"})


def test_secret_value_string_representations_are_redacted() -> None:
    raw = "sk-never-render-this"
    secret = SecretValue(raw)

    assert raw not in str(secret)
    assert raw not in repr(secret)
    assert secret.reveal() == raw


def test_in_memory_secret_store_lifecycle() -> None:
    ref = SecretRef(store="system", name="openai-primary")
    store = InMemorySecretStore()

    assert store.status(ref).state == "missing"
    assert store.put(ref, SecretValue("sk-test")).state == "available"
    assert store.status(ref).state == "available"
    assert store.resolve(ref).reveal() == "sk-test"
    assert store.delete(ref).state == "missing"

    with pytest.raises(SecretNotFound):
        store.resolve(ref)


def test_secret_errors_do_not_render_secret_values() -> None:
    ref = SecretRef(store="system", name="openai-primary")
    store = InMemorySecretStore()
    secret = "diagnostic-secret"
    store.put(ref, SecretValue(secret))
    store.delete(ref)

    with pytest.raises(SecretNotFound) as raised:
        store.resolve(ref)

    assert secret not in str(raised.value)
    assert secret not in repr(raised.value)


def test_system_store_uses_fixed_service_and_validated_account() -> None:
    adapter = FakeCredentialAdapter()
    store = SystemCredentialSecretStore(adapter=adapter)
    ref = SecretRef(store="system", name="openai-primary")

    store.put(ref, SecretValue("sk-system"))

    assert adapter.values[("openteamwork", "openai-primary")] == "sk-system"
    assert store.resolve(ref).reveal() == "sk-system"
    assert store.status(ref).state == "available"


def test_unavailable_system_backend_never_falls_back_to_plaintext() -> None:
    adapter = FakeCredentialAdapter(available=False)
    store = SystemCredentialSecretStore(adapter=adapter)
    ref = SecretRef(store="system", name="openai-primary")
    secret = "sk-do-not-fallback"

    assert store.status(ref).state == "backend_unavailable"
    with pytest.raises(SecretBackendUnavailable) as put_error:
        store.put(ref, SecretValue(secret))
    with pytest.raises(SecretBackendUnavailable) as resolve_error:
        store.resolve(ref)

    assert adapter.values == {}
    assert secret not in str(put_error.value)
    assert secret not in str(resolve_error.value)
