"""Persistent Extension health observation tests."""

from openppx.extensions import ExtensionHealthStore


def test_health_store_retains_success_failure_and_redacts_messages(tmp_path) -> None:
    store = ExtensionHealthStore(tmp_path / "health.db")
    base = {
        "kind": "mcp",
        "id": "docs",
        "revision": "sha256:abc",
        "transport": "streamable_http",
        "elapsedMs": 12,
        "attempts": 1,
        "toolCount": 2,
        "issues": [],
    }
    store.record({**base, "ready": True, "status": "ok", "message": ""})
    store.record(
        {
            **base,
            "ready": False,
            "status": "error",
            "issues": ["connection_authentication"],
            "errorKind": "authentication",
            "message": "Authorization: private-token",
        }
    )

    recent = store.recent("mcp", "docs")
    summary = store.summary("mcp", "docs")

    assert len(recent) == 2
    assert "private-token" not in recent[0].message
    assert "[REDACTED]" in recent[0].message
    assert summary["lastSuccessAtMs"] is not None
    assert summary["lastFailureAtMs"] is not None
    assert summary["consecutiveFailures"] == 1
