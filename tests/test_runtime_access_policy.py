from __future__ import annotations

from openppx.runtime.access_policy import AccessPolicy
from openppx.runtime.agent_access_store import AgentAccessStore, AgentMembership
from openppx.runtime.identity_models import ResolvedPrincipal
from openppx.runtime.identity_store import IdentityStore


def _principal(
    *,
    principal_id: str,
    privilege_level: str = "minimal",
    principal_type: str = "human",
    account_kind: str = "local",
) -> ResolvedPrincipal:
    return ResolvedPrincipal(
        principal_id=principal_id,
        principal_type=principal_type,
        privilege_level=privilege_level,
        account_kind=account_kind,
        display_name=principal_id,
        authenticated=True,
    )


def test_access_policy_owner_gets_agent_scope(tmp_path) -> None:
    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    owner = identity_store.put_principal(_principal(principal_id="owner"))
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    access_store.set_agent_owner(agent_id="writer", owner_principal_id=owner.principal_id)
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )

    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    decision = policy.decide_agent_scope(
        requester_principal_id=owner.principal_id,
        agent_id="writer",
        access_kind="session_list",
    )

    assert decision.allow is True
    assert decision.reason == "agent_owner_scope"
    assert decision.scope_kind == "agent"
    assert decision.visible_principal_ids == {"owner", "participant"}


def test_access_policy_participant_stays_self_scoped(tmp_path) -> None:
    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    other = identity_store.put_principal(_principal(principal_id="other"))
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )

    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    scope = policy.decide_agent_scope(
        requester_principal_id=participant.principal_id,
        agent_id="writer",
        access_kind="memory_query",
    )
    denied = policy.decide_subject_access(
        requester_principal_id=participant.principal_id,
        agent_id="writer",
        subject_principal_id=other.principal_id,
        access_kind="session_read",
    )

    assert scope.allow is True
    assert scope.reason == "self_scope"
    assert scope.visible_principal_ids == {"participant"}
    assert denied.allow is False
    assert denied.reason == "target_principal_outside_visible_scope"


def test_access_policy_product_user_participant_cannot_use_unowned_agent(tmp_path) -> None:
    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    participant = identity_store.put_principal(
        _principal(principal_id="participant", account_kind="product_user")
    )
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )

    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    decision = policy.decide_agent_scope(
        requester_principal_id=participant.principal_id,
        agent_id="writer",
        access_kind="session_list",
    )

    assert decision.allow is False
    assert decision.reason == "agent_ownership_required"


def test_access_policy_root_gets_global_scope(tmp_path) -> None:
    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    root = identity_store.put_principal(_principal(principal_id="root-user", privilege_level="root"))

    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    decision = policy.decide_agent_scope(
        requester_principal_id=root.principal_id,
        agent_id="writer",
        access_kind="memory_query",
    )

    assert decision.allow is True
    assert decision.reason == "root_scope"
    assert decision.scope_kind == "all"


def test_access_policy_service_relation_is_service(tmp_path) -> None:
    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    service = identity_store.put_principal(
        _principal(principal_id="cron", principal_type="service", privilege_level="high")
    )

    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    relation = policy.relation_to_agent(
        requester_principal_id=service.principal_id,
        agent_id="writer",
    )

    assert relation == "service"


def test_access_policy_owner_can_manage_memberships_but_not_ownership(tmp_path) -> None:
    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    owner = identity_store.put_principal(_principal(principal_id="owner"))
    access_store.set_agent_owner(agent_id="writer", owner_principal_id=owner.principal_id)

    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    membership_decision = policy.decide_agent_management(
        requester_principal_id=owner.principal_id,
        agent_id="writer",
        access_kind="membership_write",
    )
    audit_decision = policy.decide_agent_management(
        requester_principal_id=owner.principal_id,
        agent_id="writer",
        access_kind="access_audit_read",
    )
    ownership_decision = policy.decide_agent_management(
        requester_principal_id=owner.principal_id,
        agent_id="writer",
        access_kind="ownership_write",
    )

    assert membership_decision.allow is True
    assert membership_decision.reason == "agent_owner_membership_management"
    assert audit_decision.allow is True
    assert audit_decision.reason == "agent_owner_admin_read"
    assert ownership_decision.allow is False
    assert ownership_decision.reason == "ownership_change_requires_root"


def test_access_policy_root_can_manage_owner_and_memberships(tmp_path) -> None:
    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    root = identity_store.put_principal(_principal(principal_id="root-user", privilege_level="root"))

    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    membership_decision = policy.decide_agent_management(
        requester_principal_id=root.principal_id,
        agent_id="writer",
        access_kind="membership_write",
    )
    audit_decision = policy.decide_agent_management(
        requester_principal_id=root.principal_id,
        agent_id="writer",
        access_kind="access_audit_read",
    )
    ownership_decision = policy.decide_agent_management(
        requester_principal_id=root.principal_id,
        agent_id="writer",
        access_kind="ownership_write",
    )

    assert membership_decision.allow is True
    assert membership_decision.reason == "root_management_scope"
    assert audit_decision.allow is True
    assert audit_decision.reason == "root_management_scope"
    assert ownership_decision.allow is True
    assert ownership_decision.reason == "root_management_scope"


def test_access_policy_participant_cannot_read_access_audit(tmp_path) -> None:
    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )

    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    decision = policy.decide_agent_management(
        requester_principal_id=participant.principal_id,
        agent_id="writer",
        access_kind="access_audit_read",
    )

    assert decision.allow is False
    assert decision.reason == "insufficient_agent_admin_role"
