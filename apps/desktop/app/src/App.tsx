import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AgentProfile,
  BootstrapPayload,
  ChatMessage,
  ClientDiagnostics,
  ConnectionSettings,
  RuntimeState,
  RuntimeStatus,
  SessionSummary,
} from "./types";
import { MessageBubble } from "./components/MessageBubble";
import { normalizeConnectionSettings } from "./lib/connection-profile";

type NavView = "chat" | "settings";

function mergeMessages(current: ChatMessage[], incoming: ChatMessage): ChatMessage[] {
  return current.some((item) => item.id === incoming.id) ? current : [...current, incoming];
}

function updateMessage(
  current: ChatMessage[],
  messageId: string,
  updater: (message: ChatMessage) => ChatMessage,
): ChatMessage[] {
  return current.map((message) => (message.id === messageId ? updater(message) : message));
}

function removeSendingSession(current: string[], sessionId: string): string[] {
  return current.filter((item) => item !== sessionId);
}

function compactSessionTitle(text: string): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= 64) {
    return normalized;
  }
  return `${normalized.slice(0, 61).trimEnd()}...`;
}

function isGenericSessionTitle(title: string): boolean {
  const normalized = title.trim();
  return (
    !normalized ||
    normalized === "New local session" ||
    normalized === "New chat" ||
    normalized === "新对话" ||
    normalized.startsWith("Session ")
  );
}

function mergeSessionSummary(existing: SessionSummary | undefined, incoming: SessionSummary): SessionSummary {
  if (!existing) {
    return incoming;
  }
  if (isGenericSessionTitle(incoming.title) && !isGenericSessionTitle(existing.title)) {
    return { ...incoming, title: existing.title };
  }
  return incoming;
}

function runtimeActionLabel(state: RuntimeState): string {
  if (state === "stopped") {
    return "启动";
  }
  if (state === "healthy") {
    return "重启";
  }
  return "重试";
}

