from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openppx.runtime.user_accounts import LoginRateLimiter, UserAccountError, UserAccountService


def _service(tmp_path: Path, *, now_ms: int = 1_700_000_000_000) -> UserAccountService:
    return UserAccountService(
        db_path=tmp_path / "identity.db",
        clock_ms=lambda: now_ms,
    )


def test_add_user_normalizes_email_hashes_secret_and_creates_principal(tmp_path: Path) -> None:
    service = _service(tmp_path)

    account = service.add_user(
        email="  Jiang@Example.COM ",
        secret="correct horse battery staple",
        privilege_level="high",
    )

    assert account.email == "jiang@example.com"
    assert account.user_id.startswith("user_")
    assert account.privilege_level == "high"
    assert account.status == "active"
    assert service.identity_store.get_principal(account.user_id).privilege_level == "high"  # type: ignore[union-attr]
    with sqlite3.connect(tmp_path / "identity.db") as conn:
        secret_hash = conn.execute(
            "SELECT secret_hash FROM user_accounts WHERE user_id = ?",
            (account.user_id,),
        ).fetchone()[0]
    assert secret_hash.startswith("$argon2id$")
    assert "correct horse battery staple" not in secret_hash


def test_add_user_rejects_duplicate_email_without_overwriting_account(tmp_path: Path) -> None:
    service = _service(tmp_path)
    original = service.add_user(
        email="jiang@example.com",
        secret="first-secret-value",
        privilege_level="low",
    )

    with pytest.raises(UserAccountError, match="already exists") as raised:
        service.add_user(
            email="JIANG@example.com",
            secret="replacement-secret",
            privilege_level="root",
        )

    assert raised.value.code == "user_exists"
    assert service.list_users() == (original,)
    assert service.authenticate("jiang@example.com", "first-secret-value").account == original


@pytest.mark.parametrize("privilege_level", ["minimal", "admin", "", "HIGH"])
def test_add_user_rejects_unknown_privilege(tmp_path: Path, privilege_level: str) -> None:
    with pytest.raises(UserAccountError) as raised:
        _service(tmp_path).add_user(
            email="jiang@example.com",
            secret="correct horse battery staple",
            privilege_level=privilege_level,
        )
    assert raised.value.code == "invalid_privilege_level"


def test_authentication_returns_opaque_expiring_token_and_resolves_account(tmp_path: Path) -> None:
    service = _service(tmp_path)
    account = service.add_user(
        email="jiang@example.com",
        secret="correct horse battery staple",
        privilege_level="medium",
    )

    login = service.authenticate(
        "jiang@example.com",
        "correct horse battery staple",
        ttl_seconds=60,
    )

    assert login.account == account
    assert login.access_token.startswith("otw_session_")
    assert login.expires_at_ms == 1_700_000_060_000
    assert service.resolve_session(login.access_token) == account
    with sqlite3.connect(tmp_path / "identity.db") as conn:
        stored = conn.execute("SELECT token_hash FROM user_sessions").fetchone()[0]
    assert login.access_token not in stored


def test_authentication_default_session_lasts_one_hour(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.add_user(
        email="jiang@example.com",
        secret="correct horse battery staple",
        privilege_level="medium",
    )

    login = service.authenticate("jiang@example.com", "correct horse battery staple")

    assert login.expires_at_ms == 1_700_003_600_000


def test_session_resolution_caps_tokens_issued_by_older_builds_to_one_hour(tmp_path: Path) -> None:
    now = [1_700_000_000_000]
    service = UserAccountService(
        db_path=tmp_path / "identity.db",
        clock_ms=lambda: now[0],
    )
    service.add_user(
        email="jiang@example.com",
        secret="correct horse battery staple",
        privilege_level="medium",
    )
    login = service.authenticate("jiang@example.com", "correct horse battery staple")
    with sqlite3.connect(tmp_path / "identity.db") as conn:
        conn.execute(
            "UPDATE user_sessions SET expires_at_ms = ?",
            (1_702_592_000_000,),
        )

    now[0] += 3_600_001

    assert service.resolve_session(login.access_token) is None


def test_bad_secret_and_unknown_email_have_same_public_failure(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.add_user(
        email="jiang@example.com",
        secret="correct horse battery staple",
        privilege_level="medium",
    )

    failures = []
    for email, secret in (
        ("jiang@example.com", "incorrect secret"),
        ("unknown@example.com", "incorrect secret"),
    ):
        with pytest.raises(UserAccountError) as raised:
            service.authenticate(email, secret)
        failures.append((raised.value.code, str(raised.value)))

    assert failures == [
        ("invalid_credentials", "The email or secret is invalid."),
        ("invalid_credentials", "The email or secret is invalid."),
    ]


def test_expired_and_logged_out_sessions_do_not_resolve(tmp_path: Path) -> None:
    now = [1_700_000_000_000]
    service = UserAccountService(
        db_path=tmp_path / "identity.db",
        clock_ms=lambda: now[0],
    )
    service.add_user(
        email="jiang@example.com",
        secret="correct horse battery staple",
        privilege_level="medium",
    )
    expired = service.authenticate("jiang@example.com", "correct horse battery staple", ttl_seconds=1)
    now[0] += 1_001
    assert service.resolve_session(expired.access_token) is None

    active = service.authenticate("jiang@example.com", "correct horse battery staple")
    assert service.logout(active.access_token) is True
    assert service.logout(active.access_token) is False
    assert service.resolve_session(active.access_token) is None


def test_disabling_user_revokes_sessions_and_prevents_login(tmp_path: Path) -> None:
    service = _service(tmp_path)
    account = service.add_user(
        email="jiang@example.com",
        secret="correct horse battery staple",
        privilege_level="root",
    )
    login = service.authenticate("jiang@example.com", "correct horse battery staple")

    disabled = service.disable_user("JIANG@example.com")

    assert disabled.user_id == account.user_id
    assert disabled.status == "disabled"
    assert service.resolve_session(login.access_token) is None
    assert service.identity_store.get_principal(account.user_id).authenticated is False  # type: ignore[union-attr]
    with pytest.raises(UserAccountError) as raised:
        service.authenticate("jiang@example.com", "correct horse battery staple")
    assert raised.value.code == "invalid_credentials"


def test_login_rate_limiter_blocks_bounded_failures_and_recovers_after_window() -> None:
    now = [100.0]
    limiter = LoginRateLimiter(max_failures=2, window_seconds=60, clock=lambda: now[0])

    assert limiter.is_blocked("opaque-key") is False
    limiter.record_failure("opaque-key")
    assert limiter.is_blocked("opaque-key") is False
    limiter.record_failure("opaque-key")
    assert limiter.is_blocked("opaque-key") is True
    assert limiter.retry_after_seconds("opaque-key") == 60

    now[0] += 61
    assert limiter.is_blocked("opaque-key") is False


def test_login_rate_limiter_clear_removes_prior_failures() -> None:
    limiter = LoginRateLimiter(max_failures=1, window_seconds=60)

    limiter.record_failure("opaque-key")
    assert limiter.is_blocked("opaque-key") is True
    limiter.clear("opaque-key")

    assert limiter.is_blocked("opaque-key") is False
