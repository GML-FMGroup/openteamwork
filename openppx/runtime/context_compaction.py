"""Model-aware context compaction planning for snapshot runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from google.adk.apps.app import EventsCompactionConfig

from openppx.config import NodeContextCompactionSpec


DEFAULT_EVENT_RETENTION_SIZE = 12
UNKNOWN_WINDOW_COMPACTION_INTERVAL = 8
UNKNOWN_WINDOW_OVERLAP_SIZE = 1

ContextCompactionStrategy = Literal[
    "disabled",
    "token_threshold",
    "invocation_fallback",
]


@dataclass(frozen=True, slots=True)
class ContextCompactionPlan:
    """Resolved, secret-free compaction policy for one selected model."""

    enabled: bool
    threshold_percent: int
    strategy: ContextCompactionStrategy
    context_window_tokens: int | None = None
    context_window_source: Literal["profile", "catalog"] | None = None
    token_threshold: int | None = None
    event_retention_size: int | None = None
    compaction_interval: int | None = None
    overlap_size: int | None = None


def resolve_context_compaction_plan(
    configuration: NodeContextCompactionSpec,
    *,
    profile_context_window_tokens: int | None,
    catalog_context_window_tokens: int | None,
) -> ContextCompactionPlan:
    """Resolve percentage configuration into an ADK-compatible strategy."""
    if not configuration.enabled:
        return ContextCompactionPlan(
            enabled=False,
            threshold_percent=configuration.threshold_percent,
            strategy="disabled",
        )
    context_window_tokens = profile_context_window_tokens or catalog_context_window_tokens
    if context_window_tokens is not None:
        source: Literal["profile", "catalog"] = (
            "profile" if profile_context_window_tokens is not None else "catalog"
        )
        return ContextCompactionPlan(
            enabled=True,
            threshold_percent=configuration.threshold_percent,
            strategy="token_threshold",
            context_window_tokens=context_window_tokens,
            context_window_source=source,
            token_threshold=max(1, context_window_tokens * configuration.threshold_percent // 100),
            event_retention_size=DEFAULT_EVENT_RETENTION_SIZE,
        )
    return ContextCompactionPlan(
        enabled=True,
        threshold_percent=configuration.threshold_percent,
        strategy="invocation_fallback",
        compaction_interval=UNKNOWN_WINDOW_COMPACTION_INTERVAL,
        overlap_size=UNKNOWN_WINDOW_OVERLAP_SIZE,
    )


def build_events_compaction_config(
    plan: ContextCompactionPlan,
    *,
    summarizer: Any | None,
) -> EventsCompactionConfig | None:
    """Build ADK EventsCompactionConfig from one resolved product plan."""
    if plan.strategy == "disabled":
        return None
    if plan.strategy == "token_threshold":
        return EventsCompactionConfig(
            token_threshold=plan.token_threshold,
            event_retention_size=plan.event_retention_size,
            summarizer=summarizer,
        )
    return EventsCompactionConfig(
        compaction_interval=plan.compaction_interval,
        overlap_size=plan.overlap_size,
        summarizer=summarizer,
    )
