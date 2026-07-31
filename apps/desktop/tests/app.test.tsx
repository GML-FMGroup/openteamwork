import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { App } from "../app/src/App";
import type { BootstrapPayload, ClientDiagnostics, PpxClientApi, RunEvent, RuntimeStatus, SessionSummary } from "../app/src/types";

function buildBootstrapPayload(): BootstrapPayload {
  const runtime: RuntimeStatus = {
    target: { id: "local-default", type: "local", name: "This Mac" },
    state: "healthy",
    summary: "ready",
    detail: "detail",
  };
  const sessions: SessionSummary[] = [
    {
      id: "session-a",
      agentId: "agent-1",
      title: "Session A",
      updatedAt: "2026-04-02T10:00:00.000Z",
      lastMessagePreview: "Preview A should stay hidden",
    },
    {
      id: "session-b",
      agentId: "agent-1",
      title: "Session B",
      updatedAt: "2026-04-02T09:00:00.000Z",
      lastMessagePreview: "Preview B should stay hidden",
    },
  ];
  return {
    runtime,
    agents: [
      {
        id: "agent-1",
        name: "Agent 1",
        description: "Local test agent",
        enabled: true,
        status: "healthy",
        tags: ["local"],
      },
    ],
    sessions,
    messages: [
      {
        id: "message-a",
        sessionId: "session-a",
        role: "assistant",
        status: "completed",
        createdAt: "2026-04-02T10:00:01.000Z",
        parts: [{ type: "markdown", text: "Loaded Session A" }],
      },
    ],
    selectedAgentId: "agent-1",
    selectedSessionId: "session-a",
  };
}

function buildDiagnostics(): ClientDiagnostics {
  return {
    mode: "local",
    target: { id: "local-default", type: "local", name: "This Mac" },
    openppxRoot: "/tmp/openppx_root",
    openppxRootExists: true,
    pythonBin: "/tmp/openppx_root/.venv/bin/python",
    globalConfigPath: "/tmp/.openppx/global_config.json",
    globalConfigExists: true,
    clientApiBaseUrl: "http://127.0.0.1:8765",
    clientApiManagedByClient: true,
    clientApiHealthy: true,
    clientApiProductVersion: "0.4",
    clientApiProtocolVersion: 1,
    clientApiCompatibility: "compatible",
    clientApiProcessRunning: true,
    bridgeScriptPath: "/tmp/ppx-client/scripts/openppx_bridge.py",
    bridgeScriptExists: true,
    agentCount: 1,
    sessionCacheEntries: 1,
    messageCacheEntries: 1,
    debugEnabled: false,
    mockEnabled: false,
    legacyBridgeEnabled: false,
  };
}

function installClient(overrides: Partial<PpxClientApi> = {}): { client: PpxClientApi; emit: (event: RunEvent) => void } {
  let listener: ((event: RunEvent) => void) | null = null;
  const client: PpxClientApi = {
    bootstrap: async () => buildBootstrapPayload(),
    getDiagnostics: async () => buildDiagnostics(),
    saveConnectionSettings: async () => buildDiagnostics(),
    runRuntimeCommand: async () => buildBootstrapPayload().runtime,
    listSessions: async () => ({ sessions: buildBootstrapPayload().sessions }),
    createSession: async () => ({ session: buildBootstrapPayload().sessions[0] }),
    loadSession: async () => ({ messages: [] }),
    sendMessage: async () => new Promise<{ runId: string }>(() => undefined),
    onRunEvent: (next) => {
      listener = next;
      return () => {
        if (listener === next) {
          listener = null;
        }
      };
    },
    ...overrides,
  };
  window.ppxClient = client;
  return {
    client,
    emit: (event) => listener?.(event),
  };
}

