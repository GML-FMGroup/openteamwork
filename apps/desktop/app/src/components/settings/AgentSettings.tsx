import { useEffect, useMemo, useState } from "react";
import type { AgentResourceSummary, AgentUpdateInput, ModelProfileSummary } from "../../types";

interface AgentSettingsProps {
  selectedAgentId: string;
  modelProfiles: ModelProfileSummary[];
  onWorkspaceChanged: () => Promise<void>;
}

/** Full Node-owned Agent lifecycle editor with revision-safe writes. */
export function AgentSettings({ selectedAgentId, modelProfiles, onWorkspaceChanged }: AgentSettingsProps) {
  const [agents, setAgents] = useState<AgentResourceSummary[]>([]);
  const [activeId, setActiveId] = useState(selectedAgentId);
  const [draft, setDraft] = useState<AgentUpdateInput | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current = useMemo(() => agents.find((agent) => agent.id === activeId) ?? agents[0] ?? null, [activeId, agents]);

  async function refresh(preferredId = activeId || selectedAgentId): Promise<void> {
    const result = await window.ppxClient.listManagedAgents();
    setAgents(result.agents);
    const next = result.agents.find((agent) => agent.id === preferredId) ?? result.agents[0] ?? null;
    setActiveId(next?.id ?? "");
  }

  useEffect(() => {
    void refresh().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  useEffect(() => {
    if (!current) {
      setDraft(null);
      return;
    }
    setDraft({
      agentId: current.id,
      displayName: current.name,
      workspace: current.workspace,
      instruction: current.instruction,
      privilegeLevel: current.privilegeLevel,
      modelProfileId: current.modelProfileId,
      expectedRevision: current.revision,
    });
  }, [current]);

  async function execute(operation: () => Promise<unknown>, preferredId = activeId): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await operation();
      await refresh(preferredId);
      await onWorkspaceChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings-card settings-page-card agent-lifecycle-card">
      <div className="settings-card-heading">
        <div><h3>Agents</h3><p>Workspaces, permissions, instructions, and model policy owned by this Node.</p></div>
        <button className="secondary settings-quiet-button" onClick={() => void refresh()} disabled={busy}>Refresh</button>
      </div>
      <div className="agent-lifecycle-layout">
        <nav className="agent-resource-list" aria-label="Configured Agents">
          {agents.map((agent) => (
            <button key={agent.id} className={agent.id === current?.id ? "active" : ""} onClick={() => setActiveId(agent.id)}>
              <span><strong>{agent.name}</strong><small>{agent.workspace}</small></span>
              <em className={agent.enabled ? "resource-state ready" : "resource-state muted"}>{agent.enabled ? "enabled" : "disabled"}</em>
            </button>
          ))}
        </nav>
        {current && draft ? (
          <form className="agent-policy-form" onSubmit={(event) => { event.preventDefault(); void execute(() => window.ppxClient.updateAgent(draft)); }}>
            <div className="agent-policy-heading"><div><span>Agent ID</span><strong>{current.id}</strong></div><small>Changes apply to new Runs. Active Runs keep their current snapshot.</small></div>
            <div className="settings-form settings-form-grid">
              <label className="settings-field"><span>Name</span><input value={draft.displayName} maxLength={80} onChange={(event) => setDraft({ ...draft, displayName: event.target.value })} /></label>
              <label className="settings-field"><span>Model Profile</span><select value={draft.modelProfileId} onChange={(event) => setDraft({ ...draft, modelProfileId: event.target.value })}>{modelProfiles.filter((profile) => profile.enabled).map((profile) => <option key={profile.id} value={profile.id}>{profile.displayName} · {profile.model}</option>)}</select></label>
              <label className="settings-field"><span>Privilege</span><select value={draft.privilegeLevel} onChange={(event) => setDraft({ ...draft, privilegeLevel: event.target.value as AgentUpdateInput["privilegeLevel"] })}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="root">Root</option></select></label>
              <label className="settings-field agent-workspace-field"><span>Workspace</span><input value={draft.workspace} spellCheck={false} onChange={(event) => setDraft({ ...draft, workspace: event.target.value })} /></label>
              <label className="settings-field agent-instruction-field"><span>Agent instruction</span><textarea value={draft.instruction} maxLength={16_384} placeholder="Optional role, output, or operating guidance for this Agent." onChange={(event) => setDraft({ ...draft, instruction: event.target.value })} /></label>
            </div>
            {error ? <p className="settings-inline-error" role="alert">{error}</p> : null}
            <footer className="agent-policy-actions">
              <button type="submit" disabled={busy || !draft.displayName.trim() || !draft.workspace.trim()}>Save changes</button>
              <button type="button" className="secondary" disabled={busy} onClick={() => void execute(() => window.ppxClient.setAgentEnabled(current.id, !current.enabled))}>{current.enabled ? "Disable" : "Enable"}</button>
              <button type="button" className="secondary" disabled={busy} onClick={() => void execute(async () => {
                const duplicateId = `${current.id}-copy`.slice(0, 63).replace(/-+$/g, "");
                await window.ppxClient.createAgent({ agentId: duplicateId, displayName: `${current.name} Copy`, workspace: null, privilegeLevel: current.privilegeLevel, modelProfileId: current.modelProfileId, instruction: current.instruction });
              }, `${current.id}-copy`)}>Duplicate</button>
              <button type="button" className="danger-quiet" disabled={busy || current.enabled} title={current.enabled ? "Disable the Agent before removing it." : "Workspace and runtime data will be retained."} onClick={() => {
                if (window.confirm(`Remove ${current.name}? Its workspace and runtime data will be retained.`)) void execute(() => window.ppxClient.removeAgent(current.id, current.revision));
              }}>Remove</button>
            </footer>
          </form>
        ) : <p className="extension-empty">No configured Agents.</p>}
      </div>
    </section>
  );
}
