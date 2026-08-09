"""Google ADK Skill draft generator boundary tests."""

from __future__ import annotations

import types as pytypes

from google.genai import types

from openppx.skill_authoring import AdkSkillDraftGenerator, VisibleHistoryMessage


class _Runner:
    async def run_async(self, **_kwargs):
        yield pytypes.SimpleNamespace(
            error_code=None,
            error_message=None,
            content=types.Content(
                role="model",
                parts=[
                    types.Part.from_text(
                        text=(
                            '{"status":"ready_for_review","skillId":"report-review",'
                            '"displayName":"Report review","description":"Review a report consistently.",'
                            '"triggers":["A report needs review."],"inputs":["Draft report"],'
                            '"outputs":["Review notes"],"steps":[{"text":"Check the report totals.",'
                            '"evidenceInvocationIds":["inv-1"]}],"limitations":[],'
                            '"unresolvedQuestions":[]}'
                        )
                    )
                ],
            ),
        )


def test_generator_builds_a_toolless_structured_single_turn_agent() -> None:
    captured = {}

    def runner_factory(**kwargs):
        captured.update(kwargs)
        return _Runner(), object()

    generator = AdkSkillDraftGenerator(runner_factory=runner_factory)
    proposal = generator.generate(
        model="fixture-model",
        messages=(
            VisibleHistoryMessage(
                role="user",
                text="Review this report.",
                invocation_id="inv-1",
            ),
            VisibleHistoryMessage(
                role="assistant",
                text="The totals were checked.",
                invocation_id="inv-1",
            ),
        ),
        focus="report review",
        previous=None,
        revision_notes="",
    )

    agent = captured["agent"]
    assert agent.tools == []
    assert agent.output_schema is not None
    assert agent.include_contents == "none"
    assert agent.mode == "single_turn"
    assert captured["profile"] == "ephemeral"
    assert proposal.skill_id == "report-review"
    assert proposal.steps[0].evidence_invocation_ids == ["inv-1"]
