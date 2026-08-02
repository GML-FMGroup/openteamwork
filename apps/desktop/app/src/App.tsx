import { useEffect, useRef, useState } from "react";
import { SettingsView } from "./components/settings/SettingsView";
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
import { useTranscriptFollow } from "./hooks/use-transcript-follow";

type NavView = "chat" | "settings";

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
  const columnLayout = useColumnLayout();
  const transcript = useTranscriptFollow(workspace.messages, workspace.transcriptResetKey);
  const [view, setView] = useState<NavView>("chat");
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [sidebarSearchRequest, setSidebarSearchRequest] = useState(0);
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
    if (workspace.selectedAgentBusy || !workspace.composer.trim()) {
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

  function revealSidebarForSearch(): void {
    setLeftSidebarCollapsed(false);
    setSidebarSearchRequest((current) => current + 1);
  }

  function createSessionFromTopbar(): void {
    setView("chat");
    void workspace.createSession();
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

  if (!workspace.ready || !workspace.runtime) {
    return (
      <div className="loading-shell" aria-live="polite">
        <div>
          <span className="loading-brand" aria-hidden="true">P</span>
          <span className="loading-caption">Opening OpenPPX workspace…</span>
        </div>
      </div>
    );
  }

  const runtime = workspace.runtime;
  const workspaceAgentName = workspace.selectedAgent?.name ?? "OpenPPX";
  const titlebarTitle =
    view === "chat" ? workspace.selectedSession?.title ?? workspace.selectedAgent?.name ?? "No session" : "Settings";
  const titlebarSubtitle = view === "chat" ? workspace.selectedAgent?.name ?? "No agent selected" : "ppx-client";
  const canSend = Boolean(workspace.composer.trim()) && Boolean(workspace.selectedAgentId) && !workspace.selectedAgentBusy;

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
        view={view}
        runtime={runtime}
        diagnostics={workspace.diagnostics}
        agents={workspace.agents}
        sessions={workspace.sessions}
        selectedAgentId={workspace.selectedAgentId}
        selectedSessionId={workspace.selectedSessionId}
        sendingSessionIds={workspace.activeSessionIds}
        collapsed={leftSidebarCollapsed}
        searchFocusRequest={sidebarSearchRequest}
        onToggleCollapse={() => setLeftSidebarCollapsed((current) => !current)}
        onChangeView={handleChangeView}
        onSelectAgent={(agentId) => void workspace.switchAgent(agentId)}
        onSelectSession={(session) => void workspace.switchSession(session)}
        onNewSession={() => void workspace.createSession()}
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
                <button className="topbar-pill" onClick={() => setView("settings")} title="View runtime status">
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
                  helperText={
                    workspace.sendError ??
                    (workspace.selectedAgentBusy
                      ? "The current Agent is running. Progress appears in the right panel."
                      : "")
                  }
                  agentName={workspaceAgentName}
                  onChange={workspace.setComposer}
                  onKeyDown={handleComposerKeyDown}
                  onSend={() => {
                    transcript.followLatest();
                    void workspace.sendMessage();
                  }}
                  onStop={() => void workspace.cancelCurrentRun()}
                />
              </main>
            </div>
          </section>
          <WorkspaceInspector
            sessionId={workspace.selectedSessionId}
            messages={workspace.messages}
            running={workspace.currentSessionRunning}
            collapsed={inspectorCollapsed}
          />
        </>
      ) : (
        <SettingsView
          runtime={runtime}
          diagnostics={workspace.diagnostics}
          connectionForm={workspace.connectionForm}
          savingConnection={workspace.savingConnection}
          testingConnection={workspace.testingConnection}
          connectionFeedback={workspace.connectionFeedback}
          sidebarCollapsed={leftSidebarCollapsed}
          setConnectionForm={workspace.setConnectionForm}
          onRevealSidebar={() => setLeftSidebarCollapsed(false)}
          onNewSession={createSessionFromTopbar}
          onSearchSessions={revealSidebarForSearch}
          canCreateSession={Boolean(workspace.selectedAgentId)}
          onReturnToChat={() => setView("chat")}
          onRuntimeAction={() => void workspace.runRuntimeAction()}
          onStopRuntime={() => void workspace.stopRuntime()}
          onRefreshDiagnostics={() => void workspace.refreshDiagnostics()}
          onTestConnection={() => void workspace.testConnection()}
          onSaveConnection={() => void workspace.saveConnection()}
        />
      )}
    </div>
  );
}
