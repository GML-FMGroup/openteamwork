from pathlib import Path

from openppx.runtime.agent_access_store import AgentAccessStore, AgentRecord
from openppx.runtime.history_access import HistoryAccessPolicy, HistoryAgentResolver
from openppx.runtime.identity_models import ResolvedPrincipal
from openppx.runtime.identity_store import IdentityStore


def _principal(principal_id: str, privilege_level: str) -> ResolvedPrincipal:
    return ResolvedPrincipal(
        principal_id=principal_id,
        principal_type="human",
        privilege_level=privilege_level,
        account_kind="product_user",
        display_name=f"{principal_id}@example.com",
        authenticated=True,
    )


def _stores(tmp_path: Path) -> tuple[IdentityStore, AgentAccessStore]:
    db_path = tmp_path / "identity.db"
    return IdentityStore(db_path=db_path), AgentAccessStore(db_path=db_path)


def _seed_user(identity: IdentityStore, principal_id: str, level: str) -> None:
    identity.put_principal(_principal(principal_id, level))


def _seed_agent(
    access: AgentAccessStore,
    agent_id: str,
    owner_id: str,
    level: str,
    *,
    name: str | None = None,
) -> None:
    access.upsert_agent_record(
        AgentRecord(
            agent_id=agent_id,
            name=name or agent_id,
            privilege_level=level,
            owner_principal_id=owner_id,
        )
    )


def test_same_user_history_access_follows_agent_privilege_dominance(tmp_path: Path) -> None:
    identity, access = _stores(tmp_path)
    _seed_user(identity, "user-a", "high")
    _seed_agent(access, "a-low", "user-a", "low")
    _seed_agent(access, "a-medium", "user-a", "medium")
    _seed_agent(access, "a-high", "user-a", "high")
    policy = HistoryAccessPolicy(identity_store=identity, agent_access_store=access)

    assert policy.decide(
        source_user_id="user-a",
        source_agent_id="a-high",
        target_user_id="user-a",
        target_agent_id="a-low",
    ).allow
    assert policy.decide(
        source_user_id="user-a",
        source_agent_id="a-medium",
        target_user_id="user-a",
        target_agent_id="a-medium",
    ).allow
    denied = policy.decide(
        source_user_id="user-a",
        source_agent_id="a-low",
        target_user_id="user-a",
        target_agent_id="a-high",
    )
    assert denied.allow is False
    assert denied.reason == "source_agent_privilege_too_low"


def test_management_history_access_allows_only_cross_user_non_root_targets(tmp_path: Path) -> None:
    identity, access = _stores(tmp_path)
    for user_id, level in (
        ("manager", "high"),
        ("root-user", "root"),
        ("member", "medium"),
    ):
        _seed_user(identity, user_id, level)
    _seed_agent(access, "manager-high", "manager", "high")
    _seed_agent(access, "manager-medium", "manager", "medium")
    _seed_agent(access, "root-high", "root-user", "high")
    _seed_agent(access, "member-low", "member", "low")
    _seed_agent(access, "member-root", "member", "root")
    _seed_agent(access, "root-low", "root-user", "low")
    policy = HistoryAccessPolicy(identity_store=identity, agent_access_store=access)

    assert policy.decide(
        source_user_id="manager",
        source_agent_id="manager-high",
        target_user_id="member",
        target_agent_id="member-low",
    ).allow
    assert policy.decide(
        source_user_id="root-user",
        source_agent_id="root-high",
        target_user_id="member",
        target_agent_id="member-low",
    ).allow
    assert policy.decide(
        source_user_id="manager",
        source_agent_id="manager-medium",
        target_user_id="member",
        target_agent_id="member-low",
    ).reason == "organization_history_requires_high_agent"
    assert policy.decide(
        source_user_id="manager",
        source_agent_id="manager-high",
        target_user_id="root-user",
        target_agent_id="root-low",
    ).reason == "cross_user_root_history_denied"
    assert policy.decide(
        source_user_id="manager",
        source_agent_id="manager-high",
        target_user_id="member",
        target_agent_id="member-root",
    ).reason == "cross_user_root_history_denied"


