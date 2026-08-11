"""Trusted authorization and name resolution for historical Session access."""

from __future__ import annotations

from dataclasses import dataclass

from openppx.config import FilesystemConfigRepository

from .agent_access_runtime import ensure_access_principal
from .agent_access_store import AgentAccessStore, AgentRecord
from .identity_store import IdentityStore
from .user_accounts import USER_PRIVILEGE_LEVELS, privilege_allows


_MANAGEMENT_LEVELS = frozenset({"high", "root"})


@dataclass(frozen=True, slots=True)
class HistoryAccessDecision:
    """One explicit history authorization result with an audit-safe reason."""

    allow: bool
    reason: str


@dataclass(frozen=True, slots=True)
class HistoryAgentCandidate:
    """One authorized display-name candidate safe to show for clarification."""

    agent_id: str
    display_name: str
    owner_principal_id: str
    owner_display_name: str
    privilege_level: str
    short_id: str


@dataclass(frozen=True, slots=True)
class HistoryAgentResolution:
    """Deterministic result of resolving a display name inside an allowed scope."""

    status: str
    agent_id: str | None = None
    owner_principal_id: str | None = None
    candidates: tuple[HistoryAgentCandidate, ...] = ()


class HistoryAccessPolicy:
    """Evaluate historical Session access using trusted user and Agent identities."""

    def __init__(
        self,
        *,
        identity_store: IdentityStore,
        agent_access_store: AgentAccessStore,
    ) -> None:
        self._identity_store = identity_store
        self._agent_access_store = agent_access_store

    def decide(
        self,
        *,
        source_user_id: str,
        source_agent_id: str,
        target_user_id: str,
        target_agent_id: str,
        source_agent_privilege_level: str | None = None,
    ) -> HistoryAccessDecision:
        """Return whether one invoking Agent may read a target Agent's user history."""
        source_user = self._identity_store.get_principal(source_user_id)
        target_user = self._identity_store.get_principal(target_user_id)
        source_agent = self._agent_access_store.get_agent_record(source_agent_id)
        target_agent = self._agent_access_store.get_agent_record(target_agent_id)
        if source_user is None or source_agent is None:
            return HistoryAccessDecision(False, "source_identity_not_found")
        if target_user is None or target_agent is None:
            return HistoryAccessDecision(False, "target_identity_not_found")
        if source_agent.owner_principal_id != source_user.principal_id:
            return HistoryAccessDecision(False, "invoking_agent_not_owned_by_source_user")
        if target_agent.owner_principal_id != target_user.principal_id:
            return HistoryAccessDecision(False, "target_agent_not_owned_by_target_user")
        effective_source_agent_level = self._effective_source_agent_level(
            source_agent.privilege_level,
            source_agent_privilege_level,
        )

        if source_user.principal_id == target_user.principal_id:
            if source_agent.agent_id == target_agent.agent_id:
                return HistoryAccessDecision(True, "own_agent_history_allows")
            if privilege_allows(effective_source_agent_level, target_agent.privilege_level):
                return HistoryAccessDecision(True, "same_user_agent_privilege_allows")
            return HistoryAccessDecision(False, "source_agent_privilege_too_low")

        if target_user.privilege_level == "root" or target_agent.privilege_level == "root":
            return HistoryAccessDecision(False, "cross_user_root_history_denied")
        target_owner = self._identity_store.get_principal(target_agent.owner_principal_id)
        if target_owner is None or target_owner.privilege_level == "root":
            return HistoryAccessDecision(False, "cross_user_root_history_denied")
        if source_user.privilege_level not in _MANAGEMENT_LEVELS:
            return HistoryAccessDecision(False, "organization_history_requires_management_user")
        if effective_source_agent_level not in _MANAGEMENT_LEVELS:
            return HistoryAccessDecision(False, "organization_history_requires_high_agent")
        return HistoryAccessDecision(True, "organization_non_root_history_allows")

    @staticmethod
    def _effective_source_agent_level(configured: str, runtime: str | None) -> str:
        """Use the lower of current Config and trusted Runtime Agent privilege."""
        if runtime not in USER_PRIVILEGE_LEVELS:
            return configured
        if configured not in USER_PRIVILEGE_LEVELS:
            return ""
        configured_rank = USER_PRIVILEGE_LEVELS.index(configured)
        runtime_rank = USER_PRIVILEGE_LEVELS.index(runtime)
        return USER_PRIVILEGE_LEVELS[min(configured_rank, runtime_rank)]


