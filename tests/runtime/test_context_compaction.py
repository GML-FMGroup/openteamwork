"""Tests for model-aware context compaction planning."""

from __future__ import annotations

from openppx.config import NodeContextCompactionSpec
from openppx.runtime.context_compaction import resolve_context_compaction_plan


def test_known_context_window_uses_configured_percentage() -> None:
    plan = resolve_context_compaction_plan(
        NodeContextCompactionSpec(enabled=True, thresholdPercent=70),
        profile_context_window_tokens=100_000,
        catalog_context_window_tokens=120_000,
    )

    assert plan.strategy == "token_threshold"
    assert plan.context_window_tokens == 100_000
    assert plan.context_window_source == "profile"
    assert plan.token_threshold == 70_000
    assert plan.event_retention_size == 12
    assert plan.compaction_interval is None


def test_catalog_window_is_used_when_profile_does_not_override_it() -> None:
    plan = resolve_context_compaction_plan(
        NodeContextCompactionSpec(),
        profile_context_window_tokens=None,
        catalog_context_window_tokens=272_000,
    )

    assert plan.context_window_source == "catalog"
    assert plan.token_threshold == 190_400


def test_unknown_context_window_uses_safe_invocation_fallback() -> None:
    plan = resolve_context_compaction_plan(
        NodeContextCompactionSpec(),
        profile_context_window_tokens=None,
        catalog_context_window_tokens=None,
    )

    assert plan.strategy == "invocation_fallback"
    assert plan.token_threshold is None
    assert plan.compaction_interval == 8
    assert plan.overlap_size == 1


def test_disabled_context_compaction_builds_no_strategy() -> None:
    plan = resolve_context_compaction_plan(
        NodeContextCompactionSpec(enabled=False),
        profile_context_window_tokens=100_000,
        catalog_context_window_tokens=None,
    )

    assert plan.strategy == "disabled"
    assert plan.token_threshold is None
    assert plan.compaction_interval is None
