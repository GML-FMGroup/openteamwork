"""ADK Goal progress observation tests."""

from __future__ import annotations

from types import SimpleNamespace

from openppx.runtime.goal_supervisor import GoalSliceObserver


def _event(*, invocation_id: str, name: str, args: dict[str, object]) -> SimpleNamespace:
    """Build the minimal ADK-like event shape used by the observer."""
    payload = {
        "invocation_id": invocation_id,
        "content": {
            "parts": [
                {
                    "function_call": {
                        "id": "call-1",
                        "name": name,
                        "args": args,
                    }
                }
            ]
        },
    }
    return SimpleNamespace(model_dump=lambda **_kwargs: payload)


def test_goal_slice_observer_captures_secret_free_action_fingerprint() -> None:
    observer = GoalSliceObserver()

    observer.observe(
        _event(
            invocation_id="inv-1",
            name="web_search",
            args={"query": "OpenPPX", "api_token": "secret-value"},
        )
    )
    observation = observer.snapshot()

    assert observation.invocation_id == "inv-1"
    assert observation.action_names == ("web_search",)
    assert len(observation.action_fingerprint) == 64
    assert "secret-value" not in observation.action_fingerprint


def test_goal_slice_observer_distinguishes_action_targets() -> None:
    first = GoalSliceObserver()
    second = GoalSliceObserver()
    first.observe(_event(invocation_id="inv-a", name="web_search", args={"query": "alpha"}))
    second.observe(_event(invocation_id="inv-b", name="web_search", args={"query": "beta"}))

    assert first.snapshot().action_fingerprint != second.snapshot().action_fingerprint


def test_goal_slice_observer_can_reset_between_continuations() -> None:
    observer = GoalSliceObserver()
    observer.observe(_event(invocation_id="inv-a", name="web_search", args={"query": "alpha"}))

    observer.reset()

    assert observer.snapshot().invocation_id == ""
    assert observer.snapshot().action_names == ()
    assert observer.snapshot().action_fingerprint == ""
