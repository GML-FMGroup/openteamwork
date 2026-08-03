"""Strict first-run setup request models."""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator

from openppx.config import AgentConfig, NodeConfig, SecretRef, StrictConfigModel
from openppx.modeling import ModelProfile


class SetupSecretInput(StrictConfigModel):
    """Optional protected value supplied only to the SecretStore boundary."""

    ref: SecretRef
    value: SecretStr


class SetupExpectedRevisions(StrictConfigModel):
    """Optimistic preconditions for every persisted setup resource."""

    node: str | None = None
    agent: str | None = None
    profile: str | None = None


class SetupApplyRequest(StrictConfigModel):
    """Complete desired baseline used by CLI and Desktop first-run flows."""

    node: NodeConfig
    agent: AgentConfig
    profile: ModelProfile
    secret: SetupSecretInput | None = None
    expected_revisions: SetupExpectedRevisions = Field(default_factory=SetupExpectedRevisions)

    @model_validator(mode="after")
    def resources_must_form_one_ready_baseline(self) -> "SetupApplyRequest":
        """Reject disconnected resources before any side effect occurs."""
        agent_id = self.agent.metadata.name
        profile_id = self.profile.metadata.name
        if agent_id not in self.node.spec.enabled_agents:
            raise ValueError("node enabledAgents must include the setup Agent")
        if self.agent.spec.model_policy.default_profile != profile_id:
            raise ValueError("agent defaultProfile must reference the setup Model Profile")
        if self.secret is not None and self.profile.spec.credential != self.secret.ref:
            raise ValueError("setup Secret reference must match the Model Profile credential")
        return self
