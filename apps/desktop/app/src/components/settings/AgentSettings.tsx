import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentCreateRequest, AgentResourceSummary, AgentUpdateInput, ModelProfileSummary } from "../../types";

interface AgentSettingsProps {
  selectedAgentId: string;
  modelProfiles: ModelProfileSummary[];
  createRequested: boolean;
  suggestedAgentId: string;
  creatingAgent: boolean;
  createError: string | null;
  onCreateRequestHandled: () => void;
  onClearCreateError: () => void;
  onCreateAgent: (input: AgentCreateRequest) => Promise<boolean>;
  onWorkspaceChanged: () => Promise<void>;
}

type AgentOperation = "refresh" | "save" | "toggle" | "duplicate" | "remove";

function agentSlug(value: string): string {
  return value
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/g, "");
}

function initialCreateDraft(
  suggestedAgentId: string,
  modelProfiles: ModelProfileSummary[],
): AgentCreateRequest {
  return {
    agentId: suggestedAgentId,
    displayName: "",
    workspace: null,
    privilegeLevel: "medium",
    modelProfileId: modelProfiles.find((profile) => profile.enabled)?.id ?? "",
    instruction: "",
  };
}

/** Full Node-owned Agent lifecycle editor with revision-safe writes. */
export function AgentSettings({
  selectedAgentId,
  modelProfiles,
  createRequested,
  suggestedAgentId,
  creatingAgent,
  createError,
  onCreateRequestHandled,
  onClearCreateError,
  onCreateAgent,
  onWorkspaceChanged,
}: AgentSettingsProps) {
  const [agents, setAgents] = useState<AgentResourceSummary[]>([]);
  const [activeId, setActiveId] = useState(selectedAgentId);
  const [draft, setDraft] = useState<AgentUpdateInput | null>(null);
  const [creatingNew, setCreatingNew] = useState(false);
  const [createDraft, setCreateDraft] = useState<AgentCreateRequest>(() => initialCreateDraft(suggestedAgentId, modelProfiles));
  const [createIdEdited, setCreateIdEdited] = useState(false);
  const [operation, setOperation] = useState<AgentOperation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const createNameRef = useRef<HTMLInputElement | null>(null);
  const activeAgentButtonRef = useRef<HTMLButtonElement | null>(null);
  const restoreAgentFocusRef = useRef(false);
  const current = useMemo(() => agents.find((agent) => agent.id === activeId) ?? agents[0] ?? null, [activeId, agents]);
  const busy = operation !== null;
  const enabledProfiles = useMemo(() => modelProfiles.filter((profile) => profile.enabled), [modelProfiles]);
  const createIdExists = agents.some((agent) => agent.id === createDraft.agentId);
  const canCreate = Boolean(
    createDraft.displayName.trim()
    && createDraft.agentId
    && createDraft.modelProfileId
    && !createIdExists
    && !creatingAgent,
  );
  const dirty = useMemo(() => Boolean(current && draft && (
    draft.displayName !== current.name ||
    draft.workspace !== current.workspace ||
    draft.instruction !== current.instruction ||
    draft.privilegeLevel !== current.privilegeLevel ||
    draft.modelProfileId !== current.modelProfileId
  )), [current, draft]);

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
    if (!createRequested) return;
    setCreateDraft(initialCreateDraft(suggestedAgentId, modelProfiles));
    setCreateIdEdited(false);
    setCreatingNew(true);
    setError(null);
    setNotice(null);
    onClearCreateError();
    onCreateRequestHandled();
  }, [createRequested]);

  useEffect(() => {
    if (creatingNew) createNameRef.current?.focus();
  }, [creatingNew]);

  useEffect(() => {
    if (!creatingNew && restoreAgentFocusRef.current) {
      restoreAgentFocusRef.current = false;
      activeAgentButtonRef.current?.focus();
    }
  }, [creatingNew]);

  useEffect(() => {
    if (!createDraft.modelProfileId && enabledProfiles[0]) {
      setCreateDraft((currentDraft) => ({ ...currentDraft, modelProfileId: enabledProfiles[0].id }));
    }
  }, [createDraft.modelProfileId, enabledProfiles]);

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

  function patchDraft(patch: Partial<AgentUpdateInput>): void {
    setNotice(null);
    setDraft((currentDraft) => currentDraft ? { ...currentDraft, ...patch } : currentDraft);
  }

  function beginCreate(): void {
    setCreateDraft(initialCreateDraft(suggestedAgentId, modelProfiles));
    setCreateIdEdited(false);
    setCreatingNew(true);
    setError(null);
    setNotice(null);
    onClearCreateError();
  }

  function cancelCreate(): void {
    restoreAgentFocusRef.current = true;
    setCreatingNew(false);
    onClearCreateError();
  }

  function patchCreateDraft(patch: Partial<AgentCreateRequest>): void {
    onClearCreateError();
    setCreateDraft((currentDraft) => ({ ...currentDraft, ...patch }));
  }

  async function handleCreate(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canCreate) return;
    const input: AgentCreateRequest = {
      agentId: createDraft.agentId,
      displayName: createDraft.displayName.trim(),
      workspace: createDraft.workspace?.trim() || null,
      privilegeLevel: createDraft.privilegeLevel,
      modelProfileId: createDraft.modelProfileId,
      ...(createDraft.instruction?.trim() ? { instruction: createDraft.instruction.trim() } : {}),
    };
    const created = await onCreateAgent(input);
    if (!created) return;
    setCreatingNew(false);
    setActiveId(input.agentId);
    setNotice(`${input.displayName} created`);
    try {
      await refresh(input.agentId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function handleRefresh(): Promise<void> {
    setOperation("refresh");
    setError(null);
    setNotice(null);
    try {
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setOperation(null);
    }
  }

  async function execute(
    kind: AgentOperation,
    action: () => Promise<unknown>,
    preferredId = activeId,
    successMessage?: string,
  ): Promise<void> {
    setOperation(kind);
    setError(null);
    setNotice(null);
    try {
      await action();
      await refresh(preferredId);
      await onWorkspaceChanged();
      setNotice(successMessage ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setOperation(null);
    }
  }

  return (
    <section className="settings-card settings-page-card agent-lifecycle-card">
      <div className="settings-card-heading">
        <div><h3>Agents</h3><p>Workspaces, permissions, instructions, and model policy owned by this Node.</p></div>
        <div className="settings-heading-actions">
          <button onClick={beginCreate} disabled={busy || creatingAgent}>New Agent</button>
          <button className="secondary settings-quiet-button" onClick={() => void handleRefresh()} disabled={busy || creatingAgent}>{operation === "refresh" ? "Refreshing…" : "Refresh"}</button>
        </div>
      </div>
      <div className="agent-lifecycle-layout">
        <nav className="agent-resource-list" aria-label="Agents">
          {creatingNew ? (
            <button type="button" className="active agent-resource-create" aria-current="page">
              <span><strong>New Agent</strong><small>Configure a focused workspace</small></span>
              <em>new</em>
            </button>
          ) : null}
          {agents.map((agent) => (
            <button
              ref={(element) => { if (agent.id === current?.id) activeAgentButtonRef.current = element; }}
              type="button"
              key={agent.id}
              className={!creatingNew && agent.id === current?.id ? "active" : ""}
              onClick={() => { setCreatingNew(false); setActiveId(agent.id); setError(null); setNotice(null); onClearCreateError(); }}
            >
              <span><strong>{agent.name}</strong><small>{agent.workspace}</small></span>
              <em className={agent.enabled ? "resource-state ready" : "resource-state muted"}>{agent.enabled ? "enabled" : "disabled"}</em>
            </button>
          ))}
        </nav>
        {creatingNew ? (
          <form className="agent-policy-form agent-create-form" onSubmit={(event) => void handleCreate(event)}>
            <div className="agent-policy-heading">
              <div><span>New Agent</span><h4>Create a focused workspace.</h4></div>
              <small>Agent IDs and managed workspace paths stay fixed after creation.</small>
            </div>
            <div className="settings-form settings-form-grid">
              <label className="settings-field">
                <span>Agent name</span>
                <input
                  ref={createNameRef}
                  value={createDraft.displayName}
                  maxLength={80}
                  placeholder="Research"
                  onChange={(event) => {
                    const displayName = event.target.value;
                    patchCreateDraft({
                      displayName,
                      ...(!createIdEdited ? { agentId: agentSlug(displayName) || suggestedAgentId } : {}),
                    });
                  }}
                />
              </label>
              <label className="settings-field">
                <span>Agent ID</span>
                <input
                  aria-label="Agent ID"
                  value={createDraft.agentId}
                  maxLength={63}
                  spellCheck={false}
                  onChange={(event) => {
                    setCreateIdEdited(true);
                    patchCreateDraft({ agentId: agentSlug(event.target.value) });
                  }}
                />
                <small>{createIdExists ? "This Agent ID already exists." : "Lowercase identifier; fixed after creation."}</small>
              </label>
              <label className="settings-field">
                <span>Model Profile</span>
                <select value={createDraft.modelProfileId} onChange={(event) => patchCreateDraft({ modelProfileId: event.target.value })}>
                  {enabledProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.displayName} · {profile.model}</option>)}
                </select>
              </label>
              <label className="settings-field">
                <span>Privilege</span>
                <select value={createDraft.privilegeLevel} onChange={(event) => patchCreateDraft({ privilegeLevel: event.target.value as AgentCreateRequest["privilegeLevel"] })}>
                  <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="root">Root</option>
                </select>
              </label>
              <label className="settings-field agent-workspace-field">
                <span>Workspace</span>
                <input
                  aria-label="Workspace"
                  value={createDraft.workspace ?? ""}
                  maxLength={1_024}
                  spellCheck={false}
                  placeholder={`Node managed · workspaces/${createDraft.agentId || suggestedAgentId}`}
                  onChange={(event) => patchCreateDraft({ workspace: event.target.value })}
                />
                <small>Optional. A custom location must be an absolute path on the Agent machine.</small>
              </label>
              <label className="settings-field agent-instruction-field">
                <span>Agent instruction</span>
                <textarea
                  value={createDraft.instruction ?? ""}
                  maxLength={16_384}
                  placeholder="Optional role, output, or operating guidance for this Agent."
                  onChange={(event) => patchCreateDraft({ instruction: event.target.value })}
                />
              </label>
            </div>
            {enabledProfiles.length === 0 ? <p className="settings-inline-error">Create or enable a Model Profile first.</p> : null}
            {createError ? <p className="settings-inline-error" role="alert">{createError}</p> : null}
            <footer className="agent-policy-actions">
              <div className={`agent-policy-save-state ${createIdExists || enabledProfiles.length === 0 ? "unsaved" : "clean"}`} role="status" aria-live="polite">
                <span className="agent-policy-save-dot" aria-hidden="true" />
                <span>{createIdExists ? "Choose another Agent ID" : enabledProfiles.length === 0 ? "Model Profile required" : "Ready to create"}</span>
              </div>
              <div className="agent-policy-action-buttons">
                <button type="button" className="secondary" onClick={cancelCreate} disabled={creatingAgent}>Cancel</button>
                <button type="submit" className="agent-policy-save-button" disabled={!canCreate}>{creatingAgent ? "Creating…" : "Create Agent"}</button>
              </div>
            </footer>
          </form>
        ) : current && draft ? (
          <form className="agent-policy-form" onSubmit={(event) => { event.preventDefault(); void execute("save", () => window.ppxClient.updateAgent(draft), activeId, "Changes saved"); }}>
            <div className="agent-policy-heading"><div><span>Agent ID</span><strong>{current.id}</strong></div><small>Changes apply to new Runs. Active Runs keep their current snapshot.</small></div>
            <div className="settings-form settings-form-grid">
              <label className="settings-field"><span>Name</span><input value={draft.displayName} maxLength={80} onChange={(event) => patchDraft({ displayName: event.target.value })} /></label>
              <label className="settings-field"><span>Model Profile</span><select value={draft.modelProfileId} onChange={(event) => patchDraft({ modelProfileId: event.target.value })}>{modelProfiles.filter((profile) => profile.enabled).map((profile) => <option key={profile.id} value={profile.id}>{profile.displayName} · {profile.model}</option>)}</select></label>
              <label className="settings-field"><span>Privilege</span><select value={draft.privilegeLevel} onChange={(event) => patchDraft({ privilegeLevel: event.target.value as AgentUpdateInput["privilegeLevel"] })}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="root">Root</option></select></label>
              <label className="settings-field agent-workspace-field"><span>Workspace</span><input value={draft.workspace} spellCheck={false} onChange={(event) => patchDraft({ workspace: event.target.value })} /></label>
              <label className="settings-field agent-instruction-field"><span>Agent instruction</span><textarea value={draft.instruction} maxLength={16_384} placeholder="Optional role, output, or operating guidance for this Agent." onChange={(event) => patchDraft({ instruction: event.target.value })} /></label>
            </div>
            {error ? <p className="settings-inline-error" role="alert">{error}</p> : null}
            <footer className="agent-policy-actions">
              <div className={`agent-policy-save-state ${dirty ? "unsaved" : notice ? "saved" : "clean"}`} role="status" aria-live="polite">
                <span className="agent-policy-save-dot" aria-hidden="true" />
                <span>{dirty ? "Unsaved changes" : notice ?? "No unsaved changes"}</span>
              </div>
              <div className="agent-policy-action-buttons">
                <button type="submit" className="agent-policy-save-button" disabled={busy || !dirty || !draft.displayName.trim() || !draft.workspace.trim()}>{operation === "save" ? "Saving…" : "Save changes"}</button>
                <button type="button" className="secondary" disabled={busy} onClick={() => void execute("toggle", () => window.ppxClient.setAgentEnabled(current.id, !current.enabled), activeId, current.enabled ? "Agent disabled" : "Agent enabled")}>{operation === "toggle" ? "Updating…" : current.enabled ? "Disable" : "Enable"}</button>
                <button type="button" className="secondary" disabled={busy} onClick={() => void execute("duplicate", async () => {
                const duplicateId = `${current.id}-copy`.slice(0, 63).replace(/-+$/g, "");
                await window.ppxClient.createAgent({ agentId: duplicateId, displayName: `${current.name} Copy`, workspace: null, privilegeLevel: current.privilegeLevel, modelProfileId: current.modelProfileId, instruction: current.instruction });
                }, `${current.id}-copy`, "Agent duplicated")}>{operation === "duplicate" ? "Duplicating…" : "Duplicate"}</button>
                <button type="button" className="danger-quiet" disabled={busy || current.enabled} title={current.enabled ? "Disable the Agent before removing it." : "Workspace and runtime data will be retained."} onClick={() => {
                  if (window.confirm(`Remove ${current.name}? Its workspace and runtime data will be retained.`)) void execute("remove", () => window.ppxClient.removeAgent(current.id, current.revision), "");
                }}>{operation === "remove" ? "Removing…" : "Remove"}</button>
              </div>
            </footer>
          </form>
        ) : <p className="extension-empty">No configured Agents.</p>}
      </div>
    </section>
  );
}
