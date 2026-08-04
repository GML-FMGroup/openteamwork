import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import type {
  AgentProfile,
  ClientDiagnostics,
  RuntimeStatus,
  SessionSummary,
} from "../../types";

type ShellIconName = "chat" | "settings" | "expand" | "search" | "plus" | "sidebar" | "sidebar-right";

export function ShellIcon({ name }: { name: ShellIconName }) {
  const paths: Record<ShellIconName, ReactNode> = {
    chat: <path d="M4 5.5h16v11H9l-5 3v-14Z" />,
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1" />
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

interface ContextSidebarProps {
  view: "chat" | "settings";
  runtime: RuntimeStatus;
  diagnostics: ClientDiagnostics | null;
  agents: AgentProfile[];
  sessions: SessionSummary[];
  selectedAgentId: string;
  selectedSessionId: string;
  sendingSessionIds: string[];
  collapsed: boolean;
  searchFocusRequest: number;
  onToggleCollapse: () => void;
  onChangeView: (view: "chat" | "settings") => void;
  onSelectAgent: (agentId: string) => void;
  onSelectSession: (session: SessionSummary) => void;
  onNewAgent: () => void;
  onNewSession: () => void;
}

/** Left-side context navigator for Node, Agent, and Session selection. */
export function ContextSidebar({
  view,
  runtime,
  diagnostics,
  agents,
  sessions,
  selectedAgentId,
  selectedSessionId,
  sendingSessionIds,
  collapsed,
  searchFocusRequest,
  onToggleCollapse,
  onChangeView,
  onSelectAgent,
  onSelectSession,
  onNewAgent,
  onNewSession,
}: ContextSidebarProps) {
  const [query, setQuery] = useState("");
  const [agentMenuOpen, setAgentMenuOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const agentPickerRef = useRef<HTMLDivElement | null>(null);
  const agentTriggerRef = useRef<HTMLButtonElement | null>(null);
  const agentOptionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const filteredSessions = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) {
      return sessions;
    }
    return sessions.filter((session) => {
      const preview = visibleSessionPreview(session.lastMessagePreview);
      return `${session.title} ${preview}`.toLowerCase().includes(normalized);
    });
  }, [query, sessions]);
  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedAgentId) ?? null,
    [agents, selectedAgentId],
  );
  const nodeName = diagnostics?.nodeName ?? diagnostics?.target.name ?? runtime.target.name;
  const connectionMode = diagnostics?.mode === "lan" ? "LAN" : "LOCAL";

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
    <aside className="context-sidebar" aria-label="OpenPPX navigation">
      <div className="sidebar-brand-row">
        <button
          className="brand-lockup"
          onClick={() => onChangeView("chat")}
          aria-label="OpenPPX workspace"
        >
          <span className="brand-mark">P</span>
          <strong>OpenPPX</strong>
        </button>
        <button
          className="quiet-icon-button"
          onClick={onToggleCollapse}
          aria-label="Collapse sidebar"
          title="Collapse sidebar (⌘B)"
        >
          <ShellIcon name="sidebar" />
        </button>
      </div>

      <button className="node-card" onClick={() => onChangeView("settings")}>
        <span className={`node-beacon ${runtime.state}`} />
        <span className="node-card-copy">
          <strong>{nodeName}</strong>
          <small>{connectionMode} · {runtime.state}</small>
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
          <button className="section-add" onClick={onNewSession} disabled={!selectedAgentId} title="New session">
            <ShellIcon name="plus" />
          </button>
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
        <div className="session-list">
          {filteredSessions.map((session) => {
            const running = sendingSessionIds.includes(session.id);
            const preview = visibleSessionPreview(session.lastMessagePreview);
            return (
              <button
                key={session.id}
                className={session.id === selectedSessionId ? "session-row active" : "session-row"}
                onClick={() => onSelectSession(session)}
              >
                <span className="session-row-title">
                  <strong>{session.title}</strong>
                  {running ? <span className="session-live-dot" /> : null}
                </span>
                <span className="session-row-meta">
                  {preview ? <span>{preview}</span> : null}
                  <time>{compactAge(session.updatedAt)}</time>
                </span>
              </button>
            );
          })}
          {filteredSessions.length === 0 ? (
            <p className="sidebar-empty">
              {query ? "No matching sessions." : "Create a session to get started."}
            </p>
          ) : null}
        </div>
      </section>

      <nav className="sidebar-footer" aria-label="Primary">
        <button
          className={view === "chat" ? "footer-nav active" : "footer-nav"}
          onClick={() => onChangeView("chat")}
        >
          <ShellIcon name="chat" />
          <span>Workspace</span>
        </button>
        <button
          className={view === "settings" ? "footer-nav active" : "footer-nav"}
          onClick={() => onChangeView("settings")}
        >
          <ShellIcon name="settings" />
          <span>Settings</span>
        </button>
      </nav>
    </aside>
  );
}
