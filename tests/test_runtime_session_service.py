"""Tests for SQLite session service factory."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openppx.runtime.session_service import (
    SessionConfig,
    create_session_service,
    load_session_config,
)


class SessionServiceFactoryTests(unittest.TestCase):
    def test_load_uses_explicit_node_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            node_root = Path(tmp) / "node"
            cfg = load_session_config(node_root)
            self.assertTrue(cfg.db_url.startswith("sqlite+aiosqlite:///"))
            db_file = Path(cfg.db_url.replace("sqlite+aiosqlite:///", "", 1))
            self.assertEqual(db_file.parent, node_root.resolve() / "database")
            self.assertTrue((node_root / "database" / ".adk_meta.json").exists())

    def test_load_defaults_to_conventional_node_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("openppx.runtime.paths.Path.home", return_value=Path(tmp)):
                cfg = load_session_config()
            self.assertIn(".openteamwork/database/sessions.db", cfg.db_url)

    def test_create_sqlite_backend_uses_db_url(self) -> None:
        db_url = "sqlite+aiosqlite:////tmp/sessions.db"
        with patch("openppx.runtime.session_service.DatabaseSessionService") as mocked:
            mocked.return_value = object()
            out = create_session_service(SessionConfig(db_url=db_url))
            self.assertIsNotNone(out)
            mocked.assert_called_once_with(db_url)

    def test_create_sqlite_backend_stamps_database_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "agent" / "database" / "sessions.db"
            db_url = f"sqlite+aiosqlite:///{db_path}"
            with patch("openppx.runtime.session_service.DatabaseSessionService") as mocked:
                mocked.return_value = object()
                out = create_session_service(SessionConfig(db_url=db_url))

            self.assertIsNotNone(out)
            self.assertTrue((db_path.parent / ".adk_meta.json").exists())


if __name__ == "__main__":
    unittest.main()
