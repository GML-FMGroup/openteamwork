"""Typed source of truth for the OpenPPX Client API wire contract."""

from .mapper import ContractMapper
from .models import (
    ActionCatalogItem,
    ActionCatalogPayload,
    ActionInvokeRequest,
    ClientContractBundle,
    ClientEnvelope,
    ErrorEnvelope,
    ExtensionDetailPayload,
    ExtensionListPayload,
    ExtensionPreviewPayload,
    ExtensionReadiness,
    ExtensionSourceSummary,
    ExtensionSummaryItem,
    SlashCommandInvokeInput,
    SlashCommandInvokeResult,
    SlashCommandItem,
    StableError,
    SuccessEnvelope,
)
from .schema import export_client_contract

__all__ = [
    "ActionCatalogItem",
    "ActionCatalogPayload",
    "ActionInvokeRequest",
    "ClientContractBundle",
    "ClientEnvelope",
    "ContractMapper",
    "ErrorEnvelope",
    "ExtensionDetailPayload",
    "ExtensionListPayload",
    "ExtensionPreviewPayload",
    "ExtensionReadiness",
    "ExtensionSourceSummary",
    "ExtensionSummaryItem",
    "SlashCommandInvokeInput",
    "SlashCommandInvokeResult",
    "SlashCommandItem",
    "StableError",
    "SuccessEnvelope",
    "export_client_contract",
]
