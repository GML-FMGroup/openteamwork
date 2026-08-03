"""Protected Secret references, stores, and resolver values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from .models import ResourceName, StrictConfigModel


class SecretRef(StrictConfigModel):
    """Safe persisted reference to credential material."""

    store: Literal["system"] = "system"
    name: ResourceName


class SecretValue:
    """Credential material whose normal string representations are redacted."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("SecretValue must contain a non-empty string")
        self.__value = value

    def reveal(self) -> str:
        """Explicitly reveal the credential at an authorized adapter boundary."""
        return self.__value

    def __str__(self) -> str:
        return "<secret:redacted>"

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"


SecretState = Literal["available", "missing", "backend_unavailable"]


@dataclass(frozen=True, slots=True)
class SecretStatus:
    """Non-sensitive availability projection for one Secret reference."""

    ref: SecretRef
    state: SecretState
    backend: str


class SecretError(RuntimeError):
    """Base class for failures that never retain credential material."""

    def __init__(self, ref: SecretRef, message: str) -> None:
        self.ref = ref
        super().__init__(f"{message}: {ref.store}/{ref.name}")


class SecretNotFound(SecretError):
    """Raised when a protected credential is not present."""


class SecretBackendUnavailable(SecretError):
    """Raised when no secure credential backend can service the request."""


class SecretStore(Protocol):
    """Persistence port for protected credential material."""

    def put(self, ref: SecretRef, value: SecretValue) -> SecretStatus: ...

    def status(self, ref: SecretRef) -> SecretStatus: ...

    def resolve(self, ref: SecretRef) -> SecretValue: ...

    def delete(self, ref: SecretRef) -> SecretStatus: ...


class CredentialAdapter(Protocol):
    """Minimal secure-system adapter used by the system SecretStore."""

    def is_available(self) -> bool: ...

    def get_password(self, service: str, account: str) -> str | None: ...

    def set_password(self, service: str, account: str, value: str) -> None: ...

    def delete_password(self, service: str, account: str) -> None: ...


class InMemorySecretStore:
    """Deterministic SecretStore for tests; never a production persistence fallback."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def put(self, ref: SecretRef, value: SecretValue) -> SecretStatus:
        self._values[(ref.store, ref.name)] = value.reveal()
        return self.status(ref)

    def status(self, ref: SecretRef) -> SecretStatus:
        state: SecretState = "available" if (ref.store, ref.name) in self._values else "missing"
        return SecretStatus(ref=ref, state=state, backend="memory")

    def resolve(self, ref: SecretRef) -> SecretValue:
        value = self._values.get((ref.store, ref.name))
        if value is None:
            raise SecretNotFound(ref, "Secret is not available")
        return SecretValue(value)

    def delete(self, ref: SecretRef) -> SecretStatus:
        self._values.pop((ref.store, ref.name), None)
        return self.status(ref)


class SystemCredentialSecretStore:
    """SecretStore backed by the operating system credential service."""

    def __init__(self, *, adapter: CredentialAdapter | None = None) -> None:
        self._adapter = adapter if adapter is not None else _load_keyring_adapter()

    def put(self, ref: SecretRef, value: SecretValue) -> SecretStatus:
        adapter = self._require_adapter(ref)
        try:
            adapter.set_password("openppx", ref.name, value.reveal())
        except Exception:
            raise SecretBackendUnavailable(ref, "System credential backend is unavailable") from None
        return SecretStatus(ref=ref, state="available", backend="system")

    def status(self, ref: SecretRef) -> SecretStatus:
        adapter = self._adapter
        if adapter is None or not _adapter_available(adapter):
            return SecretStatus(ref=ref, state="backend_unavailable", backend="system")
        try:
            value = adapter.get_password("openppx", ref.name)
        except Exception:
            return SecretStatus(ref=ref, state="backend_unavailable", backend="system")
        state: SecretState = "available" if value is not None else "missing"
        return SecretStatus(ref=ref, state=state, backend="system")

    def resolve(self, ref: SecretRef) -> SecretValue:
        adapter = self._require_adapter(ref)
        try:
            value = adapter.get_password("openppx", ref.name)
        except Exception:
            raise SecretBackendUnavailable(ref, "System credential backend is unavailable") from None
        if value is None:
            raise SecretNotFound(ref, "Secret is not available")
        return SecretValue(value)

    def delete(self, ref: SecretRef) -> SecretStatus:
        adapter = self._require_adapter(ref)
        if self.status(ref).state == "missing":
            return SecretStatus(ref=ref, state="missing", backend="system")
        try:
            adapter.delete_password("openppx", ref.name)
        except Exception:
            raise SecretBackendUnavailable(ref, "System credential backend is unavailable") from None
        return SecretStatus(ref=ref, state="missing", backend="system")

    def _require_adapter(self, ref: SecretRef) -> CredentialAdapter:
        adapter = self._adapter
        if adapter is None or not _adapter_available(adapter):
            raise SecretBackendUnavailable(ref, "System credential backend is unavailable")
        return adapter


class _KeyringAdapter:
    """Lazy wrapper around the optional-at-import-time keyring package."""

    def __init__(self, module: object) -> None:
        self._module = module

    def is_available(self) -> bool:
        try:
            backend = self._module.get_keyring()  # type: ignore[attr-defined]
            return float(backend.priority) > 0
        except Exception:
            return False

    def get_password(self, service: str, account: str) -> str | None:
        return self._module.get_password(service, account)  # type: ignore[attr-defined,no-any-return]

    def set_password(self, service: str, account: str, value: str) -> None:
        self._module.set_password(service, account, value)  # type: ignore[attr-defined]

    def delete_password(self, service: str, account: str) -> None:
        self._module.delete_password(service, account)  # type: ignore[attr-defined]


def _adapter_available(adapter: CredentialAdapter) -> bool:
    try:
        return adapter.is_available()
    except Exception:
        return False


def _load_keyring_adapter() -> CredentialAdapter | None:
    try:
        import keyring
    except ImportError:
        return None
    return _KeyringAdapter(keyring)