class HistoryAgentResolver:
    """Resolve mutable display names only after applying the history policy."""

    def __init__(
        self,
        *,
        identity_store: IdentityStore,
        agent_access_store: AgentAccessStore,
        policy: HistoryAccessPolicy,
    ) -> None:
        self._identity_store = identity_store
        self._agent_access_store = agent_access_store
        self._policy = policy

    def resolve(
        self,
        *,
        source_user_id: str,
        source_agent_id: str,
        display_name: str,
        source_agent_privilege_level: str | None = None,
    ) -> HistoryAgentResolution:
        """Resolve one exact display name without disclosing unauthorized Agents."""
        normalized = str(display_name or "").strip().casefold()
        if not normalized:
            return HistoryAgentResolution(status="not_found")
        candidates: list[HistoryAgentCandidate] = []
        for record in self._agent_access_store.list_agent_records():
            if record.status == "purged" or record.name.strip().casefold() != normalized:
                continue
            owner = self._identity_store.get_principal(record.owner_principal_id)
            if owner is None:
                continue
            decision = self._policy.decide(
                source_user_id=source_user_id,
                source_agent_id=source_agent_id,
                target_user_id=record.owner_principal_id,
                target_agent_id=record.agent_id,
                source_agent_privilege_level=source_agent_privilege_level,
            )
            if not decision.allow:
                continue
            candidates.append(self._candidate(record, owner.display_name))
        candidates.sort(key=lambda item: item.agent_id)
        if not candidates:
            return HistoryAgentResolution(status="not_found")
        if len(candidates) > 1:
            return HistoryAgentResolution(status="ambiguous", candidates=tuple(candidates[:10]))
        candidate = candidates[0]
        return HistoryAgentResolution(
            status="resolved",
            agent_id=candidate.agent_id,
            owner_principal_id=candidate.owner_principal_id,
            candidates=(candidate,),
        )

    @staticmethod
    def _candidate(record: AgentRecord, owner_display_name: str) -> HistoryAgentCandidate:
        """Project one bounded clarification candidate without mutable authority facts."""
        return HistoryAgentCandidate(
            agent_id=record.agent_id,
            display_name=record.name,
            owner_principal_id=record.owner_principal_id,
            owner_display_name=owner_display_name,
            privilege_level=record.privilege_level,
            short_id=record.agent_id[:8],
        )


def sync_history_agent_catalog(
    *,
    repository: FilesystemConfigRepository,
    identity_store: IdentityStore,
    agent_access_store: AgentAccessStore,
) -> None:
    """Project current Config Agents into the trusted history identity catalog."""
    node = repository.read_node()
    enabled = frozenset(node.document.spec.enabled_agents)
    configured_ids = frozenset(repository.list_agent_ids())
    for agent_id in sorted(configured_ids):
        resource = repository.read_agent(agent_id)
        agent = resource.document
        existing = agent_access_store.get_agent_record(agent_id)
        owner_source = str(existing.metadata.get("owner_source", "")) if existing is not None else ""
        owner_principal_id = agent.spec.owner_principal_id
        if existing is not None and existing.owner_principal_id and owner_source not in {"", "config"}:
            owner_principal_id = existing.owner_principal_id
        ensure_access_principal(
            identity_store,
            principal_id=owner_principal_id,
            source=owner_source or "config",
            account_kind="configured_owner" if owner_source in {"", "config"} else "managed_access",
        )
        agent_access_store.upsert_agent_record(
            AgentRecord(
                agent_id=agent_id,
                name=agent.spec.display_name,
                privilege_level=agent.spec.privilege_level,
                owner_principal_id=owner_principal_id,
                status="active" if agent_id in enabled else "disabled",
                config_ref=str(resource.source.path),
                metadata={
                    **(existing.metadata if existing is not None else {}),
                    "catalog_source": "config",
                    "owner_source": owner_source or "config",
                    "config_revision": resource.revision,
                },
            )
        )
    for existing in agent_access_store.list_agent_records():
        if existing.agent_id in configured_ids or existing.metadata.get("catalog_source") != "config":
            continue
        agent_access_store.upsert_agent_record(
            AgentRecord(
                agent_id=existing.agent_id,
                name=existing.name,
                privilege_level=existing.privilege_level,
                owner_principal_id=existing.owner_principal_id,
                status="removed",
                config_ref=existing.config_ref,
                metadata=dict(existing.metadata),
            )
        )


__all__ = [
    "HistoryAccessDecision",
    "HistoryAccessPolicy",
    "HistoryAgentCandidate",
    "HistoryAgentResolution",
    "HistoryAgentResolver",
    "sync_history_agent_catalog",
]
