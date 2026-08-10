import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { sortSessionsByRecency } from "../../lib/session-order";
import type {
  AgentProfile,
  ClientDiagnostics,
  DesktopPlatform,
  RuntimeStatus,
  SessionSummary,
  UserProfile,
} from "../../types";
import { productProfile } from "../../../../product";

function runtimeStateLabel(state: RuntimeStatus["state"]): string {
  switch (state) {
    case "needs_configuration":
      return "setup required";
    case "reconnecting":
      return "reconnecting";
    case "starting":
      return "starting";
    case "healthy":
      return "healthy";
    case "stopped":
      return "stopped";
    case "error":
      return "error";
  }
}

type ShellIconName = "settings" | "extensions" | "automations" | "expand" | "search" | "plus" | "sidebar" | "sidebar-right" | "more";

export function ShellIcon({ name }: { name: ShellIconName }) {
  const paths: Record<ShellIconName, ReactNode> = {
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21h-4v-.09A1.7 1.7 0 0 0 8.6 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.1-.4H3v-4h.09A1.7 1.7 0 0 0 4.6 8.6a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1.1V3h4v.09A1.7 1.7 0 0 0 15.4 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.13.38.34.72.6 1 .3.28.68.42 1.1.4h.09v4h-.09c-.42-.02-.8.12-1.1.4-.26.28-.47.62-.6 1Z" />
      </>
    ),
    extensions: (
      <>
        <rect x="4" y="4" width="6" height="6" rx="1.5" />
        <rect x="14" y="4" width="6" height="6" rx="1.5" />
        <rect x="4" y="14" width="6" height="6" rx="1.5" />
        <rect x="14" y="14" width="6" height="6" rx="1.5" />
      </>
    ),
    automations: (
      <>
        <circle cx="12" cy="12" r="7.5" />
        <path d="M12 8v4.5l3 1.8M7 3.8 4.8 6M17 3.8 19.2 6" />
      </>
    ),
    expand: <path d="m10 6 6 6-6 6" />,
    search: (
      <>
        <circle cx="10.5" cy="10.5" r="5.5" />
        <path d="m15 15 4 4" />
      </>
    ),
    plus: <path d="M12 5v14M5 12h14" />,
    more: <><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" /></>,
    sidebar: (
      <>
        <rect x="3.5" y="4.5" width="17" height="15" rx="4" />
        <path d="M9 4.5v15" />
      </>
    ),
    "sidebar-right": (
      <>
        <rect x="3.5" y="4.5" width="17" height="15" rx="4" />
        <path d="M15 4.5v15" />
      </>
    ),
  };
  return (
    <svg className="shell-icon" viewBox="0 0 24 24" data-icon={name} aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

interface CollapsedSidebarToolsProps {
  canCreateSession: boolean;
  onRevealSidebar: () => void;
  onNewSession: () => void;
  onSearchSessions: () => void;
}

/** Window-level navigation actions shown only while the context sidebar is hidden. */
export function CollapsedSidebarTools({
  canCreateSession,
  onRevealSidebar,
  onNewSession,
  onSearchSessions,
}: CollapsedSidebarToolsProps) {
  return (
    <nav className="collapsed-sidebar-tools" aria-label="Sidebar tools">
      <button
        className="quiet-icon-button collapsed-sidebar-tool"
        onClick={onRevealSidebar}
        aria-label="Open sidebar"
        title="Open sidebar (⌘B)"
      >
        <ShellIcon name="sidebar" />
      </button>
      <button
        className="quiet-icon-button collapsed-sidebar-tool"
        onClick={onNewSession}
        aria-label="New session"
        title="New session"
        disabled={!canCreateSession}
      >
        <ShellIcon name="plus" />
      </button>
      <button
        className="quiet-icon-button collapsed-sidebar-tool"
        onClick={onSearchSessions}
        aria-label="Search sessions"
        title="Search sessions (⌘K)"
      >
        <ShellIcon name="search" />
      </button>
    </nav>
  );
}

function compactAge(value: string): string {
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return "";
  }
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (minutes < 1) {
    return "now";
  }
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  if (days < 7) {
    return `${days}d`;
  }
  return new Date(timestamp).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function visibleSessionPreview(value: string): string {
  const preview = value.trim();
  return /^openppx session$/i.test(preview) ? "" : preview;
}

type SessionView = "active" | "archived";

function profileInitials(displayName: string): string {
  const parts = displayName.trim().split(/\s+/).filter(Boolean);
  if (parts.length > 1) {
    return `${Array.from(parts[0])[0] ?? ""}${Array.from(parts.at(-1) ?? "")[0] ?? ""}`.toUpperCase();
  }
  return Array.from(parts[0] ?? "?").slice(0, 2).join("").toUpperCase();
}

interface ContextSidebarProps {
  platform: DesktopPlatform;
  view: "chat" | "settings" | "automations";
  controlArea: "settings" | "extensions" | "automations" | null;
  runtime: RuntimeStatus;
  diagnostics: ClientDiagnostics | null;
  userProfile: UserProfile;
  agents: AgentProfile[];
  sessions: SessionSummary[];
  selectedAgentId: string;
  selectedSessionId: string;
  sendingSessionIds: string[];
  collapsed: boolean;
  searchFocusRequest: number;
  onToggleCollapse: () => void;
  onChangeView: (view: "chat" | "settings" | "automations") => void;
  onOpenSettings: () => void;
  onOpenExtensions: () => void;
  onOpenAutomations: () => void;
  onSelectAgent: (agentId: string) => void;
  onSelectSession: (session: SessionSummary) => void;
  onRenameSession: (session: SessionSummary, title: string) => void;
  onArchiveSession: (session: SessionSummary) => Promise<void>;
  onForkSession: (session: SessionSummary) => void;
  onExportSession: (session: SessionSummary) => void;
  onDeleteSession: (session: SessionSummary) => void;
  onNewAgent: () => void;
  onNewSession: () => void;
}

/** Left-side context navigator for Node, Agent, and Session selection. */
export function ContextSidebar({
  platform,
  view,
  controlArea,
  runtime,
  diagnostics,
  userProfile,
  agents,
  sessions,
  selectedAgentId,
  selectedSessionId,
  sendingSessionIds,
  collapsed,
  searchFocusRequest,
  onToggleCollapse,
  onChangeView,
  onOpenSettings,
  onOpenExtensions,
  onOpenAutomations,
  onSelectAgent,
  onSelectSession,
  onRenameSession,
  onArchiveSession,
  onForkSession,
  onExportSession,
  onDeleteSession,
  onNewAgent,
  onNewSession,
}: ContextSidebarProps) {
  const [query, setQuery] = useState("");
  const [agentMenuOpen, setAgentMenuOpen] = useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const [sessionMenuId, setSessionMenuId] = useState<string | null>(null);
  const [sessionView, setSessionView] = useState<SessionView>("active");
  const [sessionMutationError, setSessionMutationError] = useState<string | null>(null);
  const [mutatingSessionId, setMutatingSessionId] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const agentPickerRef = useRef<HTMLDivElement | null>(null);
  const agentTriggerRef = useRef<HTMLButtonElement | null>(null);
  const agentOptionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const profileMenuRef = useRef<HTMLDivElement | null>(null);
  const profileTriggerRef = useRef<HTMLButtonElement | null>(null);
  const filteredSessions = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const viewingArchived = sessionView === "archived";
    const sessionsInView = sessions.filter(
      (session) => Boolean(session.archived) === viewingArchived,
    );
    if (!normalized) {
      return sortSessionsByRecency(sessionsInView);
    }
    return sortSessionsByRecency(
      sessionsInView.filter((session) => (() => {
        const preview = visibleSessionPreview(session.lastMessagePreview);
        return `${session.title} ${preview}`.toLowerCase().includes(normalized);
      })()),
    );
  }, [query, sessions, sessionView]);
  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedAgentId) ?? null,
    [agents, selectedAgentId],
  );
  const nodeName = diagnostics?.nodeName ?? diagnostics?.target.name ?? runtime.target.name;
  const connectionMode = diagnostics?.mode === "lan" ? "LAN" : "LOCAL";

  async function updateSessionArchiveState(session: SessionSummary): Promise<void> {
    const action = session.archived ? "restore" : "archive";
    setSessionMenuId(null);
    setSessionMutationError(null);
    setMutatingSessionId(session.id);
    try {
      await onArchiveSession(session);
    } catch {
      setSessionMutationError(
        `Couldn’t ${action} ${session.title}. The session was not changed. Try again.`,
      );
    } finally {
      setMutatingSessionId(null);
    }
  }

  useEffect(() => {
    function handleSearchShortcut(event: KeyboardEvent): void {
      if (!collapsed && (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    }
    window.addEventListener("keydown", handleSearchShortcut);
    return () => window.removeEventListener("keydown", handleSearchShortcut);
  }, [collapsed]);

  useEffect(() => {
    if (collapsed) {
      setAgentMenuOpen(false);
      setProfileMenuOpen(false);
    }
  }, [collapsed]);

  useEffect(() => {
    if (!collapsed && searchFocusRequest > 0) {
      searchRef.current?.focus();
    }
  }, [collapsed, searchFocusRequest]);

  useEffect(() => {
    if (!agentMenuOpen) {
      return;
    }

    const selectedIndex = Math.max(0, agents.findIndex((agent) => agent.id === selectedAgentId));
    agentOptionRefs.current[selectedIndex]?.focus();

    function handlePointerDown(event: PointerEvent): void {
      if (!agentPickerRef.current?.contains(event.target as Node)) {
        setAgentMenuOpen(false);
      }
    }

    function handleEscape(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        setAgentMenuOpen(false);
        agentTriggerRef.current?.focus();
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [agentMenuOpen, agents, selectedAgentId]);

  useEffect(() => {
    if (!profileMenuOpen) {
      return;
    }

    function handlePointerDown(event: PointerEvent): void {
      if (!profileMenuRef.current?.contains(event.target as Node)) {
        setProfileMenuOpen(false);
      }
    }

    function handleEscape(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        setProfileMenuOpen(false);
        profileTriggerRef.current?.focus();
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [profileMenuOpen]);

  function selectAgent(agentId: string): void {
    setAgentMenuOpen(false);
    onSelectAgent(agentId);
    agentTriggerRef.current?.focus();
  }

  function handleAgentOptionKeyDown(event: ReactKeyboardEvent, index: number): void {
    let nextIndex = index;
    if (event.key === "ArrowDown") {
      nextIndex = (index + 1) % agents.length;
    } else if (event.key === "ArrowUp") {
      nextIndex = (index - 1 + agents.length) % agents.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = agents.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    agentOptionRefs.current[nextIndex]?.focus();
  }

  if (collapsed) {
    return null;
  }

  return (
    <aside className={`context-sidebar platform-${platform}`} aria-label={`${productProfile.displayName} navigation`}>
      <div className="sidebar-brand-row">
        <button
          className="quiet-icon-button"
          onClick={onToggleCollapse}
          aria-label="Collapse sidebar"
          title="Collapse sidebar (⌘B)"
        >
          <ShellIcon name="sidebar" />
        </button>
      </div>

      <button className="node-card" onClick={() => onChangeView("chat")}>
        <span className={`node-beacon ${runtime.state}`} />
        <span className="node-card-copy">
          <strong>{nodeName}</strong>
          <small>{connectionMode} · {runtimeStateLabel(runtime.state)}</small>
        </span>
        <span className="node-card-count">{agents.length}</span>
      </button>

      <section className="context-section agent-section" aria-label="Agent selector">
        <div className="context-section-heading">
          <span>Agent</span>
          <span className="context-heading-actions">
            <small>{agents.length}</small>
            <button className="section-add" onClick={onNewAgent} title="New Agent" aria-label="New Agent">
              <ShellIcon name="plus" />
            </button>
          </span>
        </div>
        <div className="agent-picker" ref={agentPickerRef}>
          <button
            ref={agentTriggerRef}
            className="agent-picker-trigger"
            type="button"
            aria-haspopup="listbox"
            aria-expanded={agentMenuOpen}
            disabled={agents.length === 0}
            onClick={() => setAgentMenuOpen((current) => !current)}
            onKeyDown={(event) => {
              if (!agentMenuOpen && (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ")) {
                event.preventDefault();
                setAgentMenuOpen(true);
              }
            }}
          >
            <span className="agent-picker-copy">
              <strong>{selectedAgent?.name ?? "No agents available"}</strong>
              <small>{selectedAgent?.description ?? "Connect a Node to continue"}</small>
            </span>
            {selectedAgent ? <span className={`agent-state ${selectedAgent.status}`} /> : null}
            <span className={agentMenuOpen ? "agent-picker-chevron open" : "agent-picker-chevron"}>
              <ShellIcon name="expand" />
            </span>
          </button>

          {agentMenuOpen ? (
            <div className="agent-picker-menu" role="listbox" aria-label="Select Agent">
              {agents.map((agent, index) => (
                <button
                  key={agent.id}
                  ref={(element) => {
                    agentOptionRefs.current[index] = element;
                  }}
                  className={agent.id === selectedAgentId ? "agent-option selected" : "agent-option"}
                  type="button"
                  role="option"
                  aria-selected={agent.id === selectedAgentId}
                  onClick={() => selectAgent(agent.id)}
                  onKeyDown={(event) => handleAgentOptionKeyDown(event, index)}
                >
                  <span className="agent-option-marker">
                    <span className={`agent-state ${agent.status}`} />
                  </span>
                  <span className="agent-option-copy">
                    <strong>{agent.name}</strong>
                    <small>{agent.description}</small>
                  </span>
                  {agent.id === selectedAgentId ? <span className="agent-option-check">✓</span> : null}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </section>

      <section className="context-section session-section">
        <div className="context-section-heading">
          <span>Sessions</span>
          <span className="context-heading-actions">
            <span className="session-view-toggle" role="group" aria-label="Session view">
              <button
                type="button"
                className={sessionView === "active" ? "section-filter active" : "section-filter"}
                aria-label="Active sessions"
                aria-pressed={sessionView === "active"}
                title="Show active sessions"
                onClick={() => {
                  setSessionView("active");
                  setSessionMutationError(null);
                }}
              >
                Active
              </button>
              <button
                type="button"
                className={sessionView === "archived" ? "section-filter active" : "section-filter"}
                aria-label="Archived sessions"
                aria-pressed={sessionView === "archived"}
                title="Show archived sessions"
                onClick={() => {
                  setSessionView("archived");
                  setSessionMutationError(null);
                }}
              >
                Archived
              </button>
            </span>
            <button className="section-add" onClick={onNewSession} disabled={!selectedAgentId} title="New session"><ShellIcon name="plus" /></button>
          </span>
        </div>
        <label className="session-search">
          <ShellIcon name="search" />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search sessions"
          />
          <kbd>⌘K</kbd>
        </label>
        {sessionMutationError ? (
          <p className="session-mutation-error" role="alert">{sessionMutationError}</p>
        ) : null}
        <div className="session-list">
          {filteredSessions.map((session) => {
            const running = sendingSessionIds.includes(session.id);
            const preview = visibleSessionPreview(session.lastMessagePreview);
            return (
              <div key={session.id} className={`session-row-shell ${sessionMenuId === session.id ? "menu-open" : ""}`}>
              <button
                className={`session-row ${preview ? "with-preview" : "compact"} ${
                  session.id === selectedSessionId ? "active" : ""
                }`}
                onClick={() => onSelectSession(session)}
              >
                <span className="session-row-title">
                  <strong>{session.title}</strong>
                  {!preview ? (
                    <span className="session-row-trailing">
                      {running ? <span className="session-live-dot" /> : null}
                      <time>{compactAge(session.updatedAt)}</time>
                    </span>
                  ) : running ? <span className="session-live-dot" /> : null}
                </span>
                {preview ? (
                  <span className="session-row-meta">
                    <span>{preview}</span>
                    <time>{compactAge(session.updatedAt)}</time>
                  </span>
                ) : null}
              </button>
              <button
                className="session-more"
                aria-label="Session actions"
                title={`Actions for ${session.title}`}
                disabled={mutatingSessionId === session.id}
                onClick={() => setSessionMenuId((value) => value === session.id ? null : session.id)}
              >
                <ShellIcon name="more" />
              </button>
              {sessionMenuId === session.id ? <div className="session-action-menu" role="menu">
                <button onClick={() => { const title = window.prompt("Rename session", session.title)?.trim(); setSessionMenuId(null); if (title && title !== session.title) onRenameSession(session, title); }}>Rename</button>
                <button onClick={() => void updateSessionArchiveState(session)}>{session.archived ? "Restore" : "Archive"}</button>
                <button onClick={() => { setSessionMenuId(null); onForkSession(session); }}>Duplicate</button>
                <button onClick={() => { setSessionMenuId(null); onExportSession(session); }}>Export JSON</button>
                <button className="danger" onClick={() => { setSessionMenuId(null); if (window.confirm(`Delete ${session.title}? Its conversation and Session files will be permanently removed.`)) onDeleteSession(session); }}>Delete</button>
              </div> : null}
              </div>
            );
          })}
          {filteredSessions.length === 0 ? (
            <p className="sidebar-empty">
              {query
                ? "No matching sessions."
                : sessionView === "archived"
                  ? "No archived sessions."
                  : "Create a session to get started."}
            </p>
          ) : null}
        </div>
      </section>

      <div className="sidebar-footer">
        <div className="profile-menu-anchor" ref={profileMenuRef}>
          {profileMenuOpen ? (
            <div className="profile-menu" role="menu" aria-label="User menu">
              <div className="profile-menu-identity">
                <span className="profile-avatar" aria-hidden="true">
                  {profileInitials(userProfile.displayName)}
                </span>
                <span className="profile-menu-copy">
                  <strong>{userProfile.displayName}</strong>
                  <small>{userProfile.accountKind === "local" ? "Local account" : `${productProfile.displayName} account`}</small>
                </span>
              </div>
              <div className="profile-menu-divider" />
              <button
                className={view === "automations" ? "profile-menu-item active" : "profile-menu-item"}
                type="button"
                role="menuitem"
                onClick={() => {
                  setProfileMenuOpen(false);
                  onOpenAutomations();
                }}
              >
                <ShellIcon name="automations" />
                <span>Automations</span>
              </button>
              <button
                className={view === "settings" && controlArea === "extensions" ? "profile-menu-item active" : "profile-menu-item"}
                type="button"
                role="menuitem"
                onClick={() => {
                  setProfileMenuOpen(false);
                  onOpenExtensions();
                }}
              >
                <ShellIcon name="extensions" />
                <span>Extensions</span>
              </button>
              <div className="profile-menu-divider profile-menu-settings-divider" role="separator" />
              <button
                className={view === "settings" && controlArea === "settings" ? "profile-menu-item active" : "profile-menu-item"}
                type="button"
                role="menuitem"
                onClick={() => {
                  setProfileMenuOpen(false);
                  onOpenSettings();
                }}
              >
                <ShellIcon name="settings" />
                <span>Settings</span>
              </button>
            </div>
          ) : null}
          <button
            ref={profileTriggerRef}
            className={view === "settings" ? "profile-trigger active" : "profile-trigger"}
            type="button"
            aria-label="User profile"
            aria-haspopup="menu"
            aria-expanded={profileMenuOpen}
            onClick={() => setProfileMenuOpen((current) => !current)}
          >
            <span className="profile-avatar" aria-hidden="true">
              {profileInitials(userProfile.displayName)}
            </span>
            <span className="profile-trigger-name">{userProfile.displayName}</span>
            <span className={profileMenuOpen ? "profile-trigger-chevron open" : "profile-trigger-chevron"}>
              <ShellIcon name="expand" />
            </span>
          </button>
        </div>
      </div>
    </aside>
  );
}
