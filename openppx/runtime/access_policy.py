"""Unified access policy for session, artifact, and memory query surfaces."""

from __future__ import annotations

from .access_decision import AccessDecision
from .agent_access_store import AgentAccessStore, create_agent_access_store
from .identity_store import IdentityStore, create_identity_store


class AccessPolicy:
    """Evaluate runtime access decisions from principal and membership data."""

    def __init__(
        self,
        *,
        identity_store: IdentityStore | None = None,
        agent_access_store: AgentAccessStore | None = None,
    ) -> None:
        self._identity_store = identity_store or create_identity_store()
        self._agent_access_store = agent_access_store or create_agent_access_store()

    def relation_to_agent(self, *, requester_principal_id: str, agent_id: str) -> str:
        """Return the requester's relation to the target agent."""
        principal = self._identity_store.get_principal(requester_principal_id)
        if principal is None:
            return "none"
        if principal.principal_type == "service":
            return "service"
        if principal.privilege_level == "root":
            return "root"

        record = self._agent_access_store.get_agent_record(agent_id)
        if record is not None and record.owner_principal_id == requester_principal_id:
            return "owner"

        membership = self._agent_access_store.get_membership(
            agent_id=agent_id,
            principal_id=requester_principal_id,
        )
        if membership is None:
            return "none"
        relation = str(membership.relation or "").strip().lower()
        if relation in {"owner", "participant"}:
            return relation
        return "none"

    def decide_agent_scope(self, *, requester_principal_id: str, agent_id: str, access_kind: str) -> AccessDecision:
        """Return the visible principal scope for one agent-bound request."""
        principal = self._identity_store.get_principal(requester_principal_id)
        if principal is None:
            return AccessDecision.deny(
                reason="requester_principal_not_found",
                access_kind=access_kind,
                requester_principal_id=requester_principal_id,
                agent_id=agent_id,
            )

        relation = self.relation_to_agent(
            requester_principal_id=requester_principal_id,
            agent_id=agent_id,
        )
        if principal.privilege_level == "root":
            return AccessDecision.allow_scope(
                reason="root_scope",
                access_kind=access_kind,
                requester_principal_id=requester_principal_id,
                agent_id=agent_id,
                relation_to_agent=relation,
                scope_kind="all",
            )

        if relation == "owner":
            visible_principal_ids = {
                requester_principal_id,
                *(membership.principal_id for membership in self._agent_access_store.list_memberships(agent_id=agent_id)),
            }
            record = self._agent_access_store.get_agent_record(agent_id)
            if record is not None and record.owner_principal_id:
                visible_principal_ids.add(record.owner_principal_id)
            return AccessDecision.allow_scope(
                reason="agent_owner_scope",
                access_kind=access_kind,
                requester_principal_id=requester_principal_id,
                agent_id=agent_id,
                relation_to_agent=relation,
                scope_kind="agent",
                visible_principal_ids=visible_principal_ids,
            )

        if principal.account_kind == "product_user":
            return AccessDecision.deny(
                reason="agent_ownership_required",
                access_kind=access_kind,
                requester_principal_id=requester_principal_id,
                agent_id=agent_id,
                relation_to_agent=relation,
            )

        return AccessDecision.allow_scope(
            reason="self_scope",
            access_kind=access_kind,
            requester_principal_id=requester_principal_id,
            agent_id=agent_id,
            relation_to_agent=relation,
            scope_kind="self",
            visible_principal_ids=(requester_principal_id,),
        )

    def decide_subject_access(
        self,
        *,
        requester_principal_id: str,
        agent_id: str,
        subject_principal_id: str,
        access_kind: str,
    ) -> AccessDecision:
        """Return whether the requester can read a specific subject principal."""
        scope = self.decide_agent_scope(
            requester_principal_id=requester_principal_id,
            agent_id=agent_id,
            access_kind=access_kind,
        )
        if not scope.allow:
            return scope
        if scope.allows_principal(subject_principal_id):
            return scope
        return AccessDecision.deny(
            reason="target_principal_outside_visible_scope",
            access_kind=access_kind,
            requester_principal_id=requester_principal_id,
            agent_id=agent_id,
            relation_to_agent=scope.relation_to_agent,
        )

    def decide_agent_management(
        self,
        *,
        requester_principal_id: str,
        agent_id: str,
        access_kind: str,
    ) -> AccessDecision:
        """Return whether the requester can mutate one agent's access state."""
        principal = self._identity_store.get_principal(requester_principal_id)
        if principal is None:
            return AccessDecision.deny(
                reason="requester_principal_not_found",
                access_kind=access_kind,
                requester_principal_id=requester_principal_id,
                agent_id=agent_id,
            )

        relation = self.relation_to_agent(
            requester_principal_id=requester_principal_id,
            agent_id=agent_id,
        )
        if principal.privilege_level == "root":
            return AccessDecision.allow_scope(
                reason="root_management_scope",
                access_kind=access_kind,
                requester_principal_id=requester_principal_id,
                agent_id=agent_id,
                relation_to_agent=relation,
                scope_kind="all",
            )
        if access_kind in {"membership_write", "access_audit_read"} and relation == "owner":
            return AccessDecision.allow_scope(
                reason=(
                    "agent_owner_membership_management"
                    if access_kind == "membership_write"
                    else "agent_owner_admin_read"
                ),
                access_kind=access_kind,
                requester_principal_id=requester_principal_id,
                agent_id=agent_id,
                relation_to_agent=relation,
                scope_kind="agent",
                visible_principal_ids=(requester_principal_id,),
            )
        if access_kind == "ownership_write" and relation == "owner":
            return AccessDecision.deny(
                reason="ownership_change_requires_root",
                access_kind=access_kind,
                requester_principal_id=requester_principal_id,
                agent_id=agent_id,
                relation_to_agent=relation,
            )
        return AccessDecision.deny(
            reason="insufficient_agent_admin_role",
            access_kind=access_kind,
            requester_principal_id=requester_principal_id,
            agent_id=agent_id,
            relation_to_agent=relation,
        )
