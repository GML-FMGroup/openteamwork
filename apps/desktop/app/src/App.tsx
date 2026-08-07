import { useEffect, useRef, useState } from "react";
import { SettingsView, type SettingsSection } from "./components/settings/SettingsView";
import { OnboardingView } from "./components/setup/OnboardingView";
import { NewAgentDialog } from "./components/agents/NewAgentDialog";
import { ModelProfileDialog } from "./components/models/ModelProfileDialog";
import { AutomationsPage } from "./components/automations/AutomationsPage";
import { Composer } from "./components/workspace/Composer";
import {
  CollapsedSidebarTools,
  ContextSidebar,
  ShellIcon,
} from "./components/workspace/ContextSidebar";
import { Transcript } from "./components/workspace/Transcript";
import { WorkspaceInspector } from "./components/workspace/WorkspaceInspector";
import { ColumnResizeHandle } from "./components/workspace/ColumnResizeHandle";
import { COLUMN_WIDTH_LIMITS, useColumnLayout } from "./hooks/use-column-layout";
import { useDesktopWorkspace } from "./hooks/use-desktop-workspace";
import { useDesktopPreferences } from "./hooks/use-desktop-preferences";
import { useTranscriptFollow } from "./hooks/use-transcript-follow";
import type { ExtensionSummary } from "./types";

type NavView = "chat" | "settings" | "automations";
type SettingsDestination =
  | { area: "settings"; section: SettingsSection }
  | { area: "extensions"; extensionKind: ExtensionSummary["kind"] };

function resizeComposer(textarea: HTMLTextAreaElement | null): void {
  if (!textarea) {
    return;
  }
  const computedStyle = window.getComputedStyle(textarea);
  const lineHeight = Number.parseFloat(computedStyle.lineHeight) || 24;
  const minHeight = lineHeight * 2;
  const maxHeight = lineHeight * 12;
  textarea.style.height = "auto";
  const nextHeight = Math.min(Math.max(textarea.scrollHeight, minHeight), maxHeight);
  textarea.style.height = `${nextHeight}px`;
  textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
}

