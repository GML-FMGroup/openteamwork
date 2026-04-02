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
    targetType: diagnostics?.target.type ?? "local",
    targetId: diagnostics?.target.id ?? "local-default",
    targetName: diagnostics?.target.name ?? "This Mac",
    clientApiBaseUrl: diagnostics?.clientApiBaseUrl ?? "http://127.0.0.1:8765",
  };
}

function normalizeConnectionSettings(settings: ConnectionSettings): ConnectionSettings {
  const targetType = settings.targetType;
  const targetName = settings.targetName.trim() || (targetType === "remote" ? "Remote Gateway" : "This Mac");
  const normalizedName =
    targetName
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "default";
  const existingId = settings.targetId.trim();
  const targetId =
    !existingId ||
    existingId === "local-default" ||
    existingId === "remote-default" ||
    !existingId.startsWith(`${targetType}-`)
      ? `${targetType}-${normalizedName}`
      : existingId;
  const clientApiBaseUrl = settings.clientApiBaseUrl.trim() || "http://127.0.0.1:8765";
  return {
    targetType,
    targetId,
    targetName,
    clientApiBaseUrl,
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
  const [composer, setComposer] = useState("");
  const [sendingSessionIds, setSendingSessionIds] = useState<string[]>([]);
  const [connectionForm, setConnectionForm] = useState<ConnectionSettings>(buildConnectionSettings(null));
  const [savingConnection, setSavingConnection] = useState(false);
  const switchRequestIdRef = useRef(0);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

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
          [event.session, ...current.filter((item) => item.id !== event.session.id)].sort((left, right) =>
            right.updatedAt.localeCompare(left.updatedAt),
          ),
        );
      } else if (event.type === "run.finished") {
        setSendingSessionIds((current) => removeSendingSession(current, event.sessionId));
      }
    });

    window.ppxClient
      .bootstrap()
      .then((payload: BootstrapPayload) => {
        if (!mounted) {
          return;
        }
        setRuntime(payload.runtime);
        setAgents(payload.agents);
        setSessions(payload.sessions);
        setMessages(payload.messages);
        setSelectedAgentId(payload.selectedAgentId);
        setSelectedSessionId(payload.selectedSessionId);
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

  async function switchAgent(agentId: string): Promise<void> {
    const requestId = ++switchRequestIdRef.current;
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
      setMessages(loaded.messages);
      return;
    }
  }

  async function switchSession(session: SessionSummary): Promise<void> {
    const requestId = ++switchRequestIdRef.current;
    setSelectedAgentId(session.agentId);
    setSelectedSessionId(session.id);
    setMessages([]);
    const loaded = await window.ppxClient.loadSession(session.id);
    if (requestId !== switchRequestIdRef.current) {
      return;
    }
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
    const nextSettings = normalizeConnectionSettings(connectionForm);
    setSavingConnection(true);
    try {
      const nextDiagnostics = await window.ppxClient.saveConnectionSettings(nextSettings);
      setDiagnostics(nextDiagnostics);
      const nextRuntime = await window.ppxClient.runRuntimeCommand("restart");
      setRuntime(nextRuntime);
      try {
        const payload = await window.ppxClient.bootstrap();
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
    } finally {
      setSavingConnection(false);
    }
  }

  async function handleNewSession(): Promise<void> {
    if (!selectedAgentId) {
      return;
    }
    const created = await window.ppxClient.createSession(selectedAgentId);
    setSessions((current) => [created.session, ...current.filter((item) => item.id !== created.session.id)]);
    setSelectedSessionId(created.session.id);
    setMessages([]);
  }

  async function handleSend(): Promise<void> {
    const text = composer.trim();
    if (!text || !selectedAgentId || !selectedSessionId) {
      return;
    }
    const sessionId = selectedSessionId;
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
    setMessages((current) => [...current, optimisticMessage]);
    try {
      await window.ppxClient.sendMessage({
        agentId: selectedAgentId,
        sessionId,
        text,
      });
    } catch (error) {
      console.error("Failed to send message", error);
    } finally {
      setSendingSessionIds((current) => current.filter((item) => item !== sessionId));
    }
  }

  function handleComposerKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    event.preventDefault();
    if (currentSessionSending || !composer.trim()) {
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
      <aside className="nav-rail">
        <button className={view === "chat" ? "nav-item active" : "nav-item"} onClick={() => setView("chat")}>
          对话
        </button>
        <button className={view === "settings" ? "nav-item active" : "nav-item"} onClick={() => setView("settings")}>
          设置
        </button>
      </aside>

      {view === "chat" ? (
        <>
          <section className="sidebar">
            <div className="panel search-panel">
              <div className="eyebrow">local machine</div>
              <h1>openppx workbench</h1>
              <p>本地优先的 agent 工作台。第一版只聚焦 runtime、agent 和对话主路径。</p>
            </div>

            <div className="panel runtime-panel">
              <div className="panel-header">
                <span>本地 runtime</span>
                <span className={`runtime-dot ${runtime.state}`}>{runtime.state}</span>
              </div>
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
            </div>

            <div className="panel">
              <div className="panel-header">
                <span>Agents</span>
              </div>
              <div className="list-stack">
                {agents.map((agent) => (
                  <button
                    key={agent.id}
                    className={agent.id === selectedAgentId ? "list-item active" : "list-item"}
                    onClick={() => void switchAgent(agent.id)}
                  >
                    <div>
                      <strong>{agent.name}</strong>
                      <p>{agent.description}</p>
                    </div>
                    <span className={`tag status-${agent.status}`}>{agent.status}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="panel">
              <div className="panel-header">
                <span>Sessions</span>
                <button className="secondary small" onClick={() => void handleNewSession()}>
                  新建
                </button>
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
                      <p>{session.lastMessagePreview}</p>
                    </div>
                    <time>{new Date(session.updatedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time>
                  </button>
                ))}
              </div>
            </div>
          </section>

          <main className="workspace">
            <header className="workspace-header">
              <div>
                <div className="eyebrow">{selectedAgent?.description ?? "Select an agent"}</div>
                <h2>{selectedAgent?.name ?? "No agent selected"}</h2>
              </div>
              <div className="session-meta">
                <span>{selectedSession?.title ?? "No session"}</span>
                <span className={`runtime-chip ${runtime.state}`}>{runtime.state}</span>
              </div>
            </header>

            <section className="message-stream">
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
                messages.map((message) => <MessageBubble key={message.id} message={message} />)
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
                <span>{currentSessionSending ? "当前会话正在流式返回..." : "本地模式 / Electron host API / mock runtime seam"}</span>
                <button disabled={currentSessionSending || !composer.trim()} onClick={() => void handleSend()}>
                  {currentSessionSending ? "运行中" : "发送"}
                </button>
              </div>
            </footer>
          </main>
        </>
      ) : (
        <main className="settings-page">
          <section className="settings-card">
            <div className="eyebrow">settings</div>
            <h2>第一版设置</h2>
            <p>当前以本地模式为主，但已经补了远程 target 的接入准备。这里会直接展示当前 gateway 和 transport 的真实诊断信息。</p>
            <div className="runtime-actions">
              <button onClick={() => void refreshDiagnostics()}>刷新诊断</button>
            </div>
          </section>
          <section className="settings-card">
            <h3>Connection config</h3>
            <div className="settings-form">
              <label className="settings-field">
                <span>Target type</span>
                <select
                  value={connectionForm.targetType}
                  onChange={(event) =>
                    setConnectionForm((current) => ({
                      ...current,
                      targetType: event.target.value === "remote" ? "remote" : "local",
                    }))
                  }
                >
                  <option value="local">local</option>
                  <option value="remote">remote</option>
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
            </div>
            <div className="runtime-actions">
              <button onClick={() => void handleConnectionSave()} disabled={savingConnection}>
                {savingConnection ? "保存中" : "保存并应用"}
              </button>
            </div>
          </section>
          <section className="settings-card">
            <h3>Runtime status</h3>
            <p>{runtime.summary}</p>
            <small>{runtime.detail}</small>
          </section>
          <section className="settings-card">
            <h3>Connection</h3>
            <dl className="diagnostics-grid">
              <div>
                <dt>Target</dt>
                <dd>{diagnostics ? `${diagnostics.target.name} (${diagnostics.target.type})` : "-"}</dd>
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
                <dt>Process</dt>
                <dd>{diagnostics?.clientApiProcessRunning ? "running" : "not managed"}</dd>
              </div>
              <div>
                <dt>Gateway control</dt>
                <dd>{diagnostics?.clientApiManagedByClient ? "managed by client" : "external / remote"}</dd>
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
            </dl>
          </section>
        </main>
      )}
    </div>
  );
}