def test_history_policy_rejects_model_spoofed_or_misaligned_ownership(tmp_path: Path) -> None:
    identity, access = _stores(tmp_path)
    _seed_user(identity, "manager", "high")
    _seed_user(identity, "other", "high")
    _seed_user(identity, "member", "low")
    _seed_agent(access, "other-high", "other", "high")
    _seed_agent(access, "member-low", "member", "low")
    policy = HistoryAccessPolicy(identity_store=identity, agent_access_store=access)

    decision = policy.decide(
        source_user_id="manager",
        source_agent_id="other-high",
        target_user_id="member",
        target_agent_id="member-low",
    )

    assert decision.allow is False
    assert decision.reason == "invoking_agent_not_owned_by_source_user"


def test_runtime_permission_snapshot_can_only_narrow_current_agent_level(tmp_path: Path) -> None:
    identity, access = _stores(tmp_path)
    _seed_user(identity, "manager", "high")
    _seed_user(identity, "member", "low")
    _seed_agent(access, "manager-high", "manager", "high")
    _seed_agent(access, "member-low", "member", "low")
    policy = HistoryAccessPolicy(identity_store=identity, agent_access_store=access)

    decision = policy.decide(
        source_user_id="manager",
        source_agent_id="manager-high",
        target_user_id="member",
        target_agent_id="member-low",
        source_agent_privilege_level="medium",
    )

    assert decision.allow is False
    assert decision.reason == "organization_history_requires_high_agent"


def test_agent_can_always_read_its_own_history_when_runtime_snapshot_is_narrower(
    tmp_path: Path,
) -> None:
    identity, access = _stores(tmp_path)
    _seed_user(identity, "manager", "high")
    _seed_agent(access, "manager-high", "manager", "high")
    policy = HistoryAccessPolicy(identity_store=identity, agent_access_store=access)

    decision = policy.decide(
        source_user_id="manager",
        source_agent_id="manager-high",
        target_user_id="manager",
        target_agent_id="manager-high",
        source_agent_privilege_level="low",
    )

    assert decision.allow is True
    assert decision.reason == "own_agent_history_allows"


def test_name_resolution_is_authorized_case_insensitive_and_never_guesses_duplicates(
    tmp_path: Path,
) -> None:
    identity, access = _stores(tmp_path)
    _seed_user(identity, "manager", "high")
    _seed_user(identity, "member-a", "low")
    _seed_user(identity, "member-b", "medium")
    _seed_user(identity, "root-user", "root")
    _seed_agent(access, "manager-high", "manager", "high", name="Manager")
    _seed_agent(access, "research-a", "member-a", "low", name="Research")
    _seed_agent(access, "research-b", "member-b", "medium", name="RESEARCH")
    _seed_agent(access, "root-private", "root-user", "low", name="Research")
    policy = HistoryAccessPolicy(identity_store=identity, agent_access_store=access)
    resolver = HistoryAgentResolver(
        identity_store=identity,
        agent_access_store=access,
        policy=policy,
    )

    result = resolver.resolve(
        source_user_id="manager",
        source_agent_id="manager-high",
        display_name="research",
    )

    assert result.status == "ambiguous"
    assert [candidate.agent_id for candidate in result.candidates] == ["research-a", "research-b"]
    assert all(candidate.owner_principal_id != "root-user" for candidate in result.candidates)
    assert all(candidate.short_id for candidate in result.candidates)


def test_name_resolution_returns_one_immutable_agent_id_when_unique(tmp_path: Path) -> None:
    identity, access = _stores(tmp_path)
    _seed_user(identity, "owner", "medium")
    _seed_agent(access, "owner-medium", "owner", "medium", name="Larry")
    policy = HistoryAccessPolicy(identity_store=identity, agent_access_store=access)
    resolver = HistoryAgentResolver(
        identity_store=identity,
        agent_access_store=access,
        policy=policy,
    )

    result = resolver.resolve(
        source_user_id="owner",
        source_agent_id="owner-medium",
        display_name="larry",
    )

    assert result.status == "resolved"
    assert result.agent_id == "owner-medium"
    assert result.owner_principal_id == "owner"
