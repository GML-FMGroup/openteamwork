"""Tests for runtime identity resolution helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openppx.runtime.identity_store import IdentityStore


class IdentityStoreTests(unittest.TestCase):
    def test_resolve_external_principal_strips_telegram_username_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = IdentityStore(db_path=Path(tmp) / "identity.db")

            principal = store.resolve_external_principal(
                source="telegram",
                sender_id="123456|@alice",
            )

        self.assertEqual(principal.principal_id, "human:telegram:123456")
        self.assertEqual(principal.external_subject_id, "123456")
        self.assertEqual(principal.external_display_id, "@alice")
        self.assertEqual(principal.display_name, "@alice")
        self.assertEqual(principal.privilege_level, "minimal")

    def test_resolve_external_principal_persists_across_store_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "identity.db"
            store_a = IdentityStore(db_path=db_path)
            principal_a = store_a.resolve_external_principal(
                source="telegram",
                sender_id="123456|@alice",
            )

            store_b = IdentityStore(db_path=db_path)
            principal_b = store_b.resolve_external_principal(
                source="telegram",
                sender_id="123456|@alice_new",
            )

        self.assertEqual(principal_a.principal_id, principal_b.principal_id)
        self.assertEqual(principal_b.principal_id, "human:telegram:123456")
        self.assertEqual(principal_b.display_name, "@alice_new")
        self.assertEqual(principal_b.external_display_id, "@alice_new")

    def test_resolve_service_principal_defaults_to_silent_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = IdentityStore(db_path=Path(tmp) / "identity.db")

            principal = store.resolve_service_principal("heartbeat")

        self.assertEqual(principal.principal_id, "heartbeat")
        self.assertEqual(principal.principal_type, "service")
        self.assertFalse(principal.memory_ingest_enabled)


if __name__ == "__main__":
    unittest.main()
