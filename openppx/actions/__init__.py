"""Transport-independent Action catalog and execution boundary."""

from .executor import ActionExecutor
from .models import (
    ActionCatalogEntry,
    ActionContext,
    ActionError,
    ActionFailure,
    ActionOutcome,
    ActionSpec,
    SlashCommandSpec,
    SlashInvocationContext,
)
from .registry import ActionRegistrationError, ActionRegistry, ResolvedSlashCommand, SlashCommandError

__all__ = [
    "ActionCatalogEntry",
    "ActionContext",
    "ActionError",
    "ActionExecutor",
    "ActionFailure",
    "ActionOutcome",
    "ActionRegistrationError",
    "ActionRegistry",
    "ActionSpec",
    "ResolvedSlashCommand",
    "SlashCommandError",
    "SlashCommandSpec",
    "SlashInvocationContext",
]
