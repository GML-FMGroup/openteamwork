"""Codex CLI credential bridge and bounded device-login lifecycle.

The Node owns this integration.  Renderer and Client processes receive only
non-sensitive status plus the public device-login URL/code; OAuth tokens never
cross the Action boundary.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_URL_PATTERN = re.compile(r"https://[^\s\x1b]+")
_DEVICE_CODE_PATTERN = re.compile(r"\b[A-Z0-9]{4,}(?:-[A-Z0-9]{4,})+\b")
_MAX_AUTH_FILE_BYTES = 1_000_000


class CodexAuthError(RuntimeError):
    """Stable Codex authentication error without credential material."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def default_codex_home() -> Path:
    """Return the canonical Codex CLI home for the Node operating-system user."""
    return Path.home() / ".codex"


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_AUTH_FILE_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _jwt_expiry_ms(token: str, fallback_ms: int) -> int:
    """Read a JWT expiry claim without validating or retaining token content."""
    try:
        payload = token.split(".", 2)[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        expires = int(claims["exp"]) * 1000
        return expires if expires > 0 else fallback_ms
    except (IndexError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return fallback_ms


class CodexCliTokenStorage:
    """oauth-cli-kit storage that follows the newest canonical Codex CLI login.

    oauth-cli-kit imports ``~/.codex/auth.json`` only when its own cache is
    absent.  This adapter also imports a newer Codex CLI login, while retaining
    a later oauth-cli-kit refresh in its protected cache.
    """

    def __init__(self, codex_home: Path | None = None, cache_storage: Any | None = None) -> None:
        try:
            from oauth_cli_kit.storage import FileTokenStorage
        except ImportError as exc:  # pragma: no cover - dependency validation owns this
            raise CodexAuthError("codex_auth_unavailable", "oauth-cli-kit is not installed on the Node.") from exc
        self.codex_home = (codex_home or default_codex_home()).expanduser()
        self._cache = cache_storage or FileTokenStorage(
            token_filename="codex.json",
            import_codex_cli=False,
        )

    @property
    def canonical_path(self) -> Path:
        """Return the Codex CLI credential path without reading it."""
        return self.codex_home / "auth.json"

    def get_token_path(self) -> Path:
        """Return oauth-cli-kit's protected cache path for locking."""
        return self._cache.get_token_path()

    def load(self) -> Any | None:
        """Return the newest valid-shaped credential source."""
        canonical = self._load_canonical()
        cached = self._cache.load()
        if canonical is None:
            return cached
        try:
            canonical_mtime = self.canonical_path.stat().st_mtime_ns
        except OSError:
            canonical_mtime = 0
        try:
            cache_mtime = self.get_token_path().stat().st_mtime_ns
        except OSError:
            cache_mtime = -1
        if cached is None or canonical_mtime > cache_mtime:
            try:
                self._cache.save(canonical)
            except OSError:
                # An unexpired canonical token is still usable when a locked-
                # down Node cannot update oauth-cli-kit's optional cache.
                pass
            return canonical
        return cached

    def save(self, token: Any) -> None:
        """Persist refreshed OAuth material only in oauth-cli-kit's cache."""
        self._cache.save(token)

    def sync_from_codex_cli(self) -> Any:
        """Force one canonical Codex CLI login into the OpenPPX runtime cache."""
        token = self._load_canonical()
        if token is None:
            raise CodexAuthError(
                "codex_login_required",
                "Codex is not signed in on this Node. Start the device-code sign-in below.",
            )
        try:
            self._cache.save(token)
        except OSError:
            # The canonical Codex CLI credential is authoritative; its optional
            # OpenPPX cache must not turn a successful device login into failure.
            pass
        return token

    def _load_canonical(self) -> Any | None:
        data = _read_json_object(self.canonical_path)
        tokens = data.get("tokens") if data else None
        if not isinstance(tokens, dict):
            return None
        access = tokens.get("access_token")
        refresh = tokens.get("refresh_token")
        account_id = tokens.get("account_id")
        if not all(isinstance(value, str) and value for value in (access, refresh, account_id)):
            return None
        try:
            from oauth_cli_kit import OAuthToken
        except ImportError as exc:  # pragma: no cover - dependency validation owns this
            raise CodexAuthError("codex_auth_unavailable", "oauth-cli-kit is not installed on the Node.") from exc
        try:
            fallback_ms = int(self.canonical_path.stat().st_mtime * 1000) + 60 * 60 * 1000
        except OSError:
            fallback_ms = int(time.time() * 1000) + 60 * 60 * 1000
        return OAuthToken(
            access=access,
            refresh=refresh,
            expires=_jwt_expiry_ms(access, fallback_ms),
            account_id=account_id,
        )

    def source_for(self, token: Any) -> str:
        """Identify the effective non-sensitive source for status projection."""
        canonical = self._load_canonical()
        if canonical is not None and getattr(canonical, "access", None) == getattr(token, "access", None):
            return "codex_cli"
        return "openppx_cache"


def get_codex_token(
    *,
    codex_home: Path | None = None,
    storage: CodexCliTokenStorage | None = None,
) -> Any:
    """Return an available Codex token after reconciling the CLI credential."""
    try:
        from oauth_cli_kit import get_token
    except ImportError as exc:  # pragma: no cover - dependency validation owns this
        raise CodexAuthError("codex_auth_unavailable", "oauth-cli-kit is not installed on the Node.") from exc
    resolved_storage = storage or CodexCliTokenStorage(codex_home)
    try:
        return get_token(storage=resolved_storage)
    except CodexAuthError:
        raise
    except Exception as exc:
        raise CodexAuthError(
            "codex_authentication_failed",
            "Codex authentication failed on this Node. Reconnect the Codex login and try again.",
        ) from exc


@dataclass(slots=True)
class _DeviceSession:
    id: str
    process: subprocess.Popen[str]
    state: str = "starting"
    verification_url: str | None = None
    user_code: str | None = None
    expires_at: str | None = None
    error: str | None = None


ProcessFactory = Callable[..., subprocess.Popen[str]]


class CodexDeviceLoginManager:
    """Own at most one bounded Codex CLI device-code login subprocess."""

    def __init__(
        self,
        token_storage: CodexCliTokenStorage,
        *,
        codex_executable: str | None = None,
        process_factory: ProcessFactory = subprocess.Popen,
        start_timeout_seconds: float = 15.0,
    ) -> None:
        self.token_storage = token_storage
        self.codex_executable = codex_executable
        self.process_factory = process_factory
        self.start_timeout_seconds = start_timeout_seconds
        self._condition = threading.Condition()
        self._session: _DeviceSession | None = None

    def begin(self) -> dict[str, object]:
        """Start device authorization and return its public URL and one-time code."""
        with self._condition:
            if self._session is not None and self._session.state in {"starting", "pending"}:
                return self._project(self._session)
            executable = self._resolve_executable()
            try:
                process = self.process_factory(
                    [executable, "login", "--device-auth"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                raise CodexAuthError(
                    "codex_cli_unavailable",
                    "Codex CLI could not be started on this Node.",
                ) from exc
            session = _DeviceSession(id=f"codex-login-{uuid.uuid4().hex}", process=process)
            self._session = session
            threading.Thread(target=self._observe, args=(session,), daemon=True).start()
            deadline = time.monotonic() + self.start_timeout_seconds
            while session.state == "starting" and time.monotonic() < deadline:
                self._condition.wait(timeout=max(0.0, deadline - time.monotonic()))
            if session.state == "starting":
                self._terminate(session)
                session.state = "failed"
                session.error = "Codex CLI did not return a device code in time."
            if session.state == "failed":
                raise CodexAuthError("codex_device_login_failed", session.error or "Codex device login could not start.")
            return self._project(session)

    def status(self) -> dict[str, object] | None:
        """Return the current public device-login session, if one exists."""
        with self._condition:
            return self._project(self._session) if self._session is not None else None

    def close(self) -> None:
        """Terminate a pending login when its owning Node shuts down."""
        with self._condition:
            if self._session is not None and self._session.state in {"starting", "pending"}:
                self._terminate(self._session)
                self._session.state = "cancelled"
                self._condition.notify_all()

    def _resolve_executable(self) -> str:
        if self.codex_executable:
            return self.codex_executable
        discovered = shutil.which("codex")
        if discovered:
            return discovered
        mac_bundle = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
        if mac_bundle.is_file():
            return str(mac_bundle)
        raise CodexAuthError(
            "codex_cli_unavailable",
            "Codex CLI is not installed on this Node.",
        )

    def _observe(self, session: _DeviceSession) -> None:
        stream = session.process.stdout
        if stream is not None:
            for raw_line in stream:
                line = _ANSI_ESCAPE.sub("", raw_line).strip()
                with self._condition:
                    if session.verification_url is None:
                        match = _URL_PATTERN.search(line)
                        if match:
                            session.verification_url = match.group(0).rstrip(".,)")
                    if session.user_code is None:
                        match = _DEVICE_CODE_PATTERN.search(line)
                        if match:
                            session.user_code = match.group(0)
                    if session.verification_url and session.user_code and session.state == "starting":
                        session.state = "pending"
                        session.expires_at = datetime.fromtimestamp(
                            time.time() + 15 * 60,
                            tz=timezone.utc,
                        ).isoformat()
                    self._condition.notify_all()
        return_code = session.process.wait()
        with self._condition:
            if session.state == "cancelled":
                return
            if return_code == 0:
                try:
                    self.token_storage.sync_from_codex_cli()
                except CodexAuthError:
                    session.state = "failed"
                    session.error = "Codex CLI finished but no usable login was found."
                else:
                    session.state = "completed"
            else:
                session.state = "failed"
                session.error = "Codex device login did not complete."
            self._condition.notify_all()

    @staticmethod
    def _terminate(session: _DeviceSession) -> None:
        if session.process.poll() is None:
            session.process.terminate()

    @staticmethod
    def _project(session: _DeviceSession) -> dict[str, object]:
        return {
            "id": session.id,
            "state": session.state,
            "verificationUrl": session.verification_url,
            "userCode": session.user_code,
            "expiresAt": session.expires_at,
            "error": session.error,
        }


def project_codex_auth_status(
    storage: CodexCliTokenStorage,
    session: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return a secret-free status for one Node-owned Codex login."""
    token = storage.load()
    session_pending = bool(session and session.get("state") in {"starting", "pending"})
    if token is None:
        state = "pending" if session_pending else "not_authenticated"
        return {
            "providerId": "openai_codex",
            "state": state,
            "source": None,
            "expiresAt": None,
            "loginMode": "device_code",
            "session": session,
        }
    expires_ms = int(getattr(token, "expires", 0) or 0)
    state = "pending" if session_pending else "authenticated" if expires_ms > int(time.time() * 1000) else "expired"
    return {
        "providerId": "openai_codex",
        "state": state,
        "source": storage.source_for(token),
        "expiresAt": datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc).isoformat() if expires_ms else None,
        "loginMode": "device_code",
        "session": session,
    }
