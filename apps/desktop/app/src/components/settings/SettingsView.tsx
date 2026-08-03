import { useState, type Dispatch, type SetStateAction } from "react";
import type {
  AgentProfile,
  ClientDiagnostics,
  ConnectionSettings,
  ExtensionSummary,
  ModelProfileSummary,
  OperationsAuditItem,
  OperationsOverviewResult,
  RuntimeState,
  RuntimeStatus,
} from "../../types";
import { CollapsedSidebarTools } from "../workspace/ContextSidebar";

type SettingsSection = "general" | "models" | "extensions" | "operations" | "agent";

interface SettingsViewProps {
  runtime: RuntimeStatus;
  diagnostics: ClientDiagnostics | null;
  connectionForm: ConnectionSettings;
  savingConnection: boolean;
  testingConnection: boolean;
  connectionFeedback: string | null;
  extensions: ExtensionSummary[];
  modelProfiles: ModelProfileSummary[];
  agents: AgentProfile[];
  extensionsLoading: boolean;
  extensionsError: string | null;
  extensionMutationId: string | null;
  operationsOverview: OperationsOverviewResult | null;
  operationsAudit: OperationsAuditItem[];
  operationsLoading: boolean;
  operationsError: string | null;
  selectedAgentId: string;
  sidebarCollapsed: boolean;
  canCreateSession: boolean;
  setConnectionForm: Dispatch<SetStateAction<ConnectionSettings>>;
  onRevealSidebar: () => void;
  onNewSession: () => void;
  onSearchSessions: () => void;
  onReturnToChat: () => void;
  onRuntimeAction: () => void;
  onStopRuntime: () => void;
  onRefreshOperations: () => void;
  onTestConnection: () => void;
  onSaveConnection: () => void;
  onRefreshExtensions: () => void;
  onRefreshModels: () => void;
  onSetExtensionEnabled: (extension: ExtensionSummary, enabled: boolean) => void;
}

function runtimeActionLabel(state: RuntimeState): string {
  if (state === "stopped") return "Start";
  if (state === "healthy") return "Restart";
  return "Retry";
}

function sectionTitle(section: string): string {
  return section[0].toUpperCase() + section.slice(1);
}

function formatOperationTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** Stable five-section Desktop control interface backed only by Node contracts. */
export function SettingsView(props: SettingsViewProps) {
  const [section, setSection] = useState<SettingsSection>("general");
  const selectedAgent = props.agents.find((item) => item.id === props.selectedAgentId) ?? null;

  return (
    <section className="workspace-shell settings-shell">
      <header className="column-topbar workspace-topbar">
        <div className="topbar-copy">
          {props.sidebarCollapsed ? (
            <CollapsedSidebarTools
              canCreateSession={props.canCreateSession}
              onRevealSidebar={props.onRevealSidebar}
              onNewSession={props.onNewSession}
              onSearchSessions={props.onSearchSessions}
            />
          ) : null}
          <strong>Settings</strong><span className="topbar-location">{sectionTitle(section)}</span>
        </div>
        <button className="topbar-pill" onClick={props.onReturnToChat} title="Return to conversation">
          <span className={`runtime-dot ${props.runtime.state}`} />{props.runtime.state}
        </button>
      </header>
      <div className="workspace-frame settings-frame settings-control-frame">
        <nav className="settings-section-nav" aria-label="Settings sections">
          {(["general", "models", "extensions", "operations", "agent"] as const).map((item) => (
            <button key={item} className={section === item ? "active" : ""} onClick={() => setSection(item)}>
              {sectionTitle(item)}
            </button>
          ))}
        </nav>
        <main className="settings-page">
          {section === "general" ? (
            <>
              <section className="settings-card settings-card-config">
                <div className="settings-card-heading"><div><h3>Connection</h3><p>Choose whether this Desktop manages a local Node or connects over the LAN.</p></div></div>
                <div className="settings-form settings-form-grid">
                  <label className="settings-field">
                    <span>Run location</span>
                    <select value={props.connectionForm.targetType} onChange={(event) => props.setConnectionForm((current) => ({ ...current, targetType: event.target.value === "lan" ? "lan" : "local", accessToken: "" }))}>
                      <option value="local">Run on this computer</option><option value="lan">Connect to a Node on the LAN</option>
                    </select>
                  </label>
                  <label className="settings-field"><span>Target name</span><input value={props.connectionForm.targetName} onChange={(event) => props.setConnectionForm((current) => ({ ...current, targetName: event.target.value }))} /></label>
                  <label className="settings-field"><span>Node URL</span><input value={props.connectionForm.clientApiBaseUrl} onChange={(event) => props.setConnectionForm((current) => ({ ...current, clientApiBaseUrl: event.target.value }))} spellCheck={false} /></label>
                  {props.connectionForm.targetType === "lan" ? (
                    <label className="settings-field settings-field-token"><span>Access token</span><input type="password" autoComplete="new-password" value={props.connectionForm.accessToken ?? ""} onChange={(event) => props.setConnectionForm((current) => ({ ...current, accessToken: event.target.value }))} placeholder={props.diagnostics?.clientApiCredentialConfigured ? "Saved securely; leave blank to keep it" : "Bearer token"} /></label>
                  ) : null}
                </div>
                <div className="runtime-actions">
                  <button className="secondary" onClick={props.onTestConnection} disabled={props.savingConnection || props.testingConnection}>{props.testingConnection ? "Testing" : "Test connection"}</button>
                  <button onClick={props.onSaveConnection} disabled={props.savingConnection || props.testingConnection}>{props.savingConnection ? "Saving" : "Save & apply"}</button>
                </div>
                {props.connectionFeedback ? <small>{props.connectionFeedback}</small> : null}
              </section>
              <section className="settings-card settings-card-connection">
                <h3>Device</h3>
                <dl className="diagnostics-grid">
                  <div><dt>Target</dt><dd>{props.diagnostics?.target.name ?? "-"}</dd></div>
                  <div><dt>Node</dt><dd>{props.diagnostics?.nodeName ?? props.diagnostics?.nodeId ?? "-"}</dd></div>
                  <div><dt>Client API</dt><dd>{props.diagnostics?.clientApiHealthy ? "healthy" : "offline"}</dd></div>
                  <div><dt>Protocol</dt><dd>{props.diagnostics?.clientApiProtocolVersion ? `v${props.diagnostics.clientApiProtocolVersion}` : "-"}</dd></div>
                  <div><dt>Desktop</dt><dd>{props.diagnostics?.desktopVersion ?? "-"}</dd></div>
                  <div><dt>Node version</dt><dd>{props.diagnostics?.clientApiProductVersion ?? "-"}</dd></div>
                </dl>
              </section>
              <section className="settings-card settings-card-paths">
                <h3>Paths</h3>
                <dl className="diagnostics-stack settings-paths-grid">
                  <div><dt>OpenPPX root</dt><dd>{props.diagnostics?.openppxRoot || "-"}</dd></div>
                  <div><dt>Python</dt><dd>{props.diagnostics?.pythonBin || "-"}</dd></div>
                  <div><dt>Client API</dt><dd>{props.diagnostics?.clientApiBaseUrl || "-"}</dd></div>
                </dl>
              </section>
            </>
          ) : null}

          {section === "models" ? (
            <section className="settings-card settings-card-models">
              <div className="settings-card-heading"><div><h3>Model Profiles</h3><p>Configured provider and model choices for this Node.</p></div><button className="secondary settings-quiet-button" onClick={props.onRefreshModels}>Refresh</button></div>
              <div className="settings-resource-list">
                {props.modelProfiles.length ? props.modelProfiles.map((profile) => (
                  <article key={profile.id} className="settings-resource-row">
                    <div><strong>{profile.id}</strong><p>{profile.provider} · {profile.model}</p></div>
                    <span className={profile.credentialState === "available" || profile.credentialState === "not_required" ? "resource-state ready" : "resource-state blocked"}>{profile.credentialState}</span>
                  </article>
                )) : <p className="extension-empty">No Model Profiles configured.</p>}
              </div>
            </section>
          ) : null}

          {section === "extensions" ? (
            <section className="settings-card settings-card-extensions">
              <div className="settings-card-heading"><div><h3>Extensions</h3><p>Plugin, App, MCP, and Skill resources owned by this Node.</p></div><button className="secondary settings-quiet-button" onClick={props.onRefreshExtensions} disabled={props.extensionsLoading}>{props.extensionsLoading ? "Refreshing" : "Refresh"}</button></div>
              {props.extensionsError ? <p className="settings-inline-error">{props.extensionsError}</p> : null}
              <div className="extension-kind-grid">
                {(["plugin", "app", "mcp", "skill"] as const).map((kind) => {
                  const items = props.extensions.filter((item) => item.kind === kind);
                  return (
                    <section className="extension-kind" key={kind} aria-label={`${kind} extensions`}>
                      <header><h4>{kind === "mcp" ? "MCP" : sectionTitle(kind)}</h4><span>{items.length}</span></header>
                      {items.length ? <div className="extension-list">{items.map((extension) => {
                        const enabled = props.selectedAgentId ? extension.enabledAgentIds.includes(props.selectedAgentId) : false;
                        const mutable = extension.kind !== "app" && extension.status !== "builtin" && Boolean(props.selectedAgentId);
                        const pending = props.extensionMutationId === `${extension.kind}:${extension.id}`;
                        return (
                          <article className="extension-row" key={`${extension.kind}:${extension.id}`}>
                            <div className="extension-row-copy"><div className="extension-row-title"><strong>{extension.displayName}</strong><span className={`extension-ready ${extension.readiness.ready ? "ready" : "blocked"}`}>{extension.readiness.ready ? extension.status : "needs attention"}</span></div><p>{extension.description}</p><small>{extension.version} · {extension.source.trust} · {extension.risk} risk</small></div>
                            <button className="secondary extension-toggle" disabled={!mutable || pending} onClick={() => props.onSetExtensionEnabled(extension, !enabled)}>{pending ? "Applying" : extension.kind === "app" ? "Connections" : extension.status === "builtin" ? "Built in" : enabled ? "Disable" : "Enable"}</button>
                          </article>
                        );
                      })}</div> : <p className="extension-empty">No {kind === "mcp" ? "MCP servers" : `${kind}s`} installed.</p>}
                    </section>
                  );
                })}
              </div>
            </section>
          ) : null}

          {section === "operations" ? (
            <>
              <section className="settings-card settings-card-runtime">
                <div className="settings-runtime-copy"><h3>Runtime</h3><p>{props.runtime.summary}</p></div>
                <div className="runtime-actions settings-runtime-actions"><button onClick={props.onRuntimeAction}>{runtimeActionLabel(props.runtime.state)}</button><button className="secondary" onClick={props.onStopRuntime}>Stop</button><button className="secondary" onClick={props.onRefreshOperations} disabled={props.operationsLoading}>{props.operationsLoading ? "Refreshing" : "Refresh"}</button></div>
              </section>
              {props.operationsError ? <p className="settings-inline-error settings-operations-error">{props.operationsError}</p> : null}
              <section className="settings-card settings-card-health">
                <div className="settings-card-heading"><div><h3>Node health</h3><p>One authoritative view of runtime, storage, credentials, Extensions, and isolation.</p></div><span className={`operations-state ${props.operationsOverview?.state ?? "unavailable"}`}>{props.operationsOverview?.state ?? "unavailable"}</span></div>
                <div className="operations-component-list">
                  {props.operationsOverview?.components.length ? props.operationsOverview.components.map((component) => (
                    <article className="operations-component" key={component.component}>
                      <span className={`operations-component-dot ${component.state}`} />
                      <div><span>{component.component}</span><p>{component.reason}</p>{component.remediation ? <small>{component.remediation}</small> : null}</div>
                      <em>{component.state}</em>
                    </article>
                  )) : <p className="extension-empty">Health information is not available yet.</p>}
                </div>
              </section>
              <section className="settings-card settings-card-operations-summary">
                <h3>Overview</h3>
                <dl className="operations-summary-grid">
                  <div><dt>Durable Tasks</dt><dd>{props.operationsOverview?.tasks.total ?? 0}</dd></div>
                  <div><dt>Cron jobs</dt><dd>{props.operationsOverview?.automation.cronJobs ?? 0}</dd></div>
                  <div><dt>Heartbeat</dt><dd>{props.operationsOverview?.automation.heartbeatEnabled ? "enabled" : "disabled"}</dd></div>
                </dl>
              </section>
              <section className="settings-card settings-card-audit">
                <div className="settings-card-heading"><div><h3>Recent activity</h3><p>Redacted Action decisions and outcomes. Request and result payloads are never stored here.</p></div></div>
                <div className="operations-audit-list">
                  {props.operationsAudit.length ? props.operationsAudit.map((item) => (
                    <article className="operations-audit-row" key={item.id}>
                      <div><span>{item.actionId}</span><p>{item.actorId} · {formatOperationTime(item.recordedAt)}</p></div>
                      <span className={`operations-audit-outcome ${item.ok === false ? "failed" : item.ok === true ? "succeeded" : "pending"}`}>{item.outcomeCode ?? item.decisionCode}</span>
                      <small>{item.risk} risk</small>
                    </article>
                  )) : <p className="extension-empty">No Action activity has been recorded yet.</p>}
                </div>
              </section>
            </>
          ) : null}

          {section === "agent" ? (
            <section className="settings-card settings-card-agent">
              <h3>Selected Agent</h3>
              {selectedAgent ? (
                <><div className="agent-settings-identity"><span>{selectedAgent.name.slice(0, 1).toUpperCase()}</span><div><strong>{selectedAgent.name}</strong><p>{selectedAgent.description}</p></div></div><dl className="diagnostics-grid"><div><dt>ID</dt><dd>{selectedAgent.id}</dd></div><div><dt>Status</dt><dd>{selectedAgent.status}</dd></div><div><dt>Enabled Extensions</dt><dd>{props.extensions.filter((item) => item.enabledAgentIds.includes(selectedAgent.id)).length}</dd></div><div><dt>Tags</dt><dd>{selectedAgent.tags.join(", ") || "None"}</dd></div></dl></>
              ) : <p className="extension-empty">Select an Agent in the workspace sidebar.</p>}
            </section>
          ) : null}
        </main>
      </div>
    </section>
  );
}
