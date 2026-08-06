from __future__ import annotations

from openppx.runtime.trace import redact_trace_text, structured_runtime_error


def test_trace_redaction_removes_credentials() -> None:
    value = redact_trace_text("token=secret-value sk-abcdefghijklmnopqrstuvwxyz")

    assert "secret-value" not in value
    assert "sk-abc" not in value
    assert "<redacted>" in value


def test_structured_runtime_error_is_stable_and_actionable() -> None:
    error = structured_runtime_error(TimeoutError("provider token=do-not-log timed out"))

    assert error["code"] == "timeout"
    assert error["rootCause"] == "TimeoutError"
    assert error["retryable"] is True
    assert "do-not-log" not in error["message"]
    assert error["userAction"]
