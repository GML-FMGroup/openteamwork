"""Node-local product user accounts and authenticated App sessions."""

from __future__ import annotations

import hashlib
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from .identity_models import ResolvedPrincipal
from .identity_store import IdentityStore


USER_PRIVILEGE_LEVELS = ("low", "medium", "high", "root")
_PRIVILEGE_RANK = {level: index for index, level in enumerate(USER_PRIVILEGE_LEVELS)}
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SESSION_TOKEN_PREFIX = "otw_session_"
_DEFAULT_SESSION_TTL_SECONDS = 60 * 60
_DEFAULT_SESSION_IDLE_MS = _DEFAULT_SESSION_TTL_SECONDS * 1000
_ARGON2_MEMORY_KIB = 19 * 1024
_ARGON2_ITERATIONS = 2
_ARGON2_LANES = 1


class UserAccountError(RuntimeError):
    """Stable user-account failure safe for CLI and Client API projection."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class LoginRateLimiter:
    """Bound failed login attempts for one opaque caller/account key."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: int = 5 * 60,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_failures <= 0 or window_seconds <= 0:
            raise ValueError("Login rate-limit bounds must be positive.")
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._clock = clock or time.monotonic
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.RLock()

    def _active_failures(self, key: str, *, now: float) -> list[float]:
        cutoff = now - self._window_seconds
        active = [timestamp for timestamp in self._failures.get(key, ()) if timestamp > cutoff]
        if active:
            self._failures[key] = active
        else:
            self._failures.pop(key, None)
        return active

    def is_blocked(self, key: str) -> bool:
        """Return whether the key reached the failure bound inside the window."""

        with self._lock:
            return len(self._active_failures(key, now=self._clock())) >= self._max_failures

    def record_failure(self, key: str) -> None:
        """Record one failed credential check without retaining login identifiers."""

        with self._lock:
            now = self._clock()
            failures = self._active_failures(key, now=now)
            failures.append(now)
            self._failures[key] = failures

    def retry_after_seconds(self, key: str) -> int:
        """Return a conservative Retry-After value for one blocked key."""

        with self._lock:
            now = self._clock()
            failures = self._active_failures(key, now=now)
            if len(failures) < self._max_failures:
                return 0
            return max(1, math.ceil(failures[0] + self._window_seconds - now))

    def clear(self, key: str) -> None:
        """Clear accumulated failures after a successful authentication."""

        with self._lock:
            self._failures.pop(key, None)


@dataclass(frozen=True, slots=True)
class UserAccount:
    """One product user account without credential material."""

    user_id: str
    email: str
    privilege_level: str
    status: str
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class UserLogin:
    """One newly issued opaque App session returned exactly once."""

    account: UserAccount
    access_token: str
    expires_at_ms: int


def privilege_allows(user_level: str, agent_level: str) -> bool:
    """Return whether one user privilege is at least the requested Agent level."""

    user_rank = _PRIVILEGE_RANK.get(str(user_level))
    agent_rank = _PRIVILEGE_RANK.get(str(agent_level))
    return user_rank is not None and agent_rank is not None and user_rank >= agent_rank


def normalize_login_email(email: str) -> str:
    """Normalize and validate one account login email."""

    normalized = str(email or "").strip().casefold()
    if len(normalized) > 254 or _EMAIL_PATTERN.fullmatch(normalized) is None:
        raise UserAccountError("invalid_email", "Enter a valid email address.")
    return normalized


def _validate_secret(secret: str) -> bytes:
    """Return bounded secret bytes without retaining a normalized plaintext copy."""

    encoded = str(secret or "").encode("utf-8")
    if len(encoded) < 8:
        raise UserAccountError("invalid_secret", "Secret must contain at least 8 UTF-8 bytes.")
    if len(encoded) > 4096:
        raise UserAccountError("invalid_secret", "Secret exceeds the 4096-byte limit.")
    return encoded


def _hash_secret(secret: bytes) -> str:
    """Encode one secret as a self-describing Argon2id PHC string."""

    return Argon2id(
        salt=os.urandom(16),
        length=32,
        iterations=_ARGON2_ITERATIONS,
        lanes=_ARGON2_LANES,
        memory_cost=_ARGON2_MEMORY_KIB,
    ).derive_phc_encoded(secret)


_DUMMY_SECRET_HASH = _hash_secret(b"openteamwork-invalid-account-probe")


def _verify_secret(secret: bytes, encoded_hash: str) -> bool:
    """Verify one Argon2id PHC hash and collapse malformed hashes to failure."""

    try:
        Argon2id.verify_phc_encoded(secret, encoded_hash)
    except (InvalidKey, ValueError):
        return False
    return True


