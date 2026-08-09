"""Strict resources for conversation-derived Skill drafts and provenance."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from openppx.config.models import ResourceMetadata, ResourceName, StrictConfigModel


VisibleDraftText = Annotated[str, StringConstraints(min_length=1, max_length=4_096)]
VisibleHistoryText = Annotated[str, StringConstraints(min_length=1, max_length=32_768)]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class VisibleHistoryMessage(StrictConfigModel):
    """One redacted visible Session message supplied to the draft generator."""

    role: Literal["user", "assistant"]
    text: VisibleHistoryText
    invocation_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    timestamp: Annotated[str, StringConstraints(max_length=64)] | None = None


class SkillDraftStep(StrictConfigModel):
    """One procedural step grounded in visible Session evidence."""

    text: VisibleDraftText
    evidence_invocation_ids: list[
        Annotated[str, StringConstraints(min_length=1, max_length=256)]
    ] = Field(min_length=1, max_length=20)

    @field_validator("evidence_invocation_ids")
    @classmethod
    def evidence_must_be_unique(cls, value: list[str]) -> list[str]:
        """Keep source references deterministic and reviewable."""
        if len(value) != len(set(value)):
            raise ValueError("evidence invocation IDs must be unique")
        return value


class SkillDraftProposal(StrictConfigModel):
    """Tool-less model output reviewed before any Skill is installed."""

    status: Literal["ready_for_review", "needs_input"]
    skill_id: ResourceName
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    description: Annotated[str, StringConstraints(min_length=1, max_length=2_048)]
    triggers: list[VisibleDraftText] = Field(min_length=1, max_length=12)
    inputs: list[VisibleDraftText] = Field(default_factory=list, max_length=20)
    outputs: list[VisibleDraftText] = Field(default_factory=list, max_length=20)
    steps: list[SkillDraftStep] = Field(default_factory=list, max_length=30)
    limitations: list[VisibleDraftText] = Field(default_factory=list, max_length=20)
    unresolved_questions: list[VisibleDraftText] = Field(default_factory=list, max_length=12)

    @field_validator("triggers", "inputs", "outputs", "limitations", "unresolved_questions")
    @classmethod
    def list_entries_must_be_unique(cls, value: list[str]) -> list[str]:
        """Reject repeated draft prose that would create noisy Skills."""
        if len(value) != len(set(value)):
            raise ValueError("draft entries must be unique")
        return value

    @model_validator(mode="after")
    def status_matches_reviewability(self) -> "SkillDraftProposal":
        """Require usable steps for review and questions for incomplete drafts."""
        if self.status == "ready_for_review":
            if not self.steps:
                raise ValueError("reviewable drafts require at least one grounded step")
            if self.unresolved_questions:
                raise ValueError("reviewable drafts cannot retain unresolved questions")
        elif not self.unresolved_questions:
            raise ValueError("incomplete drafts must explain what input is missing")
        return self


class MakeSkillDraftRecord(StrictConfigModel):
    """Private, current-Session draft retained across review commands."""

    api_version: Literal["openppx.io/v1alpha1"] = "openppx.io/v1alpha1"
    kind: Literal["SkillDraft"] = "SkillDraft"
    draft_id: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
    agent_id: ResourceName
    user_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    session_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    focus: Annotated[str, StringConstraints(max_length=2_000)] = ""
    source_invocation_ids: list[
        Annotated[str, StringConstraints(min_length=1, max_length=256)]
    ] = Field(min_length=1, max_length=100)
    source_digest: Digest
    source_message_count: int = Field(ge=1, le=100)
    redaction_count: int = Field(ge=0)
    proposal: SkillDraftProposal
    revision: int = Field(ge=1)
    created_at: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    updated_at: Annotated[str, StringConstraints(min_length=1, max_length=64)]

    def to_payload(self) -> dict[str, object]:
        """Return the client-safe review projection without source transcript text."""
        proposal = self.proposal
        return {
            "draftId": self.draft_id,
            "status": proposal.status,
            "skillId": proposal.skill_id,
            "displayName": proposal.display_name,
            "description": proposal.description,
            "triggers": list(proposal.triggers),
            "inputs": list(proposal.inputs),
            "outputs": list(proposal.outputs),
            "steps": [
                {"text": step.text, "evidenceCount": len(step.evidence_invocation_ids)}
                for step in proposal.steps
            ],
            "limitations": list(proposal.limitations),
            "unresolvedQuestions": list(proposal.unresolved_questions),
            "sourceMessageCount": self.source_message_count,
            "redactionCount": self.redaction_count,
            "revision": self.revision,
            "confirmation": "unverified",
        }


class SkillProvenanceSpec(StrictConfigModel):
    """Private audit provenance retained after publication."""

    draft_id: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
    agent_id: ResourceName
    principal_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    session_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    source_invocation_ids: list[str]
    source_digest: Digest
    redaction_count: int = Field(ge=0)
    confirmation: Literal["user_confirmed"]
    skill_digest: Digest
    published_at: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class SkillProvenanceRecord(StrictConfigModel):
    """Node-owned publication evidence stored separately from `SKILL.md`."""

    api_version: Literal["openppx.io/v1alpha1"] = "openppx.io/v1alpha1"
    kind: Literal["SkillProvenance"] = "SkillProvenance"
    metadata: ResourceMetadata
    spec: SkillProvenanceSpec


class PublishedSkillResult(StrictConfigModel):
    """Client-safe outcome of one user-confirmed Skill publication."""

    skill_id: ResourceName
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    digest: Digest
    revision: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    agent_id: ResourceName
    confirmation: Literal["user_confirmed"] = "user_confirmed"

    def to_payload(self) -> dict[str, object]:
        """Return the stable command response projection."""
        return self.model_dump(mode="json", by_alias=True)


__all__ = [
    "MakeSkillDraftRecord",
    "PublishedSkillResult",
    "SkillDraftProposal",
    "SkillDraftStep",
    "SkillProvenanceRecord",
    "SkillProvenanceSpec",
    "VisibleHistoryMessage",
]
