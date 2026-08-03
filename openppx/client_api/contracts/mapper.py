"""Application outcome to common wire-envelope mapping."""

from __future__ import annotations

from openppx.actions import ActionOutcome

from .models import ClientEnvelope, ErrorEnvelope, StableError, SuccessEnvelope


class ContractMapper:
    """Map transport-independent application outcomes to strict wire envelopes."""

    def __init__(self, *, protocol_version: int = 1) -> None:
        if protocol_version < 1:
            raise ValueError("protocol_version must be positive")
        self.protocol_version = protocol_version

    def from_outcome(
        self,
        outcome: ActionOutcome,
        *,
        request_id: str,
        correlation_id: str,
    ) -> ClientEnvelope:
        """Build a mutually exclusive success or error envelope."""
        if outcome.ok:
            assert outcome.data is not None
            return SuccessEnvelope(
                protocol_version=self.protocol_version,
                request_id=request_id,
                correlation_id=correlation_id,
                ok=True,
                result=outcome.data,
            )
        assert outcome.error is not None
        return ErrorEnvelope(
            protocol_version=self.protocol_version,
            request_id=request_id,
            correlation_id=correlation_id,
            ok=False,
            error=StableError(
                code=outcome.error.code,
                message=outcome.error.message,
                retryable=outcome.error.retryable,
                details=outcome.error.details,
            ),
        )
