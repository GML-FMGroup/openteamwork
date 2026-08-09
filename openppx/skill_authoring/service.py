"""Conversation-driven Skill drafting, review, and confirmed publication."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from openppx.config.models import ResourceMetadata
from openppx.extensions import ExtensionError, ExtensionSourceRef, SkillManager
from openppx.runtime.session_history import project_visible_history

from .generator import AdkSkillDraftGenerator, SkillDraftGenerationError, SkillDraftGenerator
from .models import (
    MakeSkillDraftRecord,
    PublishedSkillResult,
    SkillDraftProposal,
    SkillProvenanceRecord,
    SkillProvenanceSpec,
    VisibleHistoryMessage,
)
from .store import MakeSkillDraftStore, MakeSkillStoreError


_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/-]{8,}={0,2}"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|password|secret|cookie)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\b(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b"),
)
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    flags=re.DOTALL,
)
_LOCAL_PATH_PATTERNS = (
    re.compile(r"/(?:Users|home)/[^\s]+"),
    re.compile(r"[A-Za-z]:\\Users\\[^\s]+"),
)
_UNSAFE_INSTRUCTION_PATTERNS = (
    re.compile(r"(?i)ignore (?:all |the )?(?:previous|system) instructions"),
    re.compile(r"(?i)(?:reveal|print|expose) (?:the )?system prompt"),
    re.compile(r"(?i)(?:disable|bypass) (?:security|safety|permission)"),
)
LOGGER = logging.getLogger(__name__)


class MakeSkillError(RuntimeError):
    """Stable, client-safe Skill authoring failure."""

    def __init__(self, code: str, message: str, *, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class MakeSkillService:
    """Own one explicit current-Session draft and publish only after user approval."""

    def __init__(
        self,
        *,
        node_root: Path,
        skills: SkillManager,
        supervisor: Any,
        generator: SkillDraftGenerator | None = None,
        store: MakeSkillDraftStore | None = None,
    ) -> None:
        self.node_root = node_root.expanduser().resolve(strict=False)
        self.skills = skills
        self.supervisor = supervisor
        self.generator: SkillDraftGenerator = generator or AdkSkillDraftGenerator()
        self.store = store or MakeSkillDraftStore(self.node_root)

    def create(
        self,
        *,
        agent_id: str,
        user_id: str,
        session_id: str,
        focus: str = "",
    ) -> MakeSkillDraftRecord:
        """Create one unverified draft from the current visible Session history."""
        existing = self._read_active(agent_id=agent_id, user_id=user_id, session_id=session_id)
        if existing is not None:
            raise MakeSkillError(
                "skill_draft_exists",
                "This Session already has a Skill draft. Use /make-skill approve, revise, or cancel.",
            )
        messages, redaction_count = self._capture(
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
        )
        sanitized_focus, focus_redactions = _redact_text(focus.strip())
        redaction_count += focus_redactions
        source_digest = _source_digest(messages, sanitized_focus)
        proposal = self._generate(
            agent_id=agent_id,
            messages=messages,
            focus=sanitized_focus,
            previous=None,
            revision_notes="",
        )
        _validate_proposal(proposal, messages)
        now = _now()
        draft = MakeSkillDraftRecord(
            draft_id=uuid.uuid4().hex,
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
            focus=sanitized_focus,
            source_invocation_ids=_source_ids(messages),
            source_digest=source_digest,
            source_message_count=len(messages),
            redaction_count=redaction_count,
            proposal=proposal,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        try:
            self.store.save_active(draft, previous=None)
        except MakeSkillStoreError as exc:
            raise MakeSkillError("skill_draft_write_failed", str(exc)) from exc
        return draft

    def revise(
        self,
        *,
        agent_id: str,
        user_id: str,
        session_id: str,
        revision_notes: str,
    ) -> MakeSkillDraftRecord:
        """Regenerate the current draft against the same visible evidence boundary."""
        if not revision_notes.strip():
            raise MakeSkillError("skill_draft_revision_required", "Describe what should change in the Skill draft.")
        current = self._require_active(agent_id=agent_id, user_id=user_id, session_id=session_id)
        messages = self._recapture_current_source(current)
        sanitized_notes, note_redactions = _redact_text(revision_notes.strip())
        proposal = self._generate(
            agent_id=agent_id,
            messages=messages,
            focus=current.focus,
            previous=current.proposal,
            revision_notes=sanitized_notes,
        )
        _validate_proposal(proposal, messages)
        revised = current.model_copy(
            update={
                "proposal": proposal,
                "redaction_count": current.redaction_count + note_redactions,
                "revision": current.revision + 1,
                "updated_at": _now(),
            }
        )
        try:
            self.store.save_active(revised, previous=current)
        except MakeSkillStoreError as exc:
            raise MakeSkillError("skill_draft_write_failed", str(exc)) from exc
        return revised

    def cancel(self, *, agent_id: str, user_id: str, session_id: str) -> dict[str, object]:
        """Discard only the current Session draft without changing installed Skills."""
        current = self._require_active(agent_id=agent_id, user_id=user_id, session_id=session_id)
        try:
            self.store.delete_active(current)
        except MakeSkillStoreError as exc:
            raise MakeSkillError("skill_draft_write_failed", str(exc)) from exc
        return {"cancelled": True, "draftId": current.draft_id}

    def approve(
        self,
        *,
        agent_id: str,
        user_id: str,
        session_id: str,
    ) -> PublishedSkillResult:
        """Install and enable a reviewable draft after explicit user confirmation."""
        draft = self._require_active(agent_id=agent_id, user_id=user_id, session_id=session_id)
        if draft.proposal.status != "ready_for_review":
            raise MakeSkillError(
                "skill_draft_needs_input",
                "The Skill draft still has unresolved questions and cannot be published.",
            )
        self._recapture_current_source(draft)
        self._require_identity_available(draft.proposal.skill_id)
        document = _render_skill_document(draft.proposal)
        _validate_generated_document(document)
        installed = None
        enabled = None
        try:
            installed = self._install_document(draft, document)
            enabled = self.skills.enable(
                draft.proposal.skill_id,
                agent_id,
                expected_revision=installed.revision,
            )
            provenance = SkillProvenanceRecord(
                metadata=ResourceMetadata(name=draft.proposal.skill_id),
                spec=SkillProvenanceSpec(
                    draft_id=draft.draft_id,
                    agent_id=agent_id,
                    principal_id=user_id,
                    session_id=session_id,
                    source_invocation_ids=list(draft.source_invocation_ids),
                    source_digest=draft.source_digest,
                    redaction_count=draft.redaction_count,
                    confirmation="user_confirmed",
                    skill_digest=enabled.record.spec.digest,
                    published_at=_now(),
                ),
            )
            self.store.write_provenance(provenance)
        except MakeSkillError:
            self._rollback_install(draft.proposal.skill_id, installed=installed, enabled=enabled)
            raise
        except (ExtensionError, MakeSkillStoreError) as exc:
            self._rollback_install(draft.proposal.skill_id, installed=installed, enabled=enabled)
            code = exc.code if isinstance(exc, ExtensionError) else "skill_provenance_write_failed"
            raise MakeSkillError(code, _safe_extension_message(code)) from exc
        try:
            self.store.delete_active(draft)
        except MakeSkillStoreError as exc:
            raise MakeSkillError(
                "skill_draft_cleanup_failed",
                "The Skill was created, but its pending draft could not be cleared.",
                details={"skillId": draft.proposal.skill_id},
            ) from exc
        assert enabled is not None
        return PublishedSkillResult(
            skill_id=draft.proposal.skill_id,
            display_name=draft.proposal.display_name,
            digest=enabled.record.spec.digest,
            revision=enabled.revision,
            agent_id=agent_id,
        )

    def _capture(
        self,
        *,
        agent_id: str,
        user_id: str,
        session_id: str,
    ) -> tuple[tuple[VisibleHistoryMessage, ...], int]:
        try:
            session = self.supervisor.get_session_sync(
                agent_id,
                user_id=user_id,
                session_id=session_id,
            )
        except Exception as exc:
            raise MakeSkillError("session_unavailable", "The current Session could not be read.") from exc
        if session is None:
            raise MakeSkillError("session_not_found", "The current Session was not found.")
        raw = project_visible_history(session, limit=100)
        messages: list[VisibleHistoryMessage] = []
        redaction_count = 0
        for item in raw:
            invocation_id = str(item.get("invocationId") or "").strip()
            if not invocation_id:
                continue
            text, count = _redact_text(str(item.get("text") or ""))
            redaction_count += count
            if not text.strip():
                continue
            messages.append(
                VisibleHistoryMessage(
                    role=str(item.get("role") or "assistant"),
                    text=text,
                    invocation_id=invocation_id,
                    timestamp=(str(item["timestamp"]) if item.get("timestamp") else None),
                )
            )
        roles = {message.role for message in messages}
        if not messages or "user" not in roles or "assistant" not in roles:
            raise MakeSkillError(
                "skill_source_insufficient",
                "This Session needs at least one visible user request and Agent response before creating a Skill.",
            )
        return tuple(messages), redaction_count

    def _recapture_current_source(
        self,
        draft: MakeSkillDraftRecord,
    ) -> tuple[VisibleHistoryMessage, ...]:
        messages, _redaction_count = self._capture(
            agent_id=draft.agent_id,
            user_id=draft.user_id,
            session_id=draft.session_id,
        )
        selected = tuple(
            message for message in messages if message.invocation_id in draft.source_invocation_ids
        )
        if _source_digest(selected, draft.focus) != draft.source_digest:
            raise MakeSkillError(
                "skill_source_changed",
                "The source conversation changed after the draft was created. Cancel it and run /make-skill again.",
            )
        return selected

    def _generate(
        self,
        *,
        agent_id: str,
        messages: tuple[VisibleHistoryMessage, ...],
        focus: str,
        previous: SkillDraftProposal | None,
        revision_notes: str,
    ) -> SkillDraftProposal:
        try:
            model = self.supervisor.runtime_for(agent_id).agent.model
            return self.generator.generate(
                model=model,
                messages=messages,
                focus=focus,
                previous=previous,
                revision_notes=revision_notes,
            )
        except SkillDraftGenerationError as exc:
            raise MakeSkillError("skill_draft_generation_failed", str(exc)) from exc
        except MakeSkillError:
            raise
        except Exception as exc:
            raise MakeSkillError(
                "skill_draft_generation_failed",
                "The Skill draft model turn did not complete.",
            ) from exc

    def _read_active(self, *, agent_id: str, user_id: str, session_id: str) -> MakeSkillDraftRecord | None:
        try:
            return self.store.read_active(agent_id=agent_id, user_id=user_id, session_id=session_id)
        except MakeSkillStoreError as exc:
            raise MakeSkillError("skill_draft_unavailable", str(exc)) from exc

    def _require_active(self, *, agent_id: str, user_id: str, session_id: str) -> MakeSkillDraftRecord:
        draft = self._read_active(agent_id=agent_id, user_id=user_id, session_id=session_id)
        if draft is None:
            raise MakeSkillError(
                "skill_draft_not_found",
                "This Session has no pending Skill draft. Run /make-skill first.",
            )
        return draft

    def _require_identity_available(self, skill_id: str) -> None:
        try:
            self.skills.get(skill_id)
        except ExtensionError as exc:
            if exc.code == "extension_not_found":
                return
            raise MakeSkillError(exc.code, _safe_extension_message(exc.code)) from exc
        raise MakeSkillError(
            "skill_identity_conflict",
            f"A Skill named '{skill_id}' already exists. Choose a different Skill ID.",
            details={"skillId": skill_id},
        )

    def _install_document(self, draft: MakeSkillDraftRecord, document: str):
        candidates = self.node_root / "extensions" / "generated-candidates"
        candidates.mkdir(parents=True, exist_ok=True)
        prefix = f"make-skill-{draft.draft_id[:8]}-"
        with tempfile.TemporaryDirectory(prefix=prefix, dir=candidates) as temporary:
            source = Path(temporary)
            path = source / "SKILL.md"
            path.write_text(document, encoding="utf-8")
            os.chmod(path, 0o600)
            staged = self.skills.stage(
                ExtensionSourceRef(
                    type="local_directory",
                    locator=str(source),
                    version="conversation-v1",
                )
            )
            preview = self.skills.preview(staged)
            if preview.skill_id != draft.proposal.skill_id:
                staged.extension.cleanup()
                raise MakeSkillError("invalid_manifest", "The generated Skill identity changed during validation.")
            return self.skills.install(staged, expected_revision=None)

    def _rollback_install(self, skill_id: str, *, installed: Any, enabled: Any) -> None:
        if installed is None:
            return
        try:
            current = enabled or self.skills.get(skill_id)
            if current.record.spec.enabled_agent_ids:
                for agent_id in tuple(current.record.spec.enabled_agent_ids):
                    current = self.skills.disable(
                        skill_id,
                        agent_id,
                        expected_revision=current.revision,
                    )
            self.skills.remove(skill_id, expected_revision=current.revision)
        except Exception:
            LOGGER.exception("Failed to roll back a partial generated Skill installation.")


def _redact_text(value: str) -> tuple[str, int]:
    text = value
    count = 0
    for pattern in (*_SENSITIVE_PATTERNS, _PRIVATE_KEY_PATTERN, *_LOCAL_PATH_PATTERNS):
        text, replacements = pattern.subn("[REDACTED]", text)
        count += replacements
    return text.strip(), count


def _source_ids(messages: tuple[VisibleHistoryMessage, ...]) -> list[str]:
    return list(dict.fromkeys(message.invocation_id for message in messages))


def _source_digest(messages: tuple[VisibleHistoryMessage, ...], focus: str) -> str:
    canonical = json.dumps(
        {
            "focus": focus.strip(),
            "messages": [message.model_dump(mode="json", by_alias=True) for message in messages],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _validate_proposal(
    proposal: SkillDraftProposal,
    messages: tuple[VisibleHistoryMessage, ...],
) -> None:
    allowed = set(_source_ids(messages))
    invalid = sorted(
        {
            evidence
            for step in proposal.steps
            for evidence in step.evidence_invocation_ids
            if evidence not in allowed
        }
    )
    if invalid:
        raise MakeSkillError(
            "skill_draft_ungrounded",
            "The generated Skill draft cited conversation evidence that is not visible.",
        )
    text = _proposal_text(proposal)
    if any(pattern.search(text) for pattern in _UNSAFE_INSTRUCTION_PATTERNS):
        raise MakeSkillError(
            "skill_draft_unsafe",
            "The generated Skill draft contains unsafe instruction-override language.",
        )
    redacted, count = _redact_text(text)
    if count or redacted != text.strip():
        raise MakeSkillError(
            "skill_draft_sensitive",
            "The generated Skill draft still contains sensitive values or local paths.",
        )


def _proposal_text(proposal: SkillDraftProposal) -> str:
    values = [proposal.display_name, proposal.description]
    values.extend(proposal.triggers)
    values.extend(proposal.inputs)
    values.extend(proposal.outputs)
    values.extend(step.text for step in proposal.steps)
    values.extend(proposal.limitations)
    values.extend(proposal.unresolved_questions)
    return "\n".join(values)


def _render_skill_document(proposal: SkillDraftProposal) -> str:
    frontmatter = {
        "name": proposal.skill_id,
        "description": _manifest_description(proposal),
        "metadata": {
            "openppx": {
                "version": "0.1.0",
                "risk": "medium",
                "dependencies": {"executables": [], "environment": []},
                "capabilities": [],
            }
        },
    }
    lines = [
        "---",
        yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).rstrip(),
        "---",
        "",
        f"# {proposal.display_name}",
        "",
        proposal.description,
        "",
        "## When to use",
        "",
        *[f"- {item}" for item in proposal.triggers],
    ]
    if proposal.inputs:
        lines.extend(["", "## Inputs", "", *[f"- {item}" for item in proposal.inputs]])
    if proposal.outputs:
        lines.extend(["", "## Outputs", "", *[f"- {item}" for item in proposal.outputs]])
    lines.extend(["", "## Steps", ""])
    lines.extend(f"{index}. {step.text}" for index, step in enumerate(proposal.steps, 1))
    if proposal.limitations:
        lines.extend(["", "## Boundaries", "", *[f"- {item}" for item in proposal.limitations]])
    lines.append("")
    return "\n".join(lines)


def _manifest_description(proposal: SkillDraftProposal) -> str:
    """Include bounded trigger guidance in the Runtime-visible Skill summary."""
    trigger_text = "; ".join(proposal.triggers)
    suffix = f" Use when: {trigger_text}"
    if len(suffix) >= 2_048:
        return proposal.description
    available = 2_048 - len(suffix)
    description = proposal.description[:available].rstrip()
    return f"{description}{suffix}"


def _validate_generated_document(document: str) -> None:
    if len(document.encode("utf-8")) > 128 * 1024:
        raise MakeSkillError("skill_draft_too_large", "The generated Skill is too large to publish.")
    if any(pattern.search(document) for pattern in (*_SENSITIVE_PATTERNS, _PRIVATE_KEY_PATTERN, *_LOCAL_PATH_PATTERNS)):
        raise MakeSkillError("skill_draft_sensitive", "The generated Skill contains sensitive values or local paths.")


def _safe_extension_message(code: str) -> str:
    messages = {
        "extension_conflict": "A Skill with this identity already exists.",
        "invalid_manifest": "The generated Skill did not pass manifest validation.",
        "revision_conflict": "The Skill registry changed; retry the command.",
        "dependency_missing": "The generated Skill has unavailable dependencies.",
        "write_failed": "The Skill could not be written to the Node registry.",
    }
    return messages.get(code, "The Skill could not be published.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["MakeSkillError", "MakeSkillService"]
