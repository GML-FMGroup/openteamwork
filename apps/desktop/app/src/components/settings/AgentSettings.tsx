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
  maxPrivilegeLevel: "low" | "medium" | "high" | "root";
}

type AgentOperation = "refresh" | "save" | "toggle" | "duplicate" | "remove";
type AgentCreateDraft = Omit<AgentCreateRequest, "agentId">;

function agentSlug(value: string): string {
  return value
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/g, "");
}

/** Build a stable hidden Agent identity without colliding with Node-owned Agents. */
function uniqueAgentId(
  displayName: string,
  suggestedAgentId: string,
  agents: AgentResourceSummary[],
): string {
  const existingIds = new Set(agents.map((agent) => agent.id));
  const base = agentSlug(displayName) || agentSlug(suggestedAgentId) || "agent";
  if (!existingIds.has(base)) return base;
  for (let suffix = 2; ; suffix += 1) {
    const suffixText = `-${suffix}`;
    const candidate = `${base.slice(0, 63 - suffixText.length).replace(/-+$/g, "")}${suffixText}`;
    if (!existingIds.has(candidate)) return candidate;
  }
}

function initialCreateDraft(
  modelProfiles: ModelProfileSummary[],
  maxPrivilegeLevel: AgentSettingsProps["maxPrivilegeLevel"],
): AgentCreateDraft {
  return {
    displayName: "",
    workspace: null,
    privilegeLevel: maxPrivilegeLevel === "low" ? "low" : "medium",
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
  maxPrivilegeLevel,
}: AgentSettingsProps) {
  const [agents, setAgents] = useState<AgentResourceSummary[]>([]);
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const [activeId, setActiveId] = useState(selectedAgentId);
  const [draft, setDraft] = useState<AgentUpdateInput | null>(null);
  const [creatingNew, setCreatingNew] = useState(false);
  const [createDraft, setCreateDraft] = useState<AgentCreateDraft>(() => initialCreateDraft(modelProfiles, maxPrivilegeLevel));
  const [operation, setOperation] = useState<AgentOperation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const createNameRef = useRef<HTMLInputElement | null>(null);
  const activeAgentButtonRef = useRef<HTMLButtonElement | null>(null);
  const restoreAgentFocusRef = useRef(false);
  const current = useMemo(() => agents.find((agent) => agent.id === activeId) ?? agents[0] ?? null, [activeId, agents]);
  const busy = operation !== null;
  const enabledProfiles = useMemo(() => modelProfiles.filter((profile) => profile.enabled), [modelProfiles]);
  const privilegeLevels = (["low", "medium", "high", "root"] as const).slice(
    0,
    (["low", "medium", "high", "root"] as const).indexOf(maxPrivilegeLevel) + 1,
  );
  const canCreate = Boolean(
    createDraft.displayName.trim()
    && createDraft.modelProfileId
    && agentsLoaded
    && !creatingAgent,
  );
  const dirty = useMemo(() => Boolean(current && draft && (
    draft.displayName !== current.name ||
    draft.instruction !== current.instruction ||
    draft.modelProfileId !== current.modelProfileId
  )), [current, draft]);

  async function refresh(preferredId = activeId || selectedAgentId): Promise<void> {
    const result = await window.ppxClient.listManagedAgents();
    setAgents(result.agents);
    setAgentsLoaded(true);
    const next = result.agents.find((agent) => agent.id === preferredId) ?? result.agents[0] ?? null;
    setActiveId(next?.id ?? "");
  }

  useEffect(() => {
    void refresh().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  useEffect(() => {
    if (!createRequested) return;
    setCreateDraft(initialCreateDraft(modelProfiles, maxPrivilegeLevel));
    setCreatingNew(true);
    setError(null);
    setNotice(null);
    onClearCreateError();
    onCreateRequestHandled();
  }, [createRequested, maxPrivilegeLevel]);

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
      instruction: current.instruction,
      modelProfileId: current.modelProfileId,
      expectedRevision: current.revision,
    });
  }, [current]);

  function patchDraft(patch: Partial<AgentUpdateInput>): void {
    setNotice(null);
    setDraft((currentDraft) => currentDraft ? { ...currentDraft, ...patch } : currentDraft);
  }

  function beginCreate(): void {
    setCreateDraft(initialCreateDraft(modelProfiles, maxPrivilegeLevel));
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

  function patchCreateDraft(patch: Partial<AgentCreateDraft>): void {
    onClearCreateError();
    setCreateDraft((currentDraft) => ({ ...currentDraft, ...patch }));
  }

  async function handleCreate(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canCreate) return;
    const input: AgentCreateRequest = {
      agentId: uniqueAgentId(createDraft.displayName, suggestedAgentId, agents),
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
              <small>Managed workspace paths stay fixed after creation.</small>
            </div>
            <div className="settings-form settings-form-grid">
              <label className="settings-field">
                <span>Agent name</span>
                <input
                  ref={createNameRef}
                  value={createDraft.displayName}
                  maxLength={80}
                  placeholder="Research"
                  onChange={(event) => patchCreateDraft({ displayName: event.target.value })}
                />
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
                  {privilegeLevels.map((level) => <option key={level} value={level}>{level[0].toUpperCase() + level.slice(1)}</option>)}
                </select>
              </label>
              <label className="settings-field agent-workspace-field">
                <span>Workspace</span>
                {maxPrivilegeLevel === "root" ? <input
                  aria-label="Workspace"
                  value={createDraft.workspace ?? ""}
                  maxLength={1_024}
                  spellCheck={false}
                  placeholder="Leave blank for a Node-managed workspace"
                  onChange={(event) => patchCreateDraft({ workspace: event.target.value })}
                /> : <p>Node managed automatically</p>}
                <small>{maxPrivilegeLevel === "root" ? "Optional. A custom location must be an absolute path on the Agent machine." : "Your Agent workspace is allocated automatically."}</small>
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
              <div className={`agent-policy-save-state ${enabledProfiles.length === 0 ? "unsaved" : "clean"}`} role="status" aria-live="polite">
                <span className="agent-policy-save-dot" aria-hidden="true" />
                <span>{enabledProfiles.length === 0 ? "Model Profile required" : "Ready to create"}</span>
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
              <label className="settings-field"><span>Privilege</span><p>{current.privilegeLevel[0].toUpperCase() + current.privilegeLevel.slice(1)}</p><small>Fixed when this Agent was created.</small></label>
              <label className="settings-field agent-workspace-field"><span>Workspace</span><p>{current.workspace}</p><small>Fixed when this Agent was created.</small></label>
              <label className="settings-field agent-instruction-field"><span>Agent instruction</span><textarea value={draft.instruction} maxLength={16_384} placeholder="Optional role, output, or operating guidance for this Agent." onChange={(event) => patchDraft({ instruction: event.target.value })} /></label>
            </div>
            {error ? <p className="settings-inline-error" role="alert">{error}</p> : null}
            <footer className="agent-policy-actions">
              <div className={`agent-policy-save-state ${dirty ? "unsaved" : notice ? "saved" : "clean"}`} role="status" aria-live="polite">
                <span className="agent-policy-save-dot" aria-hidden="true" />
                <span>{dirty ? "Unsaved changes" : notice ?? "No unsaved changes"}</span>
              </div>
              <div className="agent-policy-action-buttons">
                <button type="submit" className="agent-policy-save-button" disabled={busy || !dirty || !draft.displayName.trim()}>{operation === "save" ? "Saving…" : "Save changes"}</button>
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