def _token_hash(token: str) -> str:
    """Return the database representation of an opaque session token."""

    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open one identity database connection with predictable row behavior."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


class UserAccountService:
    """Provision users and authenticate revocable Node-local App sessions."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        clock_ms: Callable[[], int] | None = None,
        identity_store: IdentityStore | None = None,
    ) -> None:
        self._db_path = Path(db_path).expanduser().resolve(strict=False)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()
        self.identity_store = identity_store or IdentityStore(db_path=self._db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create additive account, session, and authentication-audit tables."""

        with _connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_accounts (
                    user_id TEXT PRIMARY KEY,
                    email_normalized TEXT NOT NULL UNIQUE,
                    secret_hash TEXT NOT NULL,
                    privilege_level TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    disabled_at_ms INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    issued_at_ms INTEGER NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    revoked_at_ms INTEGER,
                    last_seen_at_ms INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES user_accounts(user_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_sessions_user_active "
                "ON user_sessions(user_id, revoked_at_ms, expires_at_ms)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_auth_audit (
                    audit_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                )
                """
            )

    @staticmethod
    def _account_from_row(row: sqlite3.Row) -> UserAccount:
        """Project one credential-free account row."""

        return UserAccount(
            user_id=str(row["user_id"]),
            email=str(row["email_normalized"]),
            privilege_level=str(row["privilege_level"]),
            status=str(row["status"]),
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
        )

    @staticmethod
    def _effective_idle_deadline(row: sqlite3.Row) -> int:
        """Return the bounded deadline for current and legacy session rows."""

        return min(
            int(row["expires_at_ms"]),
            int(row["last_seen_at_ms"]) + _DEFAULT_SESSION_IDLE_MS,
        )

    def _audit(self, *, user_id: str | None, action: str, outcome: str) -> None:
        """Persist a credential-free authentication fact."""

        with _connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO user_auth_audit (audit_id, user_id, action, outcome, created_at_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"uaudit_{secrets.token_hex(12)}", user_id, action, outcome, self._clock_ms()),
            )

    def add_user(self, *, email: str, secret: str, privilege_level: str) -> UserAccount:
        """Create one immutable-login account and matching runtime Principal."""

        normalized_email = normalize_login_email(email)
        if privilege_level not in USER_PRIVILEGE_LEVELS:
            raise UserAccountError(
                "invalid_privilege_level",
                "Privilege level must be low, medium, high, or root.",
            )
        secret_hash = _hash_secret(_validate_secret(secret))
        now_ms = self._clock_ms()
        account = UserAccount(
            user_id=f"user_{secrets.token_hex(12)}",
            email=normalized_email,
            privilege_level=privilege_level,
            status="active",
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        with self._lock:
            try:
                with _connect(self._db_path) as conn:
                    conn.execute(
                        """
                        INSERT INTO user_accounts (
                            user_id, email_normalized, secret_hash, privilege_level,
                            status, created_at_ms, updated_at_ms, disabled_at_ms
                        ) VALUES (?, ?, ?, ?, 'active', ?, ?, NULL)
                        """,
                        (
                            account.user_id,
                            account.email,
                            secret_hash,
                            account.privilege_level,
                            now_ms,
                            now_ms,
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise UserAccountError("user_exists", "A user with this email already exists.") from exc
            try:
                self.identity_store.put_principal(self._principal_for(account, authenticated=True))
            except Exception:
                with _connect(self._db_path) as conn:
                    conn.execute("DELETE FROM user_accounts WHERE user_id = ?", (account.user_id,))
                raise
            self._audit(user_id=account.user_id, action="user.add", outcome="succeeded")
        return account

    def list_users(self) -> tuple[UserAccount, ...]:
        """Return all accounts ordered by normalized email without secret fields."""

        with self._lock, _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT user_id, email_normalized, privilege_level, status,
                       created_at_ms, updated_at_ms
                FROM user_accounts
                ORDER BY email_normalized ASC
                """
            ).fetchall()
        return tuple(self._account_from_row(row) for row in rows)

    def disable_user(self, email: str) -> UserAccount:
        """Permanently disable one MVP account and revoke all of its sessions."""

        normalized_email = normalize_login_email(email)
        now_ms = self._clock_ms()
        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM user_accounts WHERE email_normalized = ?",
                (normalized_email,),
            ).fetchone()
            if row is None:
                raise UserAccountError("user_not_found", "The user account was not found.")
            account = self._account_from_row(row)
            if account.status != "disabled":
                conn.execute(
                    """
                    UPDATE user_accounts
                    SET status = 'disabled', updated_at_ms = ?, disabled_at_ms = ?
                    WHERE user_id = ?
                    """,
                    (now_ms, now_ms, account.user_id),
                )
                conn.execute(
                    """
                    UPDATE user_sessions SET revoked_at_ms = ?
                    WHERE user_id = ? AND revoked_at_ms IS NULL
                    """,
                    (now_ms, account.user_id),
                )
            disabled = UserAccount(
                user_id=account.user_id,
                email=account.email,
                privilege_level=account.privilege_level,
                status="disabled",
                created_at_ms=account.created_at_ms,
                updated_at_ms=now_ms if account.status != "disabled" else account.updated_at_ms,
            )
        self.identity_store.put_principal(self._principal_for(disabled, authenticated=False))
        self._audit(user_id=disabled.user_id, action="user.disable", outcome="succeeded")
        return disabled

    def authenticate(
        self,
        email: str,
        secret: str,
        *,
        ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS,
    ) -> UserLogin:
        """Verify credentials and issue one opaque, revocable App session token."""

        try:
            normalized_email = normalize_login_email(email)
            secret_bytes = _validate_secret(secret)
        except UserAccountError:
            normalized_email = ""
            secret_bytes = str(secret or "").encode("utf-8")[:4096] or b"invalid"
        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM user_accounts WHERE email_normalized = ?",
                (normalized_email,),
            ).fetchone()
        encoded_hash = str(row["secret_hash"]) if row is not None else _DUMMY_SECRET_HASH
        valid = _verify_secret(secret_bytes, encoded_hash)
        if row is None or not valid or str(row["status"]) != "active":
            self._audit(
                user_id=str(row["user_id"]) if row is not None else None,
                action="auth.login",
                outcome="denied",
            )
            raise UserAccountError("invalid_credentials", "The email or secret is invalid.")
        if ttl_seconds <= 0 or ttl_seconds > _DEFAULT_SESSION_TTL_SECONDS:
            raise UserAccountError("invalid_session_ttl", "Session lifetime is outside the allowed range.")
        account = self._account_from_row(row)
        now_ms = self._clock_ms()
        expires_at_ms = now_ms + ttl_seconds * 1000
        access_token = f"{_SESSION_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
        with self._lock, _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO user_sessions (
                    session_id, token_hash, user_id, issued_at_ms,
                    expires_at_ms, revoked_at_ms, last_seen_at_ms
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    f"usession_{secrets.token_hex(12)}",
                    _token_hash(access_token),
                    account.user_id,
                    now_ms,
                    expires_at_ms,
                    now_ms,
                ),
            )
        self.identity_store.put_principal(self._principal_for(account, authenticated=True))
        self._audit(user_id=account.user_id, action="auth.login", outcome="succeeded")
        return UserLogin(account=account, access_token=access_token, expires_at_ms=expires_at_ms)

    def resolve_session(
        self,
        access_token: str,
        *,
        keepalive_if: Callable[[str, str], bool] | None = None,
    ) -> UserAccount | None:
        """Resolve one active token without treating background requests as activity.

        ``keepalive_if`` is reserved for a server-owned Run that was started by
        the same product session. It may advance an otherwise elapsed deadline;
        ordinary callers cannot revive an idle session.
        """

        token = str(access_token or "")
        if not token.startswith(_SESSION_TOKEN_PREFIX):
            return None
        now_ms = self._clock_ms()
        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT a.user_id, a.email_normalized, a.privilege_level, a.status,
                       a.created_at_ms, a.updated_at_ms, s.session_id, s.issued_at_ms,
                       s.expires_at_ms, s.revoked_at_ms, s.last_seen_at_ms
                FROM user_sessions AS s
                JOIN user_accounts AS a ON a.user_id = s.user_id
                WHERE s.token_hash = ?
                """,
                (_token_hash(token),),
            ).fetchone()
            if row is None or row["revoked_at_ms"] is not None or str(row["status"]) != "active":
                return None
            session_id = str(row["session_id"])
            if self._effective_idle_deadline(row) <= now_ms:
                if keepalive_if is None or not keepalive_if(str(row["user_id"]), session_id):
                    return None
                deadline_ms = now_ms + _DEFAULT_SESSION_IDLE_MS
                conn.execute(
                    "UPDATE user_sessions SET last_seen_at_ms = ?, expires_at_ms = ? "
                    "WHERE session_id = ? AND revoked_at_ms IS NULL",
                    (now_ms, deadline_ms, session_id),
                )
        return self._account_from_row(row)

    def session_reference(self, access_token: str) -> str | None:
        """Return the opaque database session ID for one non-revoked token."""

        token = str(access_token or "")
        if not token.startswith(_SESSION_TOKEN_PREFIX):
            return None
        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT s.session_id
                FROM user_sessions AS s
                JOIN user_accounts AS a ON a.user_id = s.user_id
                WHERE s.token_hash = ? AND s.revoked_at_ms IS NULL AND a.status = 'active'
                """,
                (_token_hash(token),),
            ).fetchone()
        return str(row["session_id"]) if row is not None else None

    def session_expires_at_ms(self, access_token: str) -> int | None:
        """Return the effective deadline for one currently usable session."""

        token = str(access_token or "")
        if not token.startswith(_SESSION_TOKEN_PREFIX):
            return None
        now_ms = self._clock_ms()
        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT s.expires_at_ms, s.last_seen_at_ms, s.revoked_at_ms, a.status
                FROM user_sessions AS s
                JOIN user_accounts AS a ON a.user_id = s.user_id
                WHERE s.token_hash = ?
                """,
                (_token_hash(token),),
            ).fetchone()
        if (
            row is None
            or row["revoked_at_ms"] is not None
            or str(row["status"]) != "active"
        ):
            return None
        deadline_ms = self._effective_idle_deadline(row)
        return deadline_ms if deadline_ms > now_ms else None

    def record_activity(self, access_token: str) -> int | None:
        """Advance one usable session after explicit, trusted user input."""

        token = str(access_token or "")
        if not token.startswith(_SESSION_TOKEN_PREFIX):
            return None
        now_ms = self._clock_ms()
        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT s.session_id, s.expires_at_ms, s.last_seen_at_ms,
                       s.revoked_at_ms, a.status
                FROM user_sessions AS s
                JOIN user_accounts AS a ON a.user_id = s.user_id
                WHERE s.token_hash = ?
                """,
                (_token_hash(token),),
            ).fetchone()
            if (
                row is None
                or row["revoked_at_ms"] is not None
                or str(row["status"]) != "active"
                or self._effective_idle_deadline(row) <= now_ms
            ):
                return None
            deadline_ms = now_ms + _DEFAULT_SESSION_IDLE_MS
            conn.execute(
                "UPDATE user_sessions SET last_seen_at_ms = ?, expires_at_ms = ? "
                "WHERE session_id = ? AND revoked_at_ms IS NULL",
                (now_ms, deadline_ms, str(row["session_id"])),
            )
        return deadline_ms

    def record_run_activity(self, session_id: str, *, user_id: str) -> bool:
        """Advance exactly the product session that owns a server-side Run."""

        normalized_session_id = str(session_id or "").strip()
        normalized_user_id = str(user_id or "").strip()
        if not normalized_session_id or not normalized_user_id:
            return False
        now_ms = self._clock_ms()
        deadline_ms = now_ms + _DEFAULT_SESSION_IDLE_MS
        with self._lock, _connect(self._db_path) as conn:
            account = conn.execute(
                "SELECT status FROM user_accounts WHERE user_id = ?",
                (normalized_user_id,),
            ).fetchone()
            if account is None or str(account["status"]) != "active":
                return False
            cursor = conn.execute(
                """
                UPDATE user_sessions
                SET last_seen_at_ms = ?, expires_at_ms = ?
                WHERE session_id = ? AND user_id = ? AND revoked_at_ms IS NULL
                """,
                (now_ms, deadline_ms, normalized_session_id, normalized_user_id),
            )
        return cursor.rowcount == 1

    def logout(self, access_token: str) -> bool:
        """Revoke one App session token, including an already elapsed session."""

        token = str(access_token or "")
        if not token.startswith(_SESSION_TOKEN_PREFIX):
            return False
        now_ms = self._clock_ms()
        with self._lock, _connect(self._db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE user_sessions SET revoked_at_ms = ?
                WHERE token_hash = ? AND revoked_at_ms IS NULL
                """,
                (now_ms, _token_hash(token)),
            )
        revoked = cursor.rowcount == 1
        if revoked:
            self._audit(user_id=None, action="auth.logout", outcome="succeeded")
        return revoked

    @staticmethod
    def _principal_for(account: UserAccount, *, authenticated: bool) -> ResolvedPrincipal:
        """Project one product account into the shared Runtime Principal model."""

        return ResolvedPrincipal(
            principal_id=account.user_id,
            principal_type="human",
            privilege_level=account.privilege_level,
            account_kind="product_user",
            display_name=account.email,
            authenticated=authenticated,
            external_subject_id=account.user_id,
            external_display_id=account.email,
            metadata={"source": "openteamwork_account", "status": account.status},
        )


__all__ = [
    "LoginRateLimiter",
    "USER_PRIVILEGE_LEVELS",
    "UserAccount",
    "UserAccountError",
    "UserAccountService",
    "UserLogin",
    "normalize_login_email",
    "privilege_allows",
]
