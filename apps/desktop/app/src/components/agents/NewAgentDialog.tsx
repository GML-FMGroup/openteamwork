import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentCreateRequest, ModelProfileSummary } from "../../types";

interface NewAgentDialogProps {
  suggestedAgentId: string;
  modelProfiles: ModelProfileSummary[];
  creating: boolean;
  error: string | null;
  onCancel: () => void;
  onCreate: (input: AgentCreateRequest) => void;
}

function agentSlug(value: string): string {
  return value
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/g, "");
}

/** Focused create-only Agent dialog backed by the Node lifecycle Action. */
export function NewAgentDialog({
  suggestedAgentId,
  modelProfiles,
  creating,
  error,
  onCancel,
  onCreate,
}: NewAgentDialogProps) {
  const enabledProfiles = useMemo(() => modelProfiles.filter((profile) => profile.enabled), [modelProfiles]);
  const [displayName, setDisplayName] = useState("");
  const [agentId, setAgentId] = useState(suggestedAgentId);
  const [idEdited, setIdEdited] = useState(false);
  const [workspace, setWorkspace] = useState("");
  const [privilegeLevel, setPrivilegeLevel] = useState<AgentCreateRequest["privilegeLevel"]>("medium");
  const [modelProfileId, setModelProfileId] = useState(enabledProfiles[0]?.id ?? "");
  const nameRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!modelProfileId && enabledProfiles[0]) {
      setModelProfileId(enabledProfiles[0].id);
    }
  }, [enabledProfiles, modelProfileId]);

  useEffect(() => {
    function handleEscape(event: KeyboardEvent): void {
      if (event.key === "Escape" && !creating) {
        event.preventDefault();
        onCancel();
      }
    }
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [creating, onCancel]);

  const canCreate = Boolean(displayName.trim() && agentId && modelProfileId) && !creating;

  function updateDisplayName(value: string): void {
    setDisplayName(value);
    if (!idEdited) {
      setAgentId(agentSlug(value) || suggestedAgentId);
    }
  }

  function submit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (!canCreate) return;
    onCreate({
      agentId,
      displayName: displayName.trim(),
      workspace: workspace.trim() || null,
      privilegeLevel,
      modelProfileId,
    });
  }

  return (
    <div
      className="agent-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !creating) onCancel();
      }}
    >
      <section className="agent-dialog" role="dialog" aria-modal="true" aria-labelledby="new-agent-title">
        <header className="agent-dialog-header">
          <div>
            <span className="agent-dialog-eyebrow">New Agent</span>
            <h2 id="new-agent-title">Create a focused workspace.</h2>
            <p>Each Agent keeps its own workspace and model policy while sharing this Node.</p>
          </div>
        </header>

        <form onSubmit={submit}>
          <div className="agent-dialog-fields">
            <label>
              <span>Agent name</span>
              <input
                ref={nameRef}
                value={displayName}
                onChange={(event) => updateDisplayName(event.target.value)}
                placeholder="Research"
                maxLength={80}
              />
            </label>
            <label>
              <span>Agent ID</span>
              <input
                value={agentId}
                aria-label="Agent ID"
                onChange={(event) => {
                  setIdEdited(true);
                  setAgentId(agentSlug(event.target.value));
                }}
                placeholder="research"
                maxLength={63}
                spellCheck={false}
              />
              <small>Lowercase identifier used by the Node.</small>
            </label>
            <label className="agent-dialog-full-row">
              <span>Workspace</span>
              <input
                value={workspace}
                aria-label="Workspace"
                onChange={(event) => setWorkspace(event.target.value)}
                placeholder={`Node managed · workspaces/${agentId || suggestedAgentId}`}
                maxLength={1_024}
                spellCheck={false}
              />
              <small>Optional. A custom location must be an absolute path on the Agent machine.</small>
            </label>
            <label>
              <span>Model Profile</span>
              <select value={modelProfileId} onChange={(event) => setModelProfileId(event.target.value)}>
                {enabledProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>{profile.id} · {profile.model}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Privilege</span>
              <select
                value={privilegeLevel}
                onChange={(event) => setPrivilegeLevel(event.target.value as AgentCreateRequest["privilegeLevel"])}
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="root">Root</option>
              </select>
            </label>
          </div>

          {enabledProfiles.length === 0 ? (
            <p className="agent-dialog-error">Create or enable a Model Profile in Settings first.</p>
          ) : error ? (
            <p className="agent-dialog-error" role="alert">{error}</p>
          ) : (
            <p className="agent-dialog-note">The Agent becomes available immediately. No Node restart is required.</p>
          )}

          <footer className="agent-dialog-actions">
            <button type="button" className="agent-dialog-cancel" onClick={onCancel} disabled={creating}>Cancel</button>
            <button type="submit" className="agent-dialog-create" disabled={!canCreate}>
              {creating ? "Creating…" : "Create Agent"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}
