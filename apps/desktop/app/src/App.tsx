import { useEffect, useRef, useState } from "react";
import { SettingsView } from "./components/settings/SettingsView";
import { Composer } from "./components/workspace/Composer";
import { ContextSidebar, ShellIcon } from "./components/workspace/ContextSidebar";
import { Transcript } from "./components/workspace/Transcript";
import { WorkspaceInspector } from "./components/workspace/WorkspaceInspector";
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
  const transcript = useTranscriptFollow(workspace.messages, workspace.transcriptResetKey);
  const [view, setView] = useState<NavView>("chat");
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
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
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const narrowWorkspace = window.matchMedia("(max-width: 1080px)");
    const collapseForNarrowWorkspace = (matches: boolean): void => {
      if (matches) {
        setLeftSidebarCollapsed(true);
        setInspectorCollapsed(true);
      }
    };
    collapseForNarrowWorkspace(narrowWorkspace.matches);
    const handleChange = (event: MediaQueryListEvent): void => collapseForNarrowWorkspace(event.matches);
    narrowWorkspace.addEventListener("change", handleChange);
    return () => narrowWorkspace.removeEventListener("change", handleChange);
  }, []);

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

  if (workspace.bootstrapError) {
    return (
      <div className="loading-shell">
        <div>
          <strong>ppx-client failed to initialize</strong>
          <p>{workspace.bootstrapError}</p>
        </div>
      </div>
    );
  }

  if (!workspace.ready || !workspace.runtime) {
    return <div className="loading-shell">Loading ppx-client...</div>;
  }

  const runtime = workspace.runtime;
  const workspaceAgentName = workspace.selectedAgent?.name ?? "OpenPPX";
  const titlebarTitle =
    view === "chat" ? workspace.selectedSession?.title ?? workspace.selectedAgent?.name ?? "No session" : "Settings";
  const titlebarSubtitle = view === "chat" ? workspace.selectedAgent?.name ?? "No agent selected" : "ppx-client";
  const canSend = Boolean(workspace.composer.trim()) && Boolean(workspace.selectedAgentId) && !workspace.selectedAgentBusy;

  return (
    <div
      className={`app-shell ${leftSidebarCollapsed ? "sidebar-is-collapsed" : ""} ${
        inspectorCollapsed ? "inspector-is-collapsed" : ""
      }`}
    >
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
        onToggleCollapse={() => setLeftSidebarCollapsed((current) => !current)}
        onChangeView={setView}
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
                  <button
                    className="quiet-icon-button sidebar-reveal"
                    onClick={() => setLeftSidebarCollapsed(false)}
                    aria-label="Open sidebar"
                    title="Open sidebar (⌘B)"
                  >
                    <ShellIcon name="expand" />
                  </button>
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
                  <ShellIcon name={inspectorCollapsed ? "collapse" : "expand"} />
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
                  nodeName={workspace.diagnostics?.nodeName ?? runtime.target.name}
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
