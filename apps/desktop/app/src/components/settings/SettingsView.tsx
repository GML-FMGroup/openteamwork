import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import type {
  AgentProfile,
  ClientDiagnostics,
  ConnectionProfileSummary,
  ConnectionSettings,
  ExtensionSummary,
  ModelProfileSummary,
  RuntimeStatus,
} from "../../types";
import { CollapsedSidebarTools } from "../workspace/ContextSidebar";
import { ExtensionsSettings } from "./ExtensionsSettings";
import { OperationsSettings } from "./OperationsSettings";
import { AgentSettings } from "./AgentSettings";
import { PreferencesSettings } from "./PreferencesSettings";
import type { DesktopPreferenceChanges, DesktopPreferences } from "../../lib/desktop-preferences";

export type SettingsSection = "general" | "models" | "operations" | "agent" | "preferences";
type ControlPanelArea = "settings" | "extensions";

const SETTINGS_SECTIONS: SettingsSection[] = ["general", "models", "agent", "operations", "preferences"];
const EXTENSION_SECTIONS: ExtensionSummary["kind"][] = ["plugin", "app", "mcp", "skill"];

interface SettingsViewProps {
  area: ControlPanelArea;
  initialSection?: SettingsSection;
  initialExtensionKind?: ExtensionSummary["kind"];
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
  selectedAgentId: string;
  sidebarCollapsed: boolean;
  canCreateSession: boolean;
  setConnectionForm: Dispatch<SetStateAction<ConnectionSettings>>;
  onRevealSidebar: () => void;
  onNewSession: () => void;
  onSearchSessions: () => void;
  onRuntimeAction: () => void;
  onStopRuntime: () => void;
  onTestConnection: () => void;
  onSaveConnection: () => Promise<void>;
  onRefreshExtensions: () => void;
  onRefreshModels: () => void;
  onNewModelProfile: () => void;
  onEditModelProfile: (profileId: string) => void;
  onSetExtensionEnabled: (extension: ExtensionSummary, enabled: boolean) => void;
  onWorkspaceChanged: () => Promise<void>;
  preferences: DesktopPreferences;
  onChangePreferences: (changes: DesktopPreferenceChanges) => void;
  onRequestNotificationPermission: () => Promise<NotificationPermission | "unsupported">;
}

function sectionTitle(section: string): string {
  return section[0].toUpperCase() + section.slice(1);
}

function extensionSectionTitle(kind: ExtensionSummary["kind"]): string {
  if (kind === "mcp") return "MCP Servers";
  return `${sectionTitle(kind)}s`;
}