function buildConnectionSettings(diagnostics: ClientDiagnostics | null): ConnectionSettings {
  return {
    targetType: diagnostics?.mode === "lan" ? "lan" : "local",
    targetId: diagnostics?.target.id ?? "local-default",
    targetName: diagnostics?.target.name ?? "This Mac",
    clientApiBaseUrl: diagnostics?.clientApiBaseUrl ?? "http://127.0.0.1:8765",
    accessToken: "",
  };
}

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
  const [view, setView] = useState<NavView>("chat");
  const [ready, setReady] = useState(false);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [diagnostics, setDiagnostics] = useState<ClientDiagnostics | null>(null);
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [composer, setComposer] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const [sendingSessionIds, setSendingSessionIds] = useState<string[]>([]);
  const [connectionForm, setConnectionForm] = useState<ConnectionSettings>(buildConnectionSettings(null));
  const [savingConnection, setSavingConnection] = useState(false);
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionFeedback, setConnectionFeedback] = useState<string | null>(null);
  const switchRequestIdRef = useRef(0);
  const agentsDropdownRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const messageStreamRef = useRef<HTMLElement | null>(null);
  const nextScrollBehaviorRef = useRef<ScrollBehavior>("auto");

  useEffect(() => {
    if (!window.ppxClient) {
      setBootstrapError("Preload host API was not injected. Check Electron preload output and restart dev.");
      return;
    }
    let mounted = true;
    const off = window.ppxClient.onRunEvent((event) => {
      if (event.type === "message.created") {
        setMessages((current) => mergeMessages(current, event.message));
      } else if (event.type === "message.updated") {
        setMessages((current) =>
          updateMessage(current, event.messageId, (message) => ({
            ...message,
            status: event.status ?? message.status,
            parts: event.replaceParts ?? [...message.parts, ...(event.appendParts ?? [])],
          })),
        );
        if (event.status === "completed" || event.status === "failed" || event.status === "cancelled") {
          setSendingSessionIds((current) => removeSendingSession(current, event.sessionId));
        }
      } else if (event.type === "session.updated") {
        setSessions((current) =>
          [
            mergeSessionSummary(
              current.find((item) => item.id === event.session.id),
              event.session,
            ),
            ...current.filter((item) => item.id !== event.session.id),
          ].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt)),
        );
      } else if (event.type === "run.finished") {
        setSendingSessionIds((current) => removeSendingSession(current, event.sessionId));
      }
    });

    window.ppxClient
      .bootstrap()
      .then(async (payload: BootstrapPayload) => {
        if (!mounted) {
          return;
        }
        let nextSessions = payload.sessions;
        let nextSelectedSessionId = payload.selectedSessionId;
        let nextMessages = payload.messages;
        if (payload.selectedAgentId && !nextSelectedSessionId) {
          const created = await window.ppxClient.createSession(payload.selectedAgentId);
          nextSessions = [created.session, ...nextSessions.filter((session) => session.id !== created.session.id)];
          nextSelectedSessionId = created.session.id;
          nextMessages = [];
        }
        if (!mounted) {
          return;
        }
        setRuntime(payload.runtime);
        setAgents(payload.agents);
        setSessions(nextSessions);
        nextScrollBehaviorRef.current = "auto";
        setMessages(nextMessages);
        setSelectedAgentId(payload.selectedAgentId);
        setSelectedSessionId(nextSelectedSessionId);
        setReady(true);
        setBootstrapError(null);
        void window.ppxClient
          .getDiagnostics()
          .then((nextDiagnostics) => {
            setDiagnostics(nextDiagnostics);
            setConnectionForm(buildConnectionSettings(nextDiagnostics));
          })
          .catch(() => undefined);
      })
      .catch((error: unknown) => {
        if (!mounted) {
          return;
        }
        setBootstrapError(error instanceof Error ? error.message : String(error));
      });

    return () => {
      mounted = false;
      off();
    };
  }, []);

  useEffect(() => {
    setConnectionForm(buildConnectionSettings(diagnostics));
  }, [diagnostics]);

  useEffect(() => {
    resizeComposer(composerRef.current);
  }, [composer]);

  useEffect(() => {
    if (!agentsOpen) {
      return;
    }
    function handlePointerDown(event: PointerEvent): void {
      const dropdown = agentsDropdownRef.current;
      if (!dropdown || dropdown.contains(event.target as Node)) {
        return;
      }
      setAgentsOpen(false);
    }
    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [agentsOpen]);

  useEffect(() => {
    const stream = messageStreamRef.current;
    if (!stream) {
      return;
    }
    const behavior = nextScrollBehaviorRef.current;
    nextScrollBehaviorRef.current = "smooth";
    if (typeof stream.scrollTo === "function") {
      stream.scrollTo({
        top: stream.scrollHeight,
        behavior,
      });
      return;
    }
    stream.scrollTop = stream.scrollHeight;
  }, [messages]);

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedAgentId) ?? null,
    [agents, selectedAgentId],
  );

  const selectedSession = useMemo(
    () => sessions.find((session) => session.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId],
  );

  const currentSessionSending = useMemo(
    () => (selectedSessionId ? sendingSessionIds.includes(selectedSessionId) : false),
    [selectedSessionId, sendingSessionIds],
  );
  const selectedAgentBusy = useMemo(
    () =>
      Boolean(selectedAgentId) &&
      ((selectedSessionId && sendingSessionIds.includes(selectedSessionId)) ||
        sessions.some((session) => session.agentId === selectedAgentId && sendingSessionIds.includes(session.id))),
    [selectedAgentId, selectedSessionId, sendingSessionIds, sessions],
  );
  const canSend = Boolean(composer.trim()) && Boolean(selectedAgentId) && !selectedAgentBusy;
  const titlebarTitle =
    view === "chat" ? selectedSession?.title ?? selectedAgent?.name ?? "No session" : "Settings";
  const titlebarSubtitle = view === "chat" ? selectedAgent?.name ?? "No agent selected" : "ppx-client";

  async function switchAgent(agentId: string): Promise<void> {
    const requestId = ++switchRequestIdRef.current;
    setSendError(null);
    setSelectedAgentId(agentId);
    setSelectedSessionId("");
    setSessions([]);
    setMessages([]);
    const listed = await window.ppxClient.listSessions(agentId);
    if (requestId !== switchRequestIdRef.current) {
      return;
    }
    setSessions(listed.sessions);
    if (listed.sessions[0]) {
      const nextSessionId = listed.sessions[0].id;
      setSelectedSessionId(nextSessionId);
      const loaded = await window.ppxClient.loadSession(nextSessionId);
      if (requestId !== switchRequestIdRef.current) {
        return;
      }
      nextScrollBehaviorRef.current = "auto";
      setMessages(loaded.messages);
      return;
    }
    const created = await window.ppxClient.createSession(agentId);
    if (requestId !== switchRequestIdRef.current) {
      return;
    }
    setSessions([created.session]);
    setSelectedSessionId(created.session.id);
    setMessages([]);
  }

  function handleAgentToggle(): void {
    setAgentsOpen((current) => !current);
  }

  function handleAgentSelect(agentId: string): void {
    setAgentsOpen(false);
    if (agentId === selectedAgentId) {
      return;
    }
    void switchAgent(agentId);
  }

  async function switchSession(session: SessionSummary): Promise<void> {
    const requestId = ++switchRequestIdRef.current;
    setSendError(null);
    setSelectedAgentId(session.agentId);
    setSelectedSessionId(session.id);
    setMessages([]);
    const loaded = await window.ppxClient.loadSession(session.id);
    if (requestId !== switchRequestIdRef.current) {
      return;
    }
    nextScrollBehaviorRef.current = "auto";
    setMessages(loaded.messages);
  }

  async function handleRuntimeAction(): Promise<void> {
    if (!runtime) {
      return;
    }
    const command = runtime.state === "stopped" ? "start" : "restart";
    const next = await window.ppxClient.runRuntimeCommand(command);
    setRuntime(next);
    const nextDiagnostics = await window.ppxClient.getDiagnostics();
    setDiagnostics(nextDiagnostics);
  }

  async function refreshDiagnostics(): Promise<void> {
    const nextDiagnostics = await window.ppxClient.getDiagnostics();
    setDiagnostics(nextDiagnostics);
  }

  async function handleConnectionSave(): Promise<void> {
    setSavingConnection(true);
    setConnectionFeedback(null);
    try {
      const nextSettings = normalizeConnectionSettings(connectionForm);
      const nextDiagnostics = await window.ppxClient.saveConnectionSettings(nextSettings);
      setDiagnostics(nextDiagnostics);
      setConnectionForm(buildConnectionSettings(nextDiagnostics));
      if (nextSettings.targetType === "local") {
        await window.ppxClient.runRuntimeCommand("restart");
      }
      try {
        const payload = await window.ppxClient.bootstrap();
        setRuntime(payload.runtime);
        setAgents(payload.agents);
        setSessions(payload.sessions);
        setMessages(payload.messages);
        setSelectedAgentId(payload.selectedAgentId);
        setSelectedSessionId(payload.selectedSessionId);
      } catch {
        setAgents([]);
        setSessions([]);
        setMessages([]);
        setSelectedAgentId("");
        setSelectedSessionId("");
      }
      setConnectionFeedback(`已连接 ${nextDiagnostics.nodeName ?? nextDiagnostics.target.name}。`);
    } catch (error) {
      setConnectionFeedback(error instanceof Error ? error.message : String(error));
    } finally {
      setSavingConnection(false);
    }
  }

  async function handleConnectionTest(): Promise<void> {
    setTestingConnection(true);
    setConnectionFeedback(null);
    try {
      const nextSettings = normalizeConnectionSettings(connectionForm);
      const nextDiagnostics = await window.ppxClient.testConnectionSettings(nextSettings);
      setConnectionFeedback(
        `连接成功：${nextDiagnostics.nodeName ?? nextDiagnostics.target.name} · ${nextDiagnostics.clientApiProductVersion ?? "unknown"}`,
      );
    } catch (error) {
      setConnectionFeedback(error instanceof Error ? error.message : String(error));
    } finally {
      setTestingConnection(false);
    }
  }

  async function handleNewSession(): Promise<void> {
    if (!selectedAgentId) {
      return;
    }
    setSendError(null);
    const created = await window.ppxClient.createSession(selectedAgentId);
    setSessions((current) => [created.session, ...current.filter((item) => item.id !== created.session.id)]);
    setSelectedSessionId(created.session.id);
    setMessages([]);
  }

  async function ensureActiveSession(agentId: string, preferredSessionId: string): Promise<SessionSummary> {
    const existing = sessions.find((session) => session.id === preferredSessionId && session.agentId === agentId);
    if (existing) {
      return existing;
    }
    const listed = await window.ppxClient.listSessions(agentId);
    const firstSession = listed.sessions[0];
    if (firstSession) {
      setSessions(listed.sessions);
      setSelectedSessionId(firstSession.id);
      if (firstSession.id !== preferredSessionId) {
        const loaded = await window.ppxClient.loadSession(firstSession.id);
        nextScrollBehaviorRef.current = "auto";
        setMessages(loaded.messages);
      }
      return firstSession;
    }
    const created = await window.ppxClient.createSession(agentId);
    setSessions((current) => [created.session, ...current.filter((item) => item.id !== created.session.id)]);
    setSelectedSessionId(created.session.id);
    setMessages([]);
    return created.session;
  }

  function applyFirstUserTitle(sessionId: string, text: string, timestamp: string): void {
    const title = compactSessionTitle(text);
    if (!title) {
      return;
    }
    setSessions((current) =>
      current.map((session) =>
        session.id === sessionId && isGenericSessionTitle(session.title)
          ? {
              ...session,
              title,
              updatedAt: timestamp,
            }
          : session,
      ),
    );
  }

  async function handleSend(): Promise<void> {
    const text = composer.trim();
    if (!text || !selectedAgentId) {
      return;
    }
    setSendError(null);
    let session: SessionSummary;
    try {
      session = await ensureActiveSession(selectedAgentId, selectedSessionId);
    } catch (error) {
      setSendError(error instanceof Error ? error.message : String(error));
      return;
    }
    const sessionId = session.id;
    setSendingSessionIds((current) => (current.includes(sessionId) ? current : [...current, sessionId]));
    setComposer("");
    const optimisticMessage: ChatMessage = {
      id: `local-user-${crypto.randomUUID()}`,
      sessionId,
      role: "user",
      status: "completed",
      createdAt: new Date().toISOString(),
      parts: [{ type: "markdown", text }],
    };
    applyFirstUserTitle(sessionId, text, optimisticMessage.createdAt);
    setMessages((current) => [...current, optimisticMessage]);
    try {
      await window.ppxClient.sendMessage({
        agentId: selectedAgentId,
        sessionId,
        text,
      });
    } catch (error) {
      console.error("Failed to send message", error);
      setSendError(error instanceof Error ? error.message : String(error));
    } finally {
      setSendingSessionIds((current) => current.filter((item) => item !== sessionId));
    }
  }

  function handleComposerKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    event.preventDefault();
    if (selectedAgentBusy || !composer.trim()) {
      return;
    }
    void handleSend();
  }

  if (bootstrapError) {
    return (
      <div className="loading-shell">
        <div>
          <strong>ppx-client failed to initialize</strong>
          <p>{bootstrapError}</p>
        </div>
      </div>
    );
  }

  if (!ready || !runtime) {
    return <div className="loading-shell">Loading ppx-client...</div>;
  }

  return (
    <div className="app-shell">
      <section className="nav-shell">
        <aside className="nav-rail">
          <button className={view === "chat" ? "nav-item active" : "nav-item"} onClick={() => setView("chat")} title="对话">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
            </svg>
            {/* <span className="nav-label">对话</span> */}
          </button>
          <button className={view === "settings" ? "nav-item active" : "nav-item"} onClick={() => setView("settings")} title="设置">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            {/* <span className="nav-label">设置</span> */}
          </button>
        </aside>
      </section>

      {view === "chat" ? (
        <>
          <section className="sidebar-shell">
            <section className="sidebar">
              <div className="sidebar-section">
                <div className="sidebar-section-header">
                  <span>Agents</span>
                </div>
                <div ref={agentsDropdownRef} className={agentsOpen ? "agents-container open" : "agents-container"}>
                  <button
                    type="button"
                    className={agentsOpen ? "list-item active agents-dropdown-trigger" : "list-item agents-dropdown-trigger"}
                    aria-expanded={agentsOpen}
                    aria-controls="agents-dropdown-list"
                    aria-label="Toggle agents list"
                    onClick={handleAgentToggle}
                  >
                    <div className="agent-trigger-copy">
                      <span className="agent-trigger-name-row">
                        <strong>{selectedAgent?.name ?? "No agent selected"}</strong>
                        {selectedAgent ? <span className={`agent-status-dot ${selectedAgent.status}`} aria-hidden="true" /> : null}
                      </span>
                    </div>
                    <span className={agentsOpen ? "agents-chevron expanded" : "agents-chevron"} aria-hidden="true">
                      <svg viewBox="0 0 20 20">
                        <path d="M5.25 7.5 10 12.25 14.75 7.5" />
                      </svg>
                    </span>
                  </button>
                  {agentsOpen ? (
                    <div id="agents-dropdown-list" className="list-stack agents-dropdown-list">
                      {agents.map((agent) => (
                        <button
                          key={agent.id}
                          className={agent.id === selectedAgentId ? "list-item active" : "list-item"}
                          onClick={() => handleAgentSelect(agent.id)}
                        >
                          <div>
                            <strong>{agent.name}</strong>
                            {/* <p>{agent.description}</p> */}
                          </div>
                          <span className={`agent-status-text ${agent.status}`}>{agent.status}</span>
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="sidebar-divider" />

              <div className="sidebar-section">
                <div className="sidebar-section-header">
                  <span>Sessions</span>
                  {selectedAgentId ? (
                    <button className="secondary small" onClick={() => void handleNewSession()}>
                      新建
                    </button>
                  ) : null}
                </div>
                <div className="list-stack">
                  {sessions.map((session) => (
                    <button
                      key={session.id}
                      className={session.id === selectedSessionId ? "list-item active" : "list-item"}
                      onClick={() => void switchSession(session)}
                    >
                      <div>
                        <strong>{session.title}</strong>
                      </div>
                      <time>{new Date(session.updatedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time>
                    </button>
                  ))}
                </div>
              </div>
            </section>
          </section>

          <section className="workspace-shell">
            <header className="column-topbar workspace-topbar">
              <div className="topbar-copy">
                <strong>{titlebarTitle}</strong>
                <span>{titlebarSubtitle}</span>
              </div>
              <div className="topbar-actions">
                <button className="topbar-pill" onClick={() => setView("settings")} title="查看 runtime 状态">
                  <span className={`runtime-dot ${runtime.state}`} />
                  {runtime.state}
                </button>
              </div>
            </header>
            <div className="workspace-frame">
              <main className="workspace">
                <section ref={messageStreamRef} className="message-stream">
                  {messages.length === 0 ? (
                    <div className="empty-state">
                      <h3>{selectedAgent?.name ?? "Agent"} is ready</h3>
                      <p>从一个本地任务开始。比如：帮我规划一个 client-api，或为当前仓库总结结构。</p>
                      <div className="suggestion-grid">
                        {[
                          "为 ppx-client 设计本地 runtime 对接层",
                          "帮我列出当前 agent 机器的会话模型",
                          "把聊天消息渲染做成接近飞书的风格",
                        ].map((suggestion) => (
                          <button key={suggestion} className="suggestion-card" onClick={() => setComposer(suggestion)}>
                            {suggestion}
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    messages.map((message, index) => {
                      const previousMessage = index > 0 ? messages[index - 1] : null;
                      const showIdentity = !(message.role === "assistant" && previousMessage?.role === "assistant");
                      return <MessageBubble key={message.id} message={message} showIdentity={showIdentity} />;
                    })
                  )}
                </section>

                <footer className="composer-shell">
                  <textarea
                    ref={composerRef}
                    value={composer}
                    onChange={(event) => setComposer(event.target.value)}
                    onKeyDown={handleComposerKeyDown}
                    placeholder="向本地 agent 发送任务..."
                    rows={2}
                  />
                  <div className="composer-actions">
                    <span className={sendError ? "composer-error" : undefined}>
                      {sendError ?? (selectedAgentBusy ? "当前 agent 正在流式返回..." : "本地模式 / Electron host API / mock runtime seam")}
                    </span>
                    <button
                      className={canSend ? "icon-button send-button ready" : "icon-button send-button"}
                      disabled={!canSend}
                      onClick={() => void handleSend()}
                      aria-label={selectedAgentBusy ? "运行中" : "发送"}
                      title={selectedAgentBusy ? "运行中" : "发送"}
                    >
                      <svg viewBox="0 0 20 20" aria-hidden="true">
                        <path d="M3.5 10.8 15.6 4.9c.8-.4 1.5.3 1.1 1.1l-5.9 12.1c-.4.9-1.7.8-2-.1L7.2 12.6a1 1 0 0 0-.6-.6L3.6 10.3c-.9-.3-.9-1.6-.1-2Z" />
                      </svg>
                    </button>
                  </div>
                </footer>
              </main>
            </div>
          </section>
        </>
      ) : (
          <section className="workspace-shell settings-shell">
            <header className="column-topbar workspace-topbar">
              <div className="topbar-copy">
                <strong>{titlebarTitle}</strong>
              </div>
              <div className="topbar-actions">
                <button className="topbar-pill" onClick={() => setView("chat")} title="返回对话">
                  <span className={`runtime-dot ${runtime.state}`} />
                  {runtime.state}
                </button>
              </div>
            </header>
            <div className="workspace-frame settings-frame">
              <main className="settings-page">
                <section className="settings-card">
                  <div className="eyebrow">settings</div>
                  <h2>第一版设置</h2>
                  <p>可以在这台电脑运行 OpenPPX Node，也可以连接可信局域网中的另一台机器。局域网模式需要 Bearer Token。</p>
                  <div className="runtime-actions">
                    <button onClick={() => void refreshDiagnostics()}>刷新诊断</button>
                  </div>
                </section>
                <section className="settings-card">
                  <h3>Connection config</h3>
                  <div className="settings-form">
                    <label className="settings-field">
                      <span>运行位置</span>
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
                        <option value="local">在这台电脑运行</option>
                        <option value="lan">连接局域网 OpenPPX Node</option>
                      </select>
                    </label>
                    <label className="settings-field">
                      <span>Target name</span>
                      <input
                        value={connectionForm.targetName}
                        onChange={(event) => setConnectionForm((current) => ({ ...current, targetName: event.target.value }))}
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
                    {connectionForm.targetType === "lan" && (
                      <label className="settings-field">
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
                              ? "已安全保存；留空保持原 Token"
                              : "输入远端 OPENPPX_CLIENT_API_TOKEN"
                          }
                        />
                      </label>
                    )}
                  </div>
                  <div className="runtime-actions">
                    <button
                      className="secondary"
                      onClick={() => void handleConnectionTest()}
                      disabled={savingConnection || testingConnection}
                    >
                      {testingConnection ? "测试中" : "测试连接"}
                    </button>
                    <button onClick={() => void handleConnectionSave()} disabled={savingConnection || testingConnection}>
                      {savingConnection ? "保存中" : "保存并应用"}
                    </button>
                  </div>
                  {connectionFeedback && <small>{connectionFeedback}</small>}
                </section>
                <section className="settings-card">
                  <h3>Runtime status</h3>
                  <p>{runtime.summary}</p>
                  <small>{runtime.detail}</small>
                  <div className="runtime-actions">
                    <button onClick={handleRuntimeAction}>{runtimeActionLabel(runtime.state)}</button>
                    <button
                      className="secondary"
                      onClick={() => window.ppxClient.runRuntimeCommand("stop").then(setRuntime)}
                    >
                      停止
                    </button>
                  </div>
                </section>
                <section className="settings-card">
                  <h3>Connection</h3>
                  <dl className="diagnostics-grid">
                    <div>
                      <dt>Target</dt>
                      <dd>{diagnostics ? `${diagnostics.target.name} (${diagnostics.mode})` : "-"}</dd>
                    </div>
                    <div>
                      <dt>Mode</dt>
                      <dd>{diagnostics?.mode ?? "-"}</dd>
                    </div>
                    <div>
                      <dt>Client API</dt>
                      <dd>{diagnostics?.clientApiHealthy ? "healthy" : "offline"}</dd>
                    </div>
                    <div>
                      <dt>Protocol</dt>
                      <dd>
                        {diagnostics?.clientApiProtocolVersion === undefined
                          ? "-"
                          : `v${diagnostics.clientApiProtocolVersion} / ${diagnostics.clientApiCompatibility ?? "unknown"}`}
                      </dd>
                    </div>
                    <div>
                      <dt>Node version</dt>
                      <dd>{diagnostics?.clientApiProductVersion ?? "-"}</dd>
                    </div>
                    <div>
                      <dt>Authentication</dt>
                      <dd>{diagnostics?.clientApiAuthState ?? "unknown"}</dd>
                    </div>
                    <div>
                      <dt>Node</dt>
                      <dd>{diagnostics?.nodeName ?? "-"}</dd>
                    </div>
                    <div>
                      <dt>Process</dt>
                      <dd>{diagnostics?.clientApiProcessRunning ? "running" : "not managed"}</dd>
                    </div>
                    <div>
                      <dt>Gateway control</dt>
                      <dd>{diagnostics?.clientApiManagedByClient ? "managed by client" : "external / LAN"}</dd>
                    </div>
                    <div>
                      <dt>Agents</dt>
                      <dd>{diagnostics?.agentCount ?? 0}</dd>
                    </div>
                  </dl>
                </section>
                <section className="settings-card">
                  <h3>Paths</h3>
                  <dl className="diagnostics-stack">
                    <div>
                      <dt>openppx root</dt>
                      <dd>{diagnostics?.openppxRoot || "-"}</dd>
                    </div>
                    <div>
                      <dt>Python</dt>
                      <dd>{diagnostics?.pythonBin || "-"}</dd>
                    </div>
                    <div>
                      <dt>Global config</dt>
                      <dd>{diagnostics?.globalConfigPath || "-"}</dd>
                    </div>
                    <div>
                      <dt>Bridge script</dt>
                      <dd>{diagnostics?.bridgeScriptPath || "-"}</dd>
                    </div>
                  </dl>
                </section>
                <section className="settings-card">
                  <h3>Diagnostics</h3>
                  <dl className="diagnostics-grid">
                    <div>
                      <dt>Root exists</dt>
                      <dd>{diagnostics?.openppxRootExists ? "yes" : "no"}</dd>
                    </div>
                    <div>
                      <dt>Config exists</dt>
                      <dd>{diagnostics?.globalConfigExists ? "yes" : "no"}</dd>
                    </div>
                    <div>
                      <dt>Bridge exists</dt>
                      <dd>{diagnostics?.bridgeScriptExists ? "yes" : "no"}</dd>
                    </div>
                    <div>
                      <dt>Debug</dt>
                      <dd>{diagnostics?.debugEnabled ? "enabled" : "off"}</dd>
                    </div>
                    <div>
                      <dt>Dev fallbacks</dt>
                      <dd>
                        {diagnostics?.mockEnabled
                          ? "mock"
                          : diagnostics?.legacyBridgeEnabled
                            ? "legacy bridge"
                            : "off"}
                      </dd>
                    </div>
                    <div>
                      <dt>Session cache</dt>
                      <dd>{diagnostics?.sessionCacheEntries ?? 0}</dd>
                    </div>
                    <div>
                      <dt>Message cache</dt>
                      <dd>{diagnostics?.messageCacheEntries ?? 0}</dd>
                    </div>
                    <div>
                      <dt>Client API URL</dt>
                      <dd>{diagnostics?.clientApiBaseUrl || "-"}</dd>
                    </div>
                    <div>
                      <dt>Last API error</dt>
                      <dd>{diagnostics?.clientApiLastError || "-"}</dd>
                    </div>
                  </dl>
                </section>
              </main>
            </div>
          </section>
      )}
    </div>
  );
}