export function App() {
  const workspace = useDesktopWorkspace();
  const { preferences, updatePreferences, requestNotificationPermission } = useDesktopPreferences();
  const columnLayout = useColumnLayout();
  const transcript = useTranscriptFollow(workspace.messages, workspace.transcriptResetKey);
  const [view, setView] = useState<NavView>("chat");
  const [settingsDestination, setSettingsDestination] = useState<SettingsDestination>({ area: "settings", section: "general" });
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [sidebarSearchRequest, setSidebarSearchRequest] = useState(0);
  const [newAgentOpen, setNewAgentOpen] = useState(false);
  const [modelProfileDialog, setModelProfileDialog] = useState<{ mode: "new" | "edit"; profileId: string | null } | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    resizeComposer(composerRef.current);
  }, [workspace.composer]);

  useEffect(() => {
    function handleShellShortcut(event: KeyboardEvent): void {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "b") {
        return;
      }
      event.preventDefault();
      if (event.shiftKey) {
        setInspectorCollapsed((current) => !current);
      } else {
        setLeftSidebarCollapsed((current) => !current);
      }
    }
    window.addEventListener("keydown", handleShellShortcut);
    return () => window.removeEventListener("keydown", handleShellShortcut);
  }, []);

  useEffect(() => {
    if (columnLayout.compactLayout) {
      setLeftSidebarCollapsed(true);
      setInspectorCollapsed(true);
    }
  }, [columnLayout.compactLayout]);

  function handleComposerKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    event.preventDefault();
    if (workspace.selectedAgentBusy || (!workspace.composer.trim() && workspace.attachments.length === 0)) {
      return;
    }
    transcript.followLatest();
    void workspace.sendMessage();
  }

  function handleChangeView(nextView: NavView): void {
    setView(nextView);
    if (columnLayout.compactLayout) {
      setLeftSidebarCollapsed(true);
    }
  }

  function openSettings(): void {
    setSettingsDestination({ area: "settings", section: "general" });
    handleChangeView("settings");
  }

  function openExtensions(): void {
    setSettingsDestination({ area: "extensions", extensionKind: "plugin" });
    handleChangeView("settings");
  }

  function openAutomations(): void {
    handleChangeView("automations");
  }

  function revealSidebarForSearch(): void {
    setLeftSidebarCollapsed(false);
    setSidebarSearchRequest((current) => current + 1);
  }

  function createSessionFromTopbar(): void {
    handleChangeView("chat");
    void workspace.createSession();
  }

  function selectAgentFromSidebar(agentId: string): void {
    handleChangeView("chat");
    void workspace.switchAgent(agentId);
  }

  function openNewAgentFromSidebar(): void {
    handleChangeView("chat");
    workspace.clearAgentCreateError();
    setNewAgentOpen(true);
  }

  function selectSessionFromSidebar(session: Parameters<typeof workspace.switchSession>[0]): void {
    handleChangeView("chat");
    void workspace.switchSession(session);
  }

  if (workspace.bootstrapError) {
    return (
      <div className="loading-shell">
        <div>
          <span className="loading-brand" aria-hidden="true">P</span>
          <strong>ppx-client failed to initialize</strong>
          <p>{workspace.bootstrapError}</p>
        </div>
      </div>
    );
  }

  if (!workspace.ready || !workspace.runtime || !workspace.setupStatus) {
    return (
      <div className="loading-shell" aria-live="polite">
        <div>
          <span className="loading-brand" aria-hidden="true">P</span>
          <span className="loading-caption">Opening OpenPPX workspace…</span>
        </div>
      </div>
    );
  }

  if (workspace.setupStatus.state !== "ready") {
    return (
      <OnboardingView
        status={workspace.setupStatus}
        form={workspace.setupForm}
        connection={workspace.connectionForm}
        diagnostics={workspace.diagnostics}
        submitting={workspace.setupSubmitting}
        testingConnection={workspace.testingConnection}
        savingConnection={workspace.savingConnection}
        error={workspace.setupError}
        providerModels={workspace.providerModels}
        providerAuth={workspace.providerAuth}
        providerAccessLoading={workspace.providerAccessLoading}
        providerAccessError={workspace.providerAccessError}
        connectionFeedback={workspace.connectionFeedback}
        setForm={workspace.setSetupForm}
        setConnection={workspace.setConnectionForm}
        onTestConnection={() => void workspace.testConnection()}
        onSaveConnection={() => void workspace.saveConnection()}
        onSubmit={() => void workspace.completeSetup()}
        onBeginProviderAuth={() => void workspace.beginProviderAuth()}
        onRefreshProviderAuth={() => void workspace.refreshProviderAuth()}
        onOpenProviderAuthPage={() => void workspace.openProviderAuthPage()}
      />
    );
  }

  const runtime = workspace.runtime;
  const workspaceAgentName = workspace.selectedAgent?.name ?? "OpenPPX";
  const titlebarTitle = view === "chat"
    ? workspace.selectedSession?.title ?? workspace.selectedAgent?.name ?? "No session"
    : view === "automations" ? "Automations" : settingsDestination.area === "extensions" ? "Extensions" : "Settings";
  const titlebarSubtitle = view === "chat" ? workspace.selectedAgent?.name ?? "No agent selected" : "ppx-client";
  const canSend = Boolean(workspace.composer.trim() || workspace.attachments.length) && Boolean(workspace.selectedAgentId) && !workspace.selectedAgentBusy;
  const suggestedAgentId = (() => {
    let index = workspace.agents.length + 1;
    while (workspace.agents.some((agent) => agent.id === `agent-${index}`)) index += 1;
    return `agent-${index}`;
  })();
  return (
    <div
      ref={columnLayout.shellRef}
      style={columnLayout.style}
      className={`app-shell ${leftSidebarCollapsed ? "sidebar-is-collapsed" : ""} ${
        inspectorCollapsed ? "inspector-is-collapsed" : ""
      } ${columnLayout.resizingColumn ? "column-resize-active" : ""}`}
    >
      {!columnLayout.compactLayout && !leftSidebarCollapsed ? (
        <ColumnResizeHandle
          side="left"
          label="Resize navigation sidebar"
          value={columnLayout.leftWidth}
          minimum={COLUMN_WIDTH_LIMITS.left.min}
          maximum={COLUMN_WIDTH_LIMITS.left.max}
          active={columnLayout.resizingColumn === "left"}
          onResizeStart={(clientX) => columnLayout.beginResize("left", clientX)}
          onKeyboardResize={(key, largeStep) => columnLayout.resizeWithKeyboard("left", key, largeStep)}
          onReset={() => columnLayout.resetColumn("left")}
        />
      ) : null}
      {view === "chat" && !columnLayout.compactLayout && !inspectorCollapsed ? (
        <ColumnResizeHandle
          side="right"
          label="Resize task panel"
          value={columnLayout.rightWidth}
          minimum={COLUMN_WIDTH_LIMITS.right.min}
          maximum={COLUMN_WIDTH_LIMITS.right.max}
          active={columnLayout.resizingColumn === "right"}
          onResizeStart={(clientX) => columnLayout.beginResize("right", clientX)}
          onKeyboardResize={(key, largeStep) => columnLayout.resizeWithKeyboard("right", key, largeStep)}
          onReset={() => columnLayout.resetColumn("right")}
        />
      ) : null}
      <ContextSidebar
        platform={window.ppxClient.platform}
        view={view}
        controlArea={view === "automations" ? "automations" : view === "settings" ? settingsDestination.area : null}
        runtime={runtime}
        diagnostics={workspace.diagnostics}
        userProfile={workspace.userProfile}
        agents={workspace.agents}
        sessions={workspace.sessions}
        selectedAgentId={workspace.selectedAgentId}
        selectedSessionId={workspace.selectedSessionId}
        sendingSessionIds={workspace.activeSessionIds}
        collapsed={leftSidebarCollapsed}
        searchFocusRequest={sidebarSearchRequest}
        onToggleCollapse={() => setLeftSidebarCollapsed((current) => !current)}
        onChangeView={handleChangeView}
        onOpenSettings={openSettings}
        onOpenExtensions={openExtensions}
        onOpenAutomations={openAutomations}
        onSelectAgent={selectAgentFromSidebar}
        onSelectSession={selectSessionFromSidebar}
        onRenameSession={(session, title) => void workspace.renameSession(session, title)}
        onArchiveSession={(session) => void workspace.archiveSession(session)}
        onForkSession={(session) => void workspace.forkSession(session)}
        onExportSession={(session) => void workspace.exportSession(session)}
        onDeleteSession={(session) => void workspace.deleteSession(session)}
        onNewAgent={openNewAgentFromSidebar}
        onNewSession={createSessionFromTopbar}
      />

      {view === "chat" ? (
        <>
          <section className="workspace-shell conversation-shell">
            <header className="column-topbar workspace-topbar">
              <div className="topbar-copy">
                {leftSidebarCollapsed ? (
                  <CollapsedSidebarTools
                    canCreateSession={Boolean(workspace.selectedAgentId)}
                    onRevealSidebar={() => setLeftSidebarCollapsed(false)}
                    onNewSession={createSessionFromTopbar}
                    onSearchSessions={revealSidebarForSearch}
                  />
                ) : null}
                <span className="topbar-agent">{titlebarSubtitle}</span>
                <strong>{titlebarTitle}</strong>
                <span className="topbar-location">
                  Running on {workspace.diagnostics?.nodeName ?? runtime.target.name}
                </span>
              </div>
              <div className="topbar-actions">
                <button className="topbar-pill" onClick={openSettings} title="View runtime status">
                  <span className={`runtime-dot ${runtime.state}`} />
                  {runtime.state === "healthy"
                    ? "Connected"
                    : runtime.state === "reconnecting"
                      ? "Reconnecting"
                      : runtime.state}
                </button>
                <button
                  className="quiet-icon-button task-panel-toggle"
                  onClick={() => setInspectorCollapsed((current) => !current)}
                  aria-label={inspectorCollapsed ? "Open task panel" : "Close task panel"}
                  title={inspectorCollapsed ? "Open task panel (⌘⇧B)" : "Close task panel (⌘⇧B)"}
                >
                  <ShellIcon name="sidebar-right" />
                </button>
              </div>
            </header>
            <div className="workspace-frame">
              <main className="workspace">
                <Transcript
                  messages={workspace.messages}
                  agentName={workspaceAgentName}
                  streamRef={transcript.streamRef}
                  showJumpToLatest={!transcript.followingLatest}
                  onScroll={transcript.handleScroll}
                  onJumpToLatest={transcript.jumpToLatest}
                  onUseSuggestion={workspace.setComposer}
                />
                <Composer
                  value={workspace.composer}
                  textareaRef={composerRef}
                  canSend={canSend}
                  busy={workspace.selectedAgentBusy}
                  canStop={Boolean(workspace.activeRunId)}
                  stopping={workspace.cancellingCurrentRun}
                  helperText={workspace.sendError ?? ""}
                  agentName={workspaceAgentName}
                  goal={workspace.currentGoal}
                  goalMutation={workspace.goalMutation}
                  goalMutationError={workspace.goalMutationError}
                  commands={workspace.slashCommands}
                  attachments={workspace.attachments}
                  onChange={workspace.setComposer}
                  onKeyDown={handleComposerKeyDown}
                  onSend={() => {
                    transcript.followLatest();
                    void workspace.sendMessage();
                  }}
                  onStop={() => void workspace.cancelCurrentRun()}
                  onAddAttachments={(files) => void workspace.addAttachments(files)}
                  onRemoveAttachment={workspace.removeAttachment}
                  onUpdateGoal={workspace.updateCurrentGoal}
                  onTransitionGoal={workspace.transitionCurrentGoal}
                />
              </main>
            </div>
          </section>
          <WorkspaceInspector
            sessionId={workspace.selectedSessionId}
            messages={workspace.messages}
            collapsed={inspectorCollapsed}
            artifacts={workspace.sessionArtifacts}
            onLoadArtifact={workspace.loadArtifactData}
          />
        </>
      ) : view === "automations" ? (
        <AutomationsPage
          agents={workspace.agents}
          selectedAgentId={workspace.selectedAgentId}
          userId={workspace.userProfile.id}
          sidebarCollapsed={leftSidebarCollapsed}
          canCreateSession={Boolean(workspace.selectedAgentId)}
          onRevealSidebar={() => setLeftSidebarCollapsed(false)}
          onNewSession={createSessionFromTopbar}
          onSearchSessions={revealSidebarForSearch}
        />
      ) : (
        <SettingsView
          key={settingsDestination.area === "settings" ? `settings:${settingsDestination.section}` : `extensions:${settingsDestination.extensionKind}`}
          area={settingsDestination.area}
          initialSection={settingsDestination.area === "settings" ? settingsDestination.section : undefined}
          initialExtensionKind={settingsDestination.area === "extensions" ? settingsDestination.extensionKind : undefined}
          runtime={runtime}
          diagnostics={workspace.diagnostics}
          connectionForm={workspace.connectionForm}
          savingConnection={workspace.savingConnection}
          testingConnection={workspace.testingConnection}
          connectionFeedback={workspace.connectionFeedback}
          extensions={workspace.extensions}
          modelProfiles={workspace.modelProfiles}
          agents={workspace.agents}
          extensionsLoading={workspace.extensionsLoading}
          extensionsError={workspace.extensionsError}
          extensionMutationId={workspace.extensionMutationId}
          selectedAgentId={workspace.selectedAgentId}
          sidebarCollapsed={leftSidebarCollapsed}
          setConnectionForm={workspace.setConnectionForm}
          onRevealSidebar={() => setLeftSidebarCollapsed(false)}
          onNewSession={createSessionFromTopbar}
          onSearchSessions={revealSidebarForSearch}
          canCreateSession={Boolean(workspace.selectedAgentId)}
          onRuntimeAction={() => void workspace.runRuntimeAction()}
          onStopRuntime={() => void workspace.stopRuntime()}
          onTestConnection={() => void workspace.testConnection()}
          onSaveConnection={workspace.saveConnection}
          onRefreshExtensions={() => void workspace.refreshExtensions()}
          onRefreshModels={() => void workspace.refreshModelProfiles()}
          onNewModelProfile={() => setModelProfileDialog({ mode: "new", profileId: null })}
          onEditModelProfile={(profileId) => setModelProfileDialog({ mode: "edit", profileId })}
          onSetExtensionEnabled={(extension, enabled) => void workspace.setExtensionEnabled(extension, enabled)}
          onWorkspaceChanged={workspace.reloadWorkspace}
          preferences={preferences}
          onChangePreferences={updatePreferences}
          onRequestNotificationPermission={requestNotificationPermission}
        />
      )}
      {newAgentOpen ? (
        <NewAgentDialog
          suggestedAgentId={suggestedAgentId}
          modelProfiles={workspace.modelProfiles}
          creating={workspace.agentCreating}
          error={workspace.agentCreateError}
          onCancel={() => setNewAgentOpen(false)}
          onCreate={(input) => {
            void workspace.createAgent(input).then((created) => {
              if (created) {
                setNewAgentOpen(false);
                setView("chat");
              }
            });
          }}
        />
      ) : null}
      {modelProfileDialog ? (
        <ModelProfileDialog
          mode={modelProfileDialog.mode}
          profileId={modelProfileDialog.profileId}
          profiles={workspace.modelProfiles}
          providers={workspace.setupStatus.providers}
          onRead={workspace.readModelProfile}
          onGetModels={workspace.getModelProviderModels}
          onGetAuth={workspace.getModelProviderAuth}
          onBeginAuth={workspace.beginModelProviderAuth}
          onRefreshAuth={workspace.refreshModelProviderAuth}
          onOpenExternal={workspace.openExternalUrl}
          onCreate={workspace.createModelProfile}
          onUpdate={workspace.updateModelProfile}
          onCancel={() => setModelProfileDialog(null)}
          onSaved={() => setModelProfileDialog(null)}
        />
      ) : null}
    </div>
  );
}
