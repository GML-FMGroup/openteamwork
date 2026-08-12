from __future__ import annotations

import base64
import io
import json
import os
import time
from pathlib import Path

from oauth_cli_kit import OAuthToken
from oauth_cli_kit.storage import FileTokenStorage

import openppx.config  # Establish the existing Config -> Modeling import order.
from openppx.core.codex_auth import (
    CodexCliTokenStorage,
    CodexDeviceLoginManager,
    project_codex_auth_status,
)
from openppx.modeling import ModelCatalog, ProviderAccessService


def _jwt(expires_at: int, marker: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": expires_at, "marker": marker}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


def _write_codex_auth(codex_home: Path, *, marker: str = "new") -> str:
    access = _jwt(int(time.time()) + 3600, marker)
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": access,
                    "refresh_token": f"refresh-{marker}",
                    "account_id": "account-fixture",
                },
            }
        ),
        encoding="utf-8",
    )
    return access


def test_codex_cli_newer_login_replaces_stale_oauth_cache(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    cache = FileTokenStorage(data_dir=tmp_path / "oauth", token_filename="codex.json", import_codex_cli=False)
    cache.save(
        OAuthToken(
            access=_jwt(int(time.time()) + 7200, "old"),
            refresh="refresh-old",
            expires=(int(time.time()) + 7200) * 1000,
            account_id="account-fixture",
        )
    )
    old_time = time.time() - 120
    os.utime(cache.get_token_path(), (old_time, old_time))
    expected_access = _write_codex_auth(codex_home)

    storage = CodexCliTokenStorage(codex_home, cache)
    token = storage.load()

    assert token.access == expected_access
    assert cache.load().access == expected_access
    assert storage.source_for(token) == "codex_cli"
    assert expected_access not in repr(storage)


def test_codex_cli_login_remains_usable_when_optional_cache_is_read_only(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    expected_access = _write_codex_auth(codex_home)

    class ReadOnlyCache:
        def get_token_path(self) -> Path:
            return tmp_path / "oauth" / "codex.json"

        def load(self):
            return None

        def save(self, _token) -> None:
            raise OSError("read only")

    storage = CodexCliTokenStorage(codex_home, ReadOnlyCache())

    assert storage.sync_from_codex_cli().access == expected_access
    assert storage.load().access == expected_access


def test_codex_model_catalog_projects_only_safe_supported_models(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-current",
                        "display_name": "GPT Current",
                        "description": "Current model",
                        "visibility": "list",
                        "supported_in_api": True,
                        "context_window": 272000,
                        "default_reasoning_level": "medium",
                        "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}],
                        "model_messages": {"instructions_template": "must-never-be-projected"},
                    },
                    {"slug": "hidden", "visibility": "hidden", "supported_in_api": True},
                    {"slug": "unsupported", "visibility": "list", "supported_in_api": False},
                ]
            }
        ),
        encoding="utf-8",
    )

    snapshot = ModelCatalog(codex_home=codex_home).list_models("openai_codex")

    assert snapshot.authoritative is True
    assert snapshot.source == "codex_cli"
    assert [item.model_id for item in snapshot.models] == ["openai-codex/gpt-current"]
    assert snapshot.models[0].reasoning_efforts == ("low", "medium")
    assert snapshot.models[0].context_window_tokens == 272000
    assert ModelCatalog(codex_home=codex_home).context_window_tokens(
        "openai_codex", "openai-codex/gpt-current"
    ) == 272000
    assert "must-never-be-projected" not in repr(snapshot)


def test_provider_default_catalog_projects_bundled_context_window() -> None:
    snapshot = ModelCatalog().list_models("google")

    assert snapshot.models[0].model_id == "gemini-3-flash-preview"
    assert snapshot.models[0].context_window_tokens == 1_048_576
    assert ModelCatalog().context_window_tokens("deepseek", "deepseek-chat") == 65_536


class _CompletedProcess:
    def __init__(self, *_args, **_kwargs) -> None:
        self.stdout = io.StringIO(
            "Open this link\nhttps://auth.openai.com/codex/device\n"
            "Enter this one-time code\nABCD-EFGH\n"
        )
        self._return_code: int | None = None

    def wait(self) -> int:
        self._return_code = 0
        return 0

    def poll(self) -> int | None:
        return self._return_code

    def terminate(self) -> None:
        self._return_code = -15


def test_device_login_projects_only_public_device_flow_fields(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    _write_codex_auth(codex_home)
    cache = FileTokenStorage(data_dir=tmp_path / "oauth", token_filename="codex.json", import_codex_cli=False)
    storage = CodexCliTokenStorage(codex_home, cache)
    manager = CodexDeviceLoginManager(
        storage,
        codex_executable="codex-fixture",
        process_factory=_CompletedProcess,
    )
    service = ProviderAccessService(
        ModelCatalog(codex_home=codex_home),
        codex_storage=storage,
        codex_login=manager,
    )

    result = service.begin_auth("openai_codex")

    assert result["state"] == "authenticated"
    assert result["session"]["verificationUrl"] == "https://auth.openai.com/codex/device"
    assert result["session"]["userCode"] == "ABCD-EFGH"
    assert "refresh-new" not in repr(result)
    assert "account-fixture" not in repr(result)


def test_pending_device_login_overrides_expired_cached_token(tmp_path: Path) -> None:
    cache = FileTokenStorage(data_dir=tmp_path / "oauth", token_filename="codex.json", import_codex_cli=False)
    cache.save(
        OAuthToken(
            access=_jwt(int(time.time()) - 60, "expired"),
            refresh="refresh-expired",
            expires=(int(time.time()) - 60) * 1000,
            account_id="account-fixture",
        )
    )
    storage = CodexCliTokenStorage(tmp_path / "codex", cache)

    result = project_codex_auth_status(
        storage,
        session={"id": "login-fixture", "state": "pending"},
    )

    assert result["state"] == "pending"
    assert result["session"] == {"id": "login-fixture", "state": "pending"}
