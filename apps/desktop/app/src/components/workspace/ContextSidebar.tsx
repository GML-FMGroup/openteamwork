import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type {
  AgentProfile,
  ClientDiagnostics,
  RuntimeStatus,
  SessionSummary,
} from "../../types";

type ShellIconName = "chat" | "settings" | "collapse" | "expand" | "search" | "plus";

function ShellIcon({ name }: { name: ShellIconName }) {
  const paths: Record<ShellIconName, ReactNode> = {
    chat: <path d="M4 5.5h16v11H9l-5 3v-14Z" />,
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1" />
      </>
    ),
    collapse: <path d="m14 6-6 6 6 6" />,
    expand: <path d="m10 6 6 6-6 6" />,
    search: (
      <>
        <circle cx="10.5" cy="10.5" r="5.5" />
        <path d="m15 15 4 4" />
      </>
    ),
    plus: <path d="M12 5v14M5 12h14" />,
  };
  return (
    <svg className="shell-icon" viewBox="0 0 24 24" aria-hidden="true">
      {paths[name]}
    </svg>
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
  return new Date(timestamp).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
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
  onToggleCollapse: () => void;
  onChangeView: (view: "chat" | "settings") => void;
  onSelectAgent: (agentId: string) => void;
  onSelectSession: (session: SessionSummary) => void;
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
  onToggleCollapse,
  onChangeView,
  onSelectAgent,
  onSelectSession,
  onNewSession,
}: ContextSidebarProps) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement | null>(null);
  const filteredSessions = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) {
      return sessions;
    }
    return sessions.filter((session) =>
      `${session.title} ${session.lastMessagePreview}`.toLowerCase().includes(normalized),
    );
  }, [query, sessions]);
  const nodeName = diagnostics?.nodeName ?? diagnostics?.target.name ?? runtime.target.name;
  const connectionMode = diagnostics?.mode === "lan" ? "LAN" : diagnostics?.mode === "mock" ? "DEMO" : "LOCAL";

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

  if (collapsed) {
    return (
      <aside className="context-sidebar collapsed" aria-label="OpenPPX navigation">
        <button className="brand-mark compact" onClick={onToggleCollapse} title="展开侧栏 (⌘B)">
          P
        </button>
        <div className="collapsed-nav">
          <button
            className={view === "chat" ? "rail-action active" : "rail-action"}
            onClick={() => onChangeView("chat")}
            title="工作区"
          >
            <ShellIcon name="chat" />
          </button>
          <button
            className={view === "settings" ? "rail-action active" : "rail-action"}
            onClick={() => onChangeView("settings")}
            title="连接与设置"
          >
            <ShellIcon name="settings" />
          </button>
        </div>
        <span
          className={`node-beacon ${runtime.state}`}
          title={`${nodeName} · ${runtime.state}`}
        />
        <button
          className="rail-action sidebar-toggle"
          onClick={onToggleCollapse}
          title="展开侧栏 (⌘B)"
        >
          <ShellIcon name="expand" />
        </button>
      </aside>
    );
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
          <span>
            <strong>OpenPPX</strong>
            <small>Agent workspace</small>
          </span>
        </button>
        <button className="quiet-icon-button" onClick={onToggleCollapse} title="折叠侧栏 (⌘B)">
          <ShellIcon name="collapse" />
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

      <section className="context-section agent-section">
        <div className="context-section-heading">
          <span>Agents</span>
          <small>{agents.length}</small>
        </div>
        <div className="agent-list">
          {agents.map((agent) => (
            <button
              key={agent.id}
              className={agent.id === selectedAgentId ? "agent-row active" : "agent-row"}
              onClick={() => onSelectAgent(agent.id)}
            >
              <span className="agent-monogram">{agent.name.slice(0, 1).toUpperCase()}</span>
              <span className="agent-row-copy">
                <strong>{agent.name}</strong>
                <small>{agent.description}</small>
              </span>
              <span className={`agent-state ${agent.status}`} />
            </button>
          ))}
          {agents.length === 0 ? <p className="sidebar-empty">当前 Node 没有可用 Agent。</p> : null}
        </div>
      </section>

      <section className="context-section session-section">
        <div className="context-section-heading">
          <span>Sessions</span>
          <button className="section-add" onClick={onNewSession} disabled={!selectedAgentId} title="新建 Session">
            <ShellIcon name="plus" />
          </button>
        </div>
        <label className="session-search">
          <ShellIcon name="search" />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索 Session"
          />
          <kbd>⌘K</kbd>
        </label>
        <div className="session-list">
          {filteredSessions.map((session) => {
            const running = sendingSessionIds.includes(session.id);
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
                  <span>{session.lastMessagePreview || "尚无消息"}</span>
                  <time>{compactAge(session.updatedAt)}</time>
                </span>
              </button>
            );
          })}
          {filteredSessions.length === 0 ? (
            <p className="sidebar-empty">
              {query ? "没有匹配的 Session。" : "新建一个 Session 开始工作。"}
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
          <span>工作区</span>
        </button>
        <button
          className={view === "settings" ? "footer-nav active" : "footer-nav"}
          onClick={() => onChangeView("settings")}
        >
          <ShellIcon name="settings" />
          <span>连接与设置</span>
        </button>
      </nav>
    </aside>
  );
}