describe("App sending state", () => {
  it("jumps to the latest reply when loading a session", async () => {
    const scrollTo = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: scrollTo,
    });

    installClient({
      loadSession: async () => ({
        messages: [
          {
            id: "message-b",
            sessionId: "session-b",
            role: "assistant",
            status: "completed",
            createdAt: "2026-04-02T10:00:02.000Z",
            parts: [{ type: "markdown", text: "Loaded Session B" }],
          },
        ],
      }),
    });

    render(<App />);

    await screen.findByText("Loaded Session A");
    fireEvent.click(screen.getByRole("button", { name: /Session B/ }));
    await screen.findByText("Loaded Session B");

    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: "auto" }));
  });

  it("keeps send disabled while the current agent still has a running reply", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "发送" });

    fireEvent.change(screen.getByPlaceholderText("向本地 agent 发送任务..."), {
      target: { value: "hello world" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByRole("button", { name: "运行中" });

    fireEvent.click(screen.getByRole("button", { name: /Session B/ }));
    fireEvent.change(screen.getByPlaceholderText("向本地 agent 发送任务..."), {
      target: { value: "follow up" },
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "运行中" })).toBeDisabled();
    });
  });

  it("clears the current session sending state after a completed message event", async () => {
    const { emit } = installClient();

    render(<App />);

    await screen.findByRole("button", { name: "发送" });

    fireEvent.change(screen.getByPlaceholderText("向本地 agent 发送任务..."), {
      target: { value: "hello world" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByRole("button", { name: "运行中" });

    await act(async () => {
      emit({
        type: "message.updated",
        runId: "run-1",
        sessionId: "session-a",
        messageId: "assistant-1",
        status: "completed",
        replaceParts: [{ type: "markdown", text: "done" }],
      });
    });

    fireEvent.change(screen.getByPlaceholderText("向本地 agent 发送任务..."), {
      target: { value: "second try" },
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
    });
  });

  it("sends on Enter and keeps Shift+Enter for newline", async () => {
    const sendMessage = vi.fn(async () => ({ runId: "run-1" }));
    installClient({ sendMessage });

    render(<App />);

    await screen.findByRole("button", { name: "发送" });

    const composer = screen.getByPlaceholderText("向本地 agent 发送任务...");

    fireEvent.change(composer, { target: { value: "first line" } });
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter", charCode: 13 });

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith({
        agentId: "agent-1",
        sessionId: "session-a",
        text: "first line",
      });
    });

    fireEvent.change(composer, { target: { value: "hello" } });
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter", charCode: 13, shiftKey: true });

    expect(sendMessage).toHaveBeenCalledTimes(1);
  });

  it("creates a session on startup when the selected agent has none", async () => {
    const createdSession: SessionSummary = {
      id: "session-created",
      agentId: "agent-1",
      title: "New local session",
      updatedAt: "2026-04-02T10:01:00.000Z",
      lastMessagePreview: "Start a task",
    };
    const createSession = vi.fn(async () => ({ session: createdSession }));
    const sendMessage = vi.fn(async () => ({ runId: "run-1" }));
    installClient({
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        sessions: [],
        messages: [],
        selectedSessionId: "",
      }),
      createSession,
      sendMessage,
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("New local session").length).toBeGreaterThan(0);
    });

    expect(createSession).toHaveBeenCalledWith("agent-1");

    fireEvent.change(screen.getByPlaceholderText("向本地 agent 发送任务..."), {
      target: { value: "first task" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith({
        agentId: "agent-1",
        sessionId: "session-created",
        text: "first task",
      });
    });
  });

  it("creates a session before sending if the active session was not selected yet", async () => {
    const createdSession: SessionSummary = {
      id: "session-on-send",
      agentId: "agent-1",
      title: "New local session",
      updatedAt: "2026-04-02T10:01:00.000Z",
      lastMessagePreview: "Start a task",
    };
    const createSession = vi.fn(async () => ({ session: createdSession }));
    const sendMessage = vi.fn(async () => ({ runId: "run-1" }));
    installClient({
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        sessions: [],
        messages: [],
        selectedSessionId: "stale-session",
      }),
      listSessions: async () => ({ sessions: [] }),
      createSession,
      sendMessage,
    });

    render(<App />);

    await screen.findByText("Agent 1 is ready");

    fireEvent.change(screen.getByPlaceholderText("向本地 agent 发送任务..."), {
      target: { value: "recover session" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(createSession).toHaveBeenCalledWith("agent-1");
      expect(sendMessage).toHaveBeenCalledWith({
        agentId: "agent-1",
        sessionId: "session-on-send",
        text: "recover session",
      });
    });
  });

  it("shows a send error instead of failing silently", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      installClient({
        sendMessage: async () => {
          throw new Error("gateway refused the run");
        },
      });

      render(<App />);

      await screen.findByRole("button", { name: "发送" });

      fireEvent.change(screen.getByPlaceholderText("向本地 agent 发送任务..."), {
        target: { value: "will fail" },
      });
      fireEvent.click(screen.getByRole("button", { name: "发送" }));

      await screen.findByText("gateway refused the run");
    } finally {
      consoleError.mockRestore();
    }
  });

  it("renders an icon send button that activates when composer has text", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "发送" });

    const sendButton = screen.getByRole("button", { name: "发送" });
    expect(sendButton).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("向本地 agent 发送任务..."), {
      target: { value: "hello world" },
    });

    await waitFor(() => {
      expect(sendButton).toBeEnabled();
      expect(sendButton.className).toContain("ready");
    });
  });

  it("does not render session preview subtitles in the session list", async () => {
    installClient();

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("Session A").length).toBeGreaterThan(0);
    });

    expect(screen.queryByText("Preview A should stay hidden")).not.toBeInTheDocument();
    expect(screen.queryByText("Preview B should stay hidden")).not.toBeInTheDocument();
  });

  it("uses the first user message as the visible session title", async () => {
    const createdSession: SessionSummary = {
      id: "session-created",
      agentId: "agent-1",
      title: "新对话",
      updatedAt: "2026-04-02T10:01:00.000Z",
      lastMessagePreview: "",
    };
    const sendMessage = vi.fn(async () => ({ runId: "run-1" }));
    installClient({
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        sessions: [],
        messages: [],
        selectedSessionId: "",
      }),
      createSession: async () => ({ session: createdSession }),
      sendMessage,
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("新对话").length).toBeGreaterThan(0);
    });

    fireEvent.change(screen.getByPlaceholderText("向本地 agent 发送任务..."), {
      target: { value: "帮我查一下深圳到青岛的火车和费用" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(screen.getAllByText("帮我查一下深圳到青岛的火车和费用").length).toBeGreaterThan(0);
    });
    expect(sendMessage).toHaveBeenCalledWith({
      agentId: "agent-1",
      sessionId: "session-created",
      text: "帮我查一下深圳到青岛的火车和费用",
    });
  });

  it("shows assistant identity only once across consecutive assistant replies", async () => {
    installClient({
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        messages: [
          {
            id: "message-a",
            sessionId: "session-a",
            role: "assistant",
            status: "completed",
            createdAt: "2026-04-02T10:00:01.000Z",
            parts: [{ type: "markdown", text: "First chunk" }],
          },
          {
            id: "message-b",
            sessionId: "session-a",
            role: "assistant",
            status: "streaming",
            createdAt: "2026-04-02T10:00:02.000Z",
            parts: [{ type: "step_ref", stepId: "step-1", title: "exec", status: "running", detail: "command: pwd" }],
          },
        ],
      }),
    });

    render(<App />);

    await screen.findByText("First chunk");
    expect(screen.getAllByText("Agent")).toHaveLength(1);
  });

  it("clears previous messages immediately when switching sessions", async () => {
    let resolveLoad: ((value: { messages: BootstrapPayload["messages"] }) => void) | null = null;
    installClient({
      loadSession: async () =>
        await new Promise<{ messages: BootstrapPayload["messages"] }>((resolve) => {
          resolveLoad = resolve;
        }),
    });

    render(<App />);

    await screen.findByText("Loaded Session A");

    fireEvent.click(screen.getByRole("button", { name: /Session B/ }));

    await waitFor(() => {
      expect(screen.queryByText("Loaded Session A")).not.toBeInTheDocument();
    });

    await act(async () => {
      resolveLoad?.({
        messages: [
          {
            id: "message-b",
            sessionId: "session-b",
            role: "assistant",
            status: "completed",
            createdAt: "2026-04-02T10:00:02.000Z",
            parts: [{ type: "markdown", text: "Loaded Session B" }],
          },
        ],
      });
    });

    await screen.findByText("Loaded Session B");
  });

  it("renders live diagnostics in settings view", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "发送" });

    fireEvent.click(screen.getByRole("button", { name: "设置" }));

    await screen.findByText("Connection");
    expect(screen.getByText("http://127.0.0.1:8765")).toBeInTheDocument();
    expect(screen.getByText("/tmp/openppx_root")).toBeInTheDocument();
    expect(screen.getByText("This Mac (local)")).toBeInTheDocument();
    expect(screen.getByText("v1 / compatible")).toBeInTheDocument();
    expect(screen.getByText("0.4")).toBeInTheDocument();
  });

  it("renders remote target diagnostics when provided", async () => {
    installClient({
      getDiagnostics: async () => ({
        ...buildDiagnostics(),
        mode: "remote",
        target: { id: "remote-default", type: "remote", name: "Ops Gateway" },
        clientApiManagedByClient: false,
        clientApiBaseUrl: "http://10.0.0.8:8765",
        clientApiProcessRunning: false,
      }),
    });

    render(<App />);

    await screen.findByRole("button", { name: "发送" });
    fireEvent.click(screen.getByRole("button", { name: "设置" }));

    await screen.findByText("Ops Gateway (remote)");
    expect(screen.getByText("external / remote")).toBeInTheDocument();
  });

  it("saves connection settings from the settings form", async () => {
    const saveConnectionSettings = vi.fn(async () => ({
      ...buildDiagnostics(),
      mode: "remote" as const,
      target: { id: "remote-ops-gateway", type: "remote" as const, name: "Ops Gateway" },
      clientApiManagedByClient: false,
      clientApiBaseUrl: "http://10.0.0.8:8765",
    }));

    installClient({
      saveConnectionSettings,
      getDiagnostics: async () => buildDiagnostics(),
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        runtime: {
          ...buildBootstrapPayload().runtime,
          target: { id: "remote-ops-gateway", type: "remote", name: "Ops Gateway" },
        },
      }),
    });

    render(<App />);

    await screen.findByRole("button", { name: "发送" });
    fireEvent.click(screen.getByRole("button", { name: "设置" }));

    fireEvent.change(screen.getByDisplayValue("This Mac"), {
      target: { value: "Ops Gateway" },
    });
    fireEvent.change(screen.getByDisplayValue("http://127.0.0.1:8765"), {
      target: { value: "http://10.0.0.8:8765" },
    });
    fireEvent.change(screen.getByDisplayValue("local"), {
      target: { value: "remote" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存并应用" }));

    await waitFor(() => {
      expect(saveConnectionSettings).toHaveBeenCalledWith({
        targetType: "remote",
        targetId: "remote-ops-gateway",
        targetName: "Ops Gateway",
        clientApiBaseUrl: "http://10.0.0.8:8765",
      });
    });
  });
});
