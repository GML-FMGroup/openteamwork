"""Shared runtime helpers for agent ownership and access bootstrap."""

from __future__ import annotations

from .identity_models import ResolvedPrincipal
from .identity_store import IdentityStore


def ensure_access_principal(
    identity_store: IdentityStore,
    *,
    principal_id: str,
    source: str,
    account_kind: str,
    display_name: str | None = None,
    authenticated: bool = False,
) -> ResolvedPrincipal | None:
    """Ensure one referenced principal exists for access checks and management."""
    normalized_principal_id = str(principal_id or "").strip()
    if not normalized_principal_id:
        return None
    existing = identity_store.get_principal(normalized_principal_id)
    if existing is not None:
        return existing
    return identity_store.put_principal(
        ResolvedPrincipal(
            principal_id=normalized_principal_id,
            principal_type="human",
            privilege_level="minimal",
            account_kind=account_kind,
            display_name=display_name or normalized_principal_id,
            authenticated=authenticated,
            external_subject_id=normalized_principal_id,
            external_display_id=normalized_principal_id,
            metadata={"source": source},
        )
    )


def ensure_owner_principal(
    identity_store: IdentityStore,
    *,
    owner_principal_id: str,
) -> ResolvedPrincipal | None:
    """Ensure the configured owner principal exists for access checks."""
    return ensure_access_principal(
        identity_store,
        principal_id=owner_principal_id,
        source="agent_owner_config",
        account_kind="configured_owner",
    )
