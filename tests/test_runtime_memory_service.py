"""Tests for ADK memory service factory."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from google.adk.memory import InMemoryMemoryService

from openppx.runtime.markdown_memory_service import MarkdownMemoryService
from openppx.runtime.memory_service import (
    MemoryConfig,
    create_memory_service,
    load_memory_config,
)
from openppx.runtime.sqlite_memory_service import SQLiteMemoryService


class MemoryServiceFactoryTests(unittest.TestCase):
    def test_load_memory_config_defaults_to_enabled_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("openppx.runtime.paths.Path.home", return_value=Path(tmp)):
                cfg = load_memory_config()

        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.backend, "sqlite")
        self.assertIn(".openppx/database/memory.db", cfg.sqlite_db_path)
        self.assertIn(".openppx/memory", cfg.markdown_dir)

    def test_load_memory_config_uses_explicit_node_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            node_root = Path(tmp) / "node"
            cfg = load_memory_config(node_root)
            self.assertEqual(cfg.markdown_dir, str(node_root.resolve() / "memory"))
            self.assertEqual(cfg.sqlite_db_path, str(node_root.resolve() / "database" / "memory.db"))

    def test_create_memory_service_can_be_disabled(self) -> None:
        service = create_memory_service(MemoryConfig(False, "in_memory", ""))
        self.assertIsNone(service)

    def test_create_memory_service_uses_sqlite_backend(self) -> None:
        service = create_memory_service(
            MemoryConfig(
                enabled=True,
                backend="sqlite",
                markdown_dir="/tmp/unused-memory",
                sqlite_db_path="/tmp/openppx-memory.db",
            )
        )
        self.assertIsInstance(service, SQLiteMemoryService)

    def test_create_memory_service_uses_in_memory_when_requested(self) -> None:
        service = create_memory_service(MemoryConfig(True, "in_memory", "/tmp/memory"))
        self.assertIsInstance(service, InMemoryMemoryService)

    def test_create_memory_service_falls_back_to_in_memory_for_unknown_backend(self) -> None:
        service = create_memory_service(
            MemoryConfig(
                enabled=True,
                backend="unknown_backend",
                markdown_dir="/tmp/memory",
            )
        )
        self.assertIsInstance(service, InMemoryMemoryService)

    def test_create_memory_service_uses_markdown_backend(self) -> None:
        service = create_memory_service(
            MemoryConfig(
                enabled=True,
                backend="markdown",
                markdown_dir="/tmp/openppx_md_memory",
            )
        )
        self.assertIsInstance(service, MarkdownMemoryService)


if __name__ == "__main__":
    unittest.main()
