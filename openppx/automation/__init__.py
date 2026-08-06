"""User Automation domain and ADK-native execution service."""

from .service import AutomationService, AutomationTemplate, DEFAULT_AUTOMATION_TEMPLATES
from .store import (
    AutomationConflictError,
    AutomationDefinition,
    AutomationEvent,
    AutomationNotFoundError,
    AutomationRun,
    AutomationStateError,
    AutomationStore,
    AutomationStoreError,
    AutomationTrigger,
)

__all__ = [
    "AutomationConflictError",
    "AutomationDefinition",
    "AutomationEvent",
    "AutomationNotFoundError",
    "AutomationRun",
    "AutomationService",
    "AutomationStateError",
    "AutomationStore",
    "AutomationStoreError",
    "AutomationTemplate",
    "AutomationTrigger",
    "DEFAULT_AUTOMATION_TEMPLATES",
]