/** Contextual Desktop control interface for Settings and Extension management. */
export function SettingsView(props: SettingsViewProps) {
  const [section, setSection] = useState<SettingsSection>(props.initialSection ?? "general");
  const [extensionKind, setExtensionKind] = useState<ExtensionSummary["kind"]>(props.initialExtensionKind ?? "plugin");
  const extensionsArea = props.area === "extensions";
  const [connectionProfiles, setConnectionProfiles] = useState<ConnectionProfileSummary[]>([]);
  const [profileWorkingId, setProfileWorkingId] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);

  async function refreshConnectionProfiles(): Promise<void> {
    try {
      const nextProfiles = (await window.ppxClient.listConnectionProfiles()).profiles;
      if (JSON.stringify(nextProfiles) !== JSON.stringify(connectionProfiles)) {
        setConnectionProfiles(nextProfiles);
      }
    } catch (reason) {
      setProfileError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  useEffect(() => {
    if (!extensionsArea && section === "general") void refreshConnectionProfiles();
  }, [extensionsArea, section]);

  async function activateConnectionProfile(profile: ConnectionProfileSummary): Promise<void> {
    setProfileWorkingId(profile.targetId);
    setProfileError(null);
    try {
      await window.ppxClient.activateConnectionProfile(profile.targetId);
      props.setConnectionForm({
        targetType: profile.targetType,
        targetId: profile.targetId,
        targetName: profile.targetName,
        clientApiBaseUrl: profile.clientApiBaseUrl,
        accessToken: "",
      });
      await props.onWorkspaceChanged();
      await refreshConnectionProfiles();
    } catch (reason) {
      setProfileError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setProfileWorkingId(null);
    }
  }

  async function removeConnectionProfile(profile: ConnectionProfileSummary): Promise<void> {
    if (!window.confirm(`Remove the saved Node “${profile.targetName}”?`)) return;
    setProfileWorkingId(profile.targetId);
    setProfileError(null);
    try {
      await window.ppxClient.removeConnectionProfile(profile.targetId);
      await refreshConnectionProfiles();
    } catch (reason) {
      setProfileError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setProfileWorkingId(null);
    }
  }

  async function saveConnection(): Promise<void> {
    await props.onSaveConnection();
    await refreshConnectionProfiles();
  }

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
          <strong>{extensionsArea ? "Extensions" : "Settings"}</strong>
          <span className="topbar-location">{extensionsArea ? extensionSectionTitle(extensionKind) : sectionTitle(section)}</span>
        </div>
      </header>
      <div className="workspace-frame settings-frame settings-control-frame">
        <nav className="settings-section-nav" aria-label={extensionsArea ? "Extension sections" : "Settings sections"}>
          {extensionsArea ? EXTENSION_SECTIONS.map((kind) => (
            <button key={kind} className={extensionKind === kind ? "active" : ""} onClick={() => setExtensionKind(kind)}>
              {extensionSectionTitle(kind)}
            </button>
          )) : SETTINGS_SECTIONS.map((item) => (
            <button key={item} className={section === item ? "active" : ""} onClick={() => setSection(item)}>
              {sectionTitle(item)}
            </button>
          ))}
        </nav>
        <main className="settings-page">
          {!extensionsArea && section === "general" ? (
            <>
              <section className="settings-card settings-card-targets">
                <div className="settings-card-heading"><div><h3>Saved Nodes</h3><p>Switch between local and trusted LAN Nodes without re-entering their address.</p></div></div>
                {profileError ? <p className="settings-inline-error">{profileError}</p> : null}
                <div className="connection-profile-list">
                  {connectionProfiles.length ? connectionProfiles.map((profile) => (
                    <article className={profile.active ? "connection-profile active" : "connection-profile"} key={profile.targetId}>
                      <span className={profile.active ? "node-beacon healthy" : "node-beacon"} />
                      <div><strong>{profile.targetName}</strong><p>{profile.targetType === "local" ? "This computer" : profile.clientApiBaseUrl}</p></div>
                      {profile.credentialConfigured ? <small>Token secured</small> : null}
                      <div className="extension-actions">
                        <button className="secondary" disabled={profile.active || profileWorkingId === profile.targetId} onClick={() => void activateConnectionProfile(profile)}>{profile.active ? "Active" : profileWorkingId === profile.targetId ? "Connecting" : "Use"}</button>
                        {!profile.active ? <button className="danger secondary" disabled={profileWorkingId === profile.targetId} onClick={() => void removeConnectionProfile(profile)}>Remove</button> : null}
                      </div>
                    </article>
                  )) : <p className="extension-empty">Save the connection below to add the first Node target.</p>}
                </div>
              </section>
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
                  <button onClick={() => void saveConnection()} disabled={props.savingConnection || props.testingConnection}>{props.savingConnection ? "Saving" : "Save & apply"}</button>
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

          {!extensionsArea && section === "models" ? (
            <section className="settings-card settings-card-models">
              <div className="settings-card-heading"><div><h3>Model Profiles</h3><p>Reusable provider, model, access, and fallback policies for Agents on this Node.</p></div><div className="settings-heading-actions"><button className="secondary settings-quiet-button" onClick={props.onRefreshModels}>Refresh</button><button onClick={props.onNewModelProfile}>New Profile</button></div></div>
              <div className="settings-resource-list">
                {props.modelProfiles.length ? props.modelProfiles.map((profile) => (
                  <button key={profile.id} className="settings-resource-row" onClick={() => props.onEditModelProfile(profile.id)}>
                    <div><strong>{profile.displayName}</strong><p>{profile.provider} · {profile.model}</p></div>
                    <span className="settings-resource-meta"><span className={profile.credentialState === "available" || profile.credentialState === "not_required" ? "resource-state ready" : "resource-state blocked"}>{profile.credentialState.replace("_", " ")}</span>{!profile.enabled ? <em>disabled</em> : null}<span aria-hidden="true">›</span></span>
                  </button>
                )) : <p className="extension-empty">No Model Profiles configured.</p>}
              </div>
            </section>
          ) : null}

          {extensionsArea ? (
            <ExtensionsSettings
              key={extensionKind}
              kind={extensionKind}
              extensions={props.extensions}
              agents={props.agents}
              selectedAgentId={props.selectedAgentId}
              loading={props.extensionsLoading}
              error={props.extensionsError}
              mutationId={props.extensionMutationId}
              onRefresh={props.onRefreshExtensions}
              onSetEnabled={props.onSetExtensionEnabled}
            />
          ) : null}

          {!extensionsArea && section === "operations" ? (
            <OperationsSettings
              runtime={props.runtime}
              agents={props.agents}
              selectedAgentId={props.selectedAgentId}
              onRuntimeAction={props.onRuntimeAction}
              onStopRuntime={props.onStopRuntime}
            />
          ) : null}

          {!extensionsArea && section === "agent" ? (
            <AgentSettings selectedAgentId={props.selectedAgentId} modelProfiles={props.modelProfiles} onWorkspaceChanged={props.onWorkspaceChanged} />
          ) : null}

          {!extensionsArea && section === "preferences" ? (
            <PreferencesSettings
              preferences={props.preferences}
              onChange={props.onChangePreferences}
              onRequestNotificationPermission={props.onRequestNotificationPermission}
            />
          ) : null}
        </main>
      </div>
    </section>
  );
}
