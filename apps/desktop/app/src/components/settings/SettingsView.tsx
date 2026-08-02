import type { Dispatch, SetStateAction } from "react";
import type { ClientDiagnostics, ConnectionSettings, RuntimeState, RuntimeStatus } from "../../types";
import { CollapsedSidebarTools } from "../workspace/ContextSidebar";

interface SettingsViewProps {
  runtime: RuntimeStatus;
  diagnostics: ClientDiagnostics | null;
  connectionForm: ConnectionSettings;
  savingConnection: boolean;
  testingConnection: boolean;
  connectionFeedback: string | null;
  sidebarCollapsed: boolean;
  canCreateSession: boolean;
  setConnectionForm: Dispatch<SetStateAction<ConnectionSettings>>;
  onRevealSidebar: () => void;
  onNewSession: () => void;
  onSearchSessions: () => void;
  onReturnToChat: () => void;
  onRuntimeAction: () => void;
  onStopRuntime: () => void;
  onRefreshDiagnostics: () => void;
  onTestConnection: () => void;
  onSaveConnection: () => void;
}

function runtimeActionLabel(state: RuntimeState): string {
  if (state === "stopped") {
    return "Start";
  }
  if (state === "healthy") {
    return "Restart";
  }
  return "Retry";
}

/** Settings surface kept separate from workspace orchestration and state ownership. */
export function SettingsView({
  runtime,
  diagnostics,
  connectionForm,
  savingConnection,
  testingConnection,
  connectionFeedback,
  sidebarCollapsed,
  canCreateSession,
  setConnectionForm,
  onRevealSidebar,
  onNewSession,
  onSearchSessions,
  onReturnToChat,
  onRuntimeAction,
  onStopRuntime,
  onRefreshDiagnostics,
  onTestConnection,
  onSaveConnection,
}: SettingsViewProps) {
  return (
    <section className="workspace-shell settings-shell">
      <header className="column-topbar workspace-topbar">
        <div className="topbar-copy">
          {sidebarCollapsed ? (
            <CollapsedSidebarTools
              canCreateSession={canCreateSession}
              onRevealSidebar={onRevealSidebar}
              onNewSession={onNewSession}
              onSearchSessions={onSearchSessions}
            />
          ) : null}
          <strong>Settings</strong>
        </div>
        <div className="topbar-actions">
          <button className="topbar-pill" onClick={onReturnToChat} title="Return to conversation">
            <span className={`runtime-dot ${runtime.state}`} />
            {runtime.state}
          </button>
        </div>
      </header>
      <div className="workspace-frame settings-frame">
        <main className="settings-page">
          <section className="settings-card settings-card-runtime">
            <div className="settings-runtime-copy">
              <h3>Runtime status</h3>
              <p>{runtime.summary}</p>
            </div>
            <div className="runtime-actions settings-runtime-actions">
              <button onClick={onRuntimeAction}>{runtimeActionLabel(runtime.state)}</button>
              <button className="secondary" onClick={onStopRuntime}>
                Stop
              </button>
              <button className="secondary" onClick={onRefreshDiagnostics}>
                Refresh diagnostics
              </button>
            </div>
          </section>

          <section className="settings-card settings-card-config">
            <h3>Connection config</h3>
            <div className="settings-form settings-form-grid">
              <label className="settings-field">
                <span>Run location</span>
                <select
                  value={connectionForm.targetType}
                  onChange={(event) =>
                    setConnectionForm((current) => ({
                      ...current,
                      targetType: event.target.value === "lan" ? "lan" : "local",
                      accessToken: "",
                    }))
                  }
                >
                  <option value="local">Run on this computer</option>
                  <option value="lan">Connect to an OpenPPX Node on the LAN</option>
                </select>
              </label>
              <label className="settings-field">
                <span>Target name</span>
                <input
                  value={connectionForm.targetName}
                  onChange={(event) =>
                    setConnectionForm((current) => ({ ...current, targetName: event.target.value }))
                  }
                  placeholder="This Mac"
                />
              </label>
              <label className="settings-field">
                <span>Gateway URL</span>
                <input
                  value={connectionForm.clientApiBaseUrl}
                  onChange={(event) =>
                    setConnectionForm((current) => ({ ...current, clientApiBaseUrl: event.target.value }))
                  }
                  placeholder="http://127.0.0.1:8765"
                />
              </label>
              {connectionForm.targetType === "lan" ? (
                <label className="settings-field settings-field-token">
                  <span>Access Token</span>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={connectionForm.accessToken ?? ""}
                    onChange={(event) =>
                      setConnectionForm((current) => ({ ...current, accessToken: event.target.value }))
                    }
                    placeholder={
                      diagnostics?.mode === "lan" && diagnostics.clientApiCredentialConfigured
                        ? "Saved securely; leave blank to keep the current token"
                        : "Enter the remote OPENPPX_CLIENT_API_TOKEN"
                    }
                  />
                </label>
              ) : null}
            </div>
            <div className="runtime-actions">
              <button
                className="secondary"
                onClick={onTestConnection}
                disabled={savingConnection || testingConnection}
              >
                {testingConnection ? "Testing" : "Test connection"}
              </button>
              <button onClick={onSaveConnection} disabled={savingConnection || testingConnection}>
                {savingConnection ? "Saving" : "Save & apply"}
              </button>
            </div>
            {connectionFeedback ? <small>{connectionFeedback}</small> : null}
          </section>

          <section className="settings-card settings-card-connection">
            <h3>Connection</h3>
            <dl className="diagnostics-grid">
              <div><dt>Target</dt><dd>{diagnostics ? `${diagnostics.target.name} (${diagnostics.mode})` : "-"}</dd></div>
              <div><dt>Mode</dt><dd>{diagnostics?.mode ?? "-"}</dd></div>
              <div><dt>Client API</dt><dd>{diagnostics?.clientApiHealthy ? "healthy" : "offline"}</dd></div>
              <div>
                <dt>Protocol</dt>
                <dd>
                  {diagnostics?.clientApiProtocolVersion === undefined
                    ? "-"
                    : `v${diagnostics.clientApiProtocolVersion} / ${diagnostics.clientApiCompatibility ?? "unknown"}`}
                </dd>
              </div>
              <div><dt>Desktop version</dt><dd>{diagnostics?.desktopVersion ?? "-"}</dd></div>
              <div><dt>Node version</dt><dd>{diagnostics?.clientApiProductVersion ?? "-"}</dd></div>
              <div><dt>Authentication</dt><dd>{diagnostics?.clientApiAuthState ?? "unknown"}</dd></div>
              <div><dt>Node ID</dt><dd>{diagnostics?.nodeId ?? "-"}</dd></div>
              <div><dt>Process</dt><dd>{diagnostics?.clientApiProcessRunning ? "running" : "not managed"}</dd></div>
              <div><dt>Gateway control</dt><dd>{diagnostics?.clientApiManagedByClient ? "managed by client" : "external / LAN"}</dd></div>
            </dl>
          </section>

          <section className="settings-card settings-card-diagnostics">
            <h3>Diagnostics</h3>
            <dl className="diagnostics-grid">
              <div><dt>Root exists</dt><dd>{diagnostics?.openppxRootExists ? "yes" : "no"}</dd></div>
              <div><dt>Config exists</dt><dd>{diagnostics?.globalConfigExists ? "yes" : "no"}</dd></div>
              <div><dt>Bridge exists</dt><dd>{diagnostics?.bridgeScriptExists ? "yes" : "no"}</dd></div>
              <div><dt>Debug</dt><dd>{diagnostics?.debugEnabled ? "enabled" : "off"}</dd></div>
              <div>
                <dt>Dev fallbacks</dt>
                <dd>{diagnostics?.mockEnabled ? "mock" : diagnostics?.legacyBridgeEnabled ? "legacy bridge" : "off"}</dd>
              </div>
              <div><dt>Session cache</dt><dd>{diagnostics?.sessionCacheEntries ?? 0}</dd></div>
              <div><dt>Message cache</dt><dd>{diagnostics?.messageCacheEntries ?? 0}</dd></div>
              <div><dt>Client API URL</dt><dd>{diagnostics?.clientApiBaseUrl || "-"}</dd></div>
              <div><dt>Last API error</dt><dd>{diagnostics?.clientApiLastError || "-"}</dd></div>
              <div><dt>Agents</dt><dd>{diagnostics?.agentCount ?? 0}</dd></div>
            </dl>
          </section>

          <section className="settings-card settings-card-paths">
            <h3>Paths</h3>
            <dl className="diagnostics-stack settings-paths-grid">
              <div><dt>openppx root</dt><dd>{diagnostics?.openppxRoot || "-"}</dd></div>
              <div><dt>Python</dt><dd>{diagnostics?.pythonBin || "-"}</dd></div>
              <div><dt>Global config</dt><dd>{diagnostics?.globalConfigPath || "-"}</dd></div>
              <div><dt>Bridge script</dt><dd>{diagnostics?.bridgeScriptPath || "-"}</dd></div>
            </dl>
          </section>
        </main>
      </div>
    </section>
  );
}
