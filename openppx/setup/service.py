"""Transport-independent first-run setup workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, TypeVar

from openppx.config import (
    ConfigError,
    ConfigLoadError,
    ConfigService,
    FilesystemConfigRepository,
    SecretStore,
    SecretValue,
    config_revision,
)
from openppx.modeling import ModelCatalog, ModelProfileRepository

from .models import SetupApplyRequest
from .state import SetupStateRepository, configuration_issue, verification_issue


ResourceT = TypeVar("ResourceT")
SetupResourceStep = Literal["missing", "complete", "invalid"]


class SetupError(RuntimeError):
    """Stable setup failure that never retains credential material."""

    def __init__(self, code: str, message: str, *, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SetupApplyResult:
    """Non-sensitive revisions and runtime effect of one setup application."""

    node_revision: str
    agent_revision: str
    profile_revision: str
    secret_state: str
    restart_required: bool


class SetupService:
    """Validate and publish one complete Node/Agent/Model first-run baseline."""

    def __init__(
        self,
        repository: FilesystemConfigRepository,
        config_service: ConfigService,
        profiles: ModelProfileRepository,
        catalog: ModelCatalog,
        secrets: SecretStore,
    ) -> None:
        self.repository = repository
        self.config_service = config_service
        self.profiles = profiles
        self.catalog = catalog
        self.secrets = secrets
        self.state_repository = SetupStateRepository(repository.paths.node_root)

    def status(self) -> dict[str, object]:
        """Return readiness without raising for missing or invalid resources."""
        node, node_step, diagnostic = self._inspect_resource("node", self.repository.read_node)
        agent = None
        agent_step: SetupResourceStep = "missing"
        profile = None
        profile_step: SetupResourceStep = "missing"
        secret_state = "not_required"
        if node is not None and node.document.spec.enabled_agents:
            agent, agent_step, agent_diagnostic = self._inspect_resource(
                "agent",
                lambda: self.repository.read_agent(node.document.spec.enabled_agents[0]),
            )
            diagnostic = diagnostic or agent_diagnostic
        if agent is not None and agent.document.spec.model_policy.default_profile:
            profile, profile_step, profile_diagnostic = self._inspect_resource(
                "model",
                lambda: self.profiles.read_profile(agent.document.spec.model_policy.default_profile or ""),
            )
            diagnostic = diagnostic or profile_diagnostic
        if profile is not None and profile.document.spec.credential is not None:
            secret_state = self.secrets.status(profile.document.spec.credential).state

        configured = node is not None and agent is not None and profile is not None and secret_state in {
            "available",
            "not_required",
        }
        verification = "not_started"
        if configured:
            try:
                record = self.state_repository.read()
            except ConfigLoadError as exc:
                if exc.kind != "not_found":
                    verification = "invalid"
                    diagnostic = diagnostic or verification_issue(exc)
            else:
                if record.execution_fingerprint is not None:
                    current_fingerprint = self._execution_fingerprint(
                        node.document.model_dump(mode="json", by_alias=True),
                        agent.document.model_dump(mode="json", by_alias=True),
                        profile.document.model_dump(mode="json", by_alias=True),
                    )
                    verification = (
                        "verified" if record.execution_fingerprint == current_fingerprint else "stale"
                    )
                else:
                    # Verification records created by older releases did not include
                    # a semantic fingerprint. Preserve their exact-revision behavior.
                    current = (node.revision, agent.revision, profile.revision)
                    verified = (record.node_revision, record.agent_revision, record.profile_revision)
                    verification = "verified" if verified == current else "stale"
        state = "ready" if configured and verification == "verified" else "configured" if configured else "needs_configuration"
        return {
            "state": state,
            "steps": {
                "node": node_step,
                "agent": agent_step,
                "model": profile_step,
                "credential": secret_state,
                "hello": verification,
            },
            "revisions": {
                "node": node.revision if node is not None else None,
                "agent": agent.revision if agent is not None else None,
                "profile": profile.revision if profile is not None else None,
            },
            "recommendedWorkspace": str(self.repository.paths.node_root / "workspaces" / "default"),
            "diagnostic": diagnostic,
            "current": {
                "node": node.document.model_dump(mode="json", by_alias=True) if node is not None else None,
                "agent": agent.document.model_dump(mode="json", by_alias=True) if agent is not None else None,
                "profile": profile.document.model_dump(mode="json", by_alias=True) if profile is not None else None,
            },
            "providers": [
                {
                    "id": item.provider_id,
                    "displayName": item.display_name,
                    "runtime": item.runtime,
                    "credentialMode": item.credential_mode,
                    "credentialRequired": item.credential_required,
                    "defaultModel": item.default_model,
                }
                for item in self.catalog.list()
                if item.runtime != "unsupported"
            ],
        }

    def workspace_readiness(self) -> dict[str, object]:
        """Return the non-sensitive setup facts required to enter a workspace."""

        status = self.status()
        raw_steps = status.get("steps")
        steps = raw_steps if isinstance(raw_steps, dict) else {}
        projected_steps = {
            key: str(steps.get(key) or "missing")
            for key in ("node", "agent", "model", "credential")
        }
        workspace_ready = (
            projected_steps["node"] == "complete"
            and projected_steps["agent"] == "complete"
            and projected_steps["model"] == "complete"
            and projected_steps["credential"] in {"available", "not_required"}
        )
        return {
            "state": status.get("state", "needs_configuration"),
            "workspaceReady": workspace_ready,
            "steps": projected_steps,
        }

    def apply(self, request: SetupApplyRequest) -> SetupApplyResult:
        """Publish a validated baseline with Node identity written last."""
        provider = self.catalog.get(request.profile.spec.provider)
        if provider is None or provider.runtime == "unsupported":
            raise SetupError("provider_not_supported", "The selected model provider is not supported.")
        model_snapshot = self.catalog.list_models(request.profile.spec.provider)
        if model_snapshot.authoritative and request.profile.spec.model not in {
            item.model_id for item in model_snapshot.models
        }:
            raise SetupError(
                "model_not_available",
                "The selected model is not advertised by this provider on the Node.",
            )
        credential = request.profile.spec.credential
        if provider.credential_required and credential is None:
            raise SetupError("credential_required", "The selected model provider requires a credential.")

        workspace = Path(request.agent.spec.workspace).expanduser()
        if not workspace.is_absolute():
            raise SetupError("workspace_not_absolute", "The Agent workspace must be an absolute path.")

        secret_state = "not_required"
        if request.secret is not None:
            secret_state = self.secrets.put(
                request.secret.ref,
                SecretValue(request.secret.value.get_secret_value()),
            ).state
        elif credential is not None:
            secret_state = self.secrets.status(credential).state
        if provider.credential_required and secret_state != "available":
            raise SetupError("credential_unavailable", "The selected model credential is not available.")

        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SetupError("workspace_unavailable", "The Agent workspace could not be created.") from exc
        if not workspace.is_dir():
            raise SetupError("workspace_unavailable", "The Agent workspace is not a directory.")

        expected = request.expected_revisions
        profile_revision = self._write_profile(request.profile, expected.profile)
        agent_revision = self._write_agent(request, expected.agent)
        node_revision, restart_required = self._write_node(request, expected.node)
        return SetupApplyResult(
            node_revision=node_revision,
            agent_revision=agent_revision,
            profile_revision=profile_revision,
            secret_state=secret_state,
            restart_required=restart_required,
        )

    def mark_verified(self, *, session_id: str) -> None:
        """Record that the current execution configuration passed a real Runtime Hello."""
        status = self.status()
        revisions = status["revisions"]
        if status["state"] not in {"configured", "ready"} or not isinstance(revisions, dict):
            raise SetupError("setup_incomplete", "Setup must be configured before the first Hello.")
        node_revision = revisions.get("node")
        agent_revision = revisions.get("agent")
        profile_revision = revisions.get("profile")
        if not all(isinstance(item, str) and item for item in (node_revision, agent_revision, profile_revision)):
            raise SetupError("setup_incomplete", "Setup revisions are incomplete.")
        self.state_repository.mark_verified(
            node_revision=node_revision,
            agent_revision=agent_revision,
            profile_revision=profile_revision,
            execution_fingerprint=self._execution_fingerprint_from_status(status),
            session_id=session_id,
        )

    @classmethod
    def _execution_fingerprint_from_status(cls, status: dict[str, object]) -> str:
        """Return a semantic setup fingerprint from one status snapshot."""
        current = status.get("current")
        if not isinstance(current, dict):
            raise SetupError("setup_incomplete", "Setup resources are incomplete.")
        documents = (current.get("node"), current.get("agent"), current.get("profile"))
        if not all(isinstance(item, dict) for item in documents):
            raise SetupError("setup_incomplete", "Setup resources are incomplete.")
        return cls._execution_fingerprint(*documents)

    @staticmethod
    def _execution_fingerprint(
        node: dict[str, object],
        agent: dict[str, object],
        profile: dict[str, object],
    ) -> str:
        """Hash setup behavior while ignoring presentation-only display names."""

        def execution_document(document: dict[str, object]) -> dict[str, object]:
            normalized = json.loads(json.dumps(document))
            spec = normalized.get("spec")
            if isinstance(spec, dict):
                spec.pop("displayName", None)
                spec.pop("display_name", None)
            return normalized

        payload = {
            "node": execution_document(node),
            "agent": execution_document(agent),
            "profile": execution_document(profile),
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _write_profile(self, profile, expected_revision: str | None) -> str:
        current = self._optional(lambda: self.profiles.read_profile(profile.metadata.name))
        candidate_revision = config_revision(profile)
        if current is not None and current.revision == candidate_revision:
            return current.revision
        resource = self.profiles.write_profile(
            profile.metadata.name,
            profile,
            expected_revision=expected_revision,
        )
        return resource.revision

    def _write_agent(self, request: SetupApplyRequest, expected_revision: str | None) -> str:
        agent = request.agent
        current = self._optional(lambda: self.repository.read_agent(agent.metadata.name))
        candidate_revision = config_revision(agent)
        if current is not None and current.revision == candidate_revision:
            return current.revision
        return self.config_service.apply_agent(
            agent.metadata.name,
            agent,
            expected_revision=expected_revision,
        ).resource.revision

    def _write_node(self, request: SetupApplyRequest, expected_revision: str | None) -> tuple[str, bool]:
        node = request.node
        current = self._optional(self.repository.read_node)
        candidate_revision = config_revision(node)
        if current is not None and current.revision == candidate_revision:
            return current.revision, False
        result = self.config_service.apply_node(node, expected_revision=expected_revision)
        return result.resource.revision, result.effect.value == "restart_required"

    @staticmethod
    def _optional(reader):
        try:
            return reader()
        except ConfigError as exc:
            if getattr(exc, "kind", None) == "not_found":
                return None
            raise

    @staticmethod
    def _inspect_resource(
        component: str,
        reader: Callable[[], ResourceT],
    ) -> tuple[ResourceT | None, SetupResourceStep, dict[str, object] | None]:
        """Read one setup resource and retain a safe non-raising diagnosis."""
        try:
            return reader(), "complete", None
        except ConfigError as exc:
            if exc.kind == "not_found":
                return None, "missing", None
            return None, "invalid", configuration_issue(exc, component=component)
