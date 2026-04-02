import { useEffect, useMemo, useState } from "react";
import type {
  AgentProfile,
  BootstrapPayload,
  ChatMessage,
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

function runtimeActionLabel(state: RuntimeState): string {
  if (state === "stopped") {
    return "启动";
  }
  if (state === "healthy") {
    return "重启";
  }
  return "重试";
}

export function App() {
  const [view, setView] = useState<NavView>("chat");
  const [ready, setReady] = useState(false);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [composer, setComposer] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
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
        if (event.status === "completed") {
          setSending(false);
        }
      } else if (event.type === "session.updated") {
        setSessions((current) =>
          [event.session, ...current.filter((item) => item.id !== event.session.id)].sort((left, right) =>
            right.updatedAt.localeCompare(left.updatedAt),
          ),
        );
      } else if (event.type === "run.finished") {
        setSending(false);
      }
    });

    window.ppxClient.bootstrap().then((payload: BootstrapPayload) => {
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
    });

    return () => {
      mounted = false;
      off();
    };
  }, []);

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedAgentId) ?? null,
    [agents, selectedAgentId],
  );

  const selectedSession = useMemo(
    () => sessions.find((session) => session.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId],
  );

  async function switchAgent(agentId: string): Promise<void> {
    setSelectedAgentId(agentId);
    const created = await window.ppxClient.createSession(agentId);
    setSessions((current) => [created.session, ...current.filter((item) => item.id !== created.session.id)]);
    setSelectedSessionId(created.session.id);
    setMessages([]);
  }

  async function switchSession(session: SessionSummary): Promise<void> {
    setSelectedAgentId(session.agentId);
    setSelectedSessionId(session.id);
    const loaded = await window.ppxClient.loadSession(session.id);
    setMessages(loaded.messages);
  }

  async function handleRuntimeAction(): Promise<void> {
    if (!runtime) {
      return;
    }
    const command = runtime.state === "stopped" ? "start" : "restart";
    const next = await window.ppxClient.runRuntimeCommand(command);
    setRuntime(next);
  }

  async function handleNewSession(): Promise<void> {
    if (!selectedAgentId) {
      return;
    }
    const created = await window.ppxClient.createSession(selectedAgentId);
    setSessions((current) => [created.session, ...current]);
    setSelectedSessionId(created.session.id);
    setMessages([]);
  }

  async function handleSend(): Promise<void> {
    const text = composer.trim();
    if (!text || !selectedAgentId || !selectedSessionId) {
      return;
    }
    setSending(true);
    setComposer("");
    const optimisticMessage: ChatMessage = {
      id: `local-user-${crypto.randomUUID()}`,
      sessionId: selectedSessionId,
      role: "user",
      status: "completed",
      createdAt: new Date().toISOString(),
      parts: [{ type: "markdown", text }],
    };
    setMessages((current) => [...current, optimisticMessage]);
    await window.ppxClient.sendMessage({
      agentId: selectedAgentId,
      sessionId: selectedSessionId,
      text,
    });
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
                value={composer}
                onChange={(event) => setComposer(event.target.value)}
                placeholder="向本地 agent 发送任务..."
                rows={4}
              />
              <div className="composer-actions">
                <span>{sending ? "正在流式返回..." : "本地模式 / Electron host API / mock runtime seam"}</span>
                <button disabled={sending || !composer.trim()} onClick={() => void handleSend()}>
                  {sending ? "运行中" : "发送"}
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
            <ul>
              <li>当前只支持本地模式。</li>
              <li>Renderer 通过 preload 暴露的 host API 访问本地运行时。</li>
              <li>下一步将把 mock adapter 替换为真实的 openppx client-api。</li>
            </ul>
          </section>
          <section className="settings-card">
            <h3>Runtime status</h3>
            <p>{runtime.summary}</p>
            <small>{runtime.detail}</small>
          </section>
        </main>
      )}
    </div>
  );
}
