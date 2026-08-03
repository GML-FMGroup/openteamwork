"""Transport-independent Action catalog and execution boundary."""

from .executor import ActionExecutor
from .models import (
    ActionCatalogEntry,
    ActionContext,
    ActionError,
    ActionFailure,
    ActionOutcome,
    ActionSpec,
)
from .registry import ActionRegistrationError, ActionRegistry

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
]
