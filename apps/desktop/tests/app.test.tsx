import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    desktopVersion: "0.5.0-beta.1",
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
    clientApiAuthState: "authenticated",
    clientApiCredentialConfigured: true,
    nodeId: "node_test",
    nodeName: "This Mac",
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
    testConnectionSettings: async () => buildDiagnostics(),
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

  it("does not steal scroll position while the user is reading history", async () => {
    const { emit } = installClient();
    render(<App />);

    await screen.findByText("Loaded Session A");
    const stream = document.querySelector<HTMLElement>(".message-stream");
    expect(stream).not.toBeNull();
    Object.defineProperties(stream!, {
      scrollHeight: { configurable: true, value: 1_200 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, writable: true, value: 100 },
    });
    const scrollTo = vi.fn();
    Object.defineProperty(stream!, "scrollTo", { configurable: true, value: scrollTo });

    fireEvent.scroll(stream!);
    await screen.findByRole("button", { name: /Jump to latest/ });

    act(() => {
      emit({
        type: "message.created",
        runId: "run-history",
        sessionId: "session-a",
        message: {
          id: "message-new",
          sessionId: "session-a",
          role: "assistant",
          status: "streaming",
          createdAt: "2026-04-02T10:00:03.000Z",
          parts: [{ type: "markdown", text: "New reply while reading" }],
        },
      });
    });

    await screen.findByText("New reply while reading");
    expect(scrollTo).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Jump to latest/ }));
    expect(scrollTo).toHaveBeenCalledWith({ top: 1_200, behavior: "smooth" });
  });

  it("supports workspace collapse and search shortcuts", async () => {
    installClient();
    render(<App />);

    const search = await screen.findByPlaceholderText("Search sessions");
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(search).toHaveFocus();

    fireEvent.keyDown(window, { key: "b", metaKey: true });
    await screen.findByRole("button", { name: "P" });

    fireEvent.keyDown(window, { key: "b", metaKey: true, shiftKey: true });
    await screen.findByRole("button", { name: "Open task panel" });
  });

  it("starts with both side columns collapsed in a narrow window", async () => {
    const originalMatchMedia = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({
        matches: true,
        media: "(max-width: 1080px)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    });
    try {
      installClient();
      render(<App />);

      await screen.findByRole("button", { name: "P" });
      await screen.findByRole("button", { name: "Open task panel" });
    } finally {
      Object.defineProperty(window, "matchMedia", {
        configurable: true,
        value: originalMatchMedia,
      });
    }
  });

  it("keeps send disabled while the current agent still has a running reply", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "hello world" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByRole("button", { name: "Running" });

    fireEvent.click(screen.getByRole("button", { name: /Session B/ }));
    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "follow up" },
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Running" })).toBeDisabled();
    });
  });

  it("clears the current session sending state after a completed message event", async () => {
    const { emit } = installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "hello world" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByRole("button", { name: "Running" });

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

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "second try" },
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    });
  });

  it("sends on Enter and keeps Shift+Enter for newline", async () => {
    const sendMessage = vi.fn(async () => ({ runId: "run-1" }));
    installClient({ sendMessage });

    render(<App />);

    await screen.findByRole("button", { name: "Send" });

    const composer = screen.getByPlaceholderText("Describe the outcome you want...");

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

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "first task" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

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

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "recover session" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

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

      await screen.findByRole("button", { name: "Send" });

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
        target: { value: "will fail" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Send" }));

      await screen.findByText("gateway refused the run");
    } finally {
      consoleError.mockRestore();
    }
  });

  it("renders an icon send button that activates when composer has text", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });

    const sendButton = screen.getByRole("button", { name: "Send" });
    expect(sendButton).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "hello world" },
    });

    await waitFor(() => {
      expect(sendButton).toBeEnabled();
      expect(sendButton.className).toContain("ready");
    });
  });

  it("renders compact session context in the session list", async () => {
    installClient();

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("Session A").length).toBeGreaterThan(0);
    });

    expect(screen.getByText("Preview A should stay hidden")).toBeInTheDocument();
    expect(screen.getByText("Preview B should stay hidden")).toBeInTheDocument();
  });

  it("hides the generic OpenPPX session preview", async () => {
    installClient({
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        sessions: buildBootstrapPayload().sessions.map((session) => ({
          ...session,
          lastMessagePreview: "OpenPPX session",
        })),
      }),
    });

    render(<App />);

    await screen.findByText("Loaded Session A");
    expect(screen.queryByText("OpenPPX session")).not.toBeInTheDocument();
    expect(document.querySelectorAll(".session-row-meta > span")).toHaveLength(0);
  });

  it("uses the visible Agent list for the Node count", async () => {
    installClient({
      getDiagnostics: async () => ({ ...buildDiagnostics(), agentCount: 99 }),
    });

    render(<App />);

    await screen.findByText("Loaded Session A");
    expect(document.querySelector(".node-card-count")).toHaveTextContent("1");
  });

  it("does not show an initial block in the selected Agent summary", async () => {
    render(<App />);

    await screen.findByText("Loaded Session A");
    const agentTrigger = screen.getByRole("button", { name: /Agent 1 Local test agent/ });

    expect(agentTrigger.querySelector(".agent-monogram")).not.toBeInTheDocument();
  });

  it("uses the first user message as the visible session title", async () => {
    const createdSession: SessionSummary = {
      id: "session-created",
      agentId: "agent-1",
      title: "New chat",
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
      expect(screen.getAllByText("New chat").length).toBeGreaterThan(0);
    });

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "帮我查一下深圳到青岛的火车和费用" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

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
    const transcript = document.querySelector<HTMLElement>(".transcript-column");
    expect(transcript).not.toBeNull();
    expect(within(transcript!).getAllByText("Agent")).toHaveLength(1);
  });

  it("switches transcript, progress, and artifacts together when selecting an Agent", async () => {
    const bootstrap = buildBootstrapPayload();
    const secondSession: SessionSummary = {
      id: "session-agent-2",
      agentId: "agent-2",
      title: "Agent 2 Session",
      updatedAt: "2026-04-02T11:00:00.000Z",
      lastMessagePreview: "Agent 2 context",
    };
    const firstMessages: BootstrapPayload["messages"] = [
      {
        id: "agent-1-message",
        sessionId: "session-a",
        role: "assistant",
        status: "completed",
        createdAt: "2026-04-02T10:00:01.000Z",
        parts: [
          { type: "markdown", text: "Agent 1 transcript" },
          { type: "step_ref", stepId: "agent-1-step", title: "Agent 1 progress", status: "completed", detail: "Done" },
          { type: "file", text: "First output", fileName: "agent-1.txt", mimeType: "text/plain" },
        ],
      },
    ];
    const secondMessages: BootstrapPayload["messages"] = [
      {
        id: "agent-2-message",
        sessionId: secondSession.id,
        role: "assistant",
        status: "completed",
        createdAt: "2026-04-02T11:00:01.000Z",
        parts: [
          { type: "markdown", text: "Agent 2 transcript" },
          { type: "step_ref", stepId: "agent-2-step", title: "Agent 2 progress", status: "completed", detail: "Done" },
          { type: "file", text: "Second output", fileName: "agent-2.txt", mimeType: "text/plain" },
        ],
      },
    ];

    installClient({
      bootstrap: async () => ({
        ...bootstrap,
        agents: [
          ...bootstrap.agents,
          {
            id: "agent-2",
            name: "Agent 2",
            description: "Remote test agent",
            enabled: true,
            status: "healthy",
            tags: ["lan"],
          },
        ],
        messages: firstMessages,
      }),
      listSessions: async (agentId) => ({
        sessions: agentId === "agent-2" ? [secondSession] : bootstrap.sessions,
      }),
      loadSession: async (sessionId) => ({
        messages: sessionId === secondSession.id ? secondMessages : firstMessages,
      }),
    });

    render(<App />);

    await screen.findByText("Agent 1 transcript");
    let taskPanel = screen.getByLabelText("Task panel");
    expect(within(taskPanel).getByText("Agent 1 progress")).toBeInTheDocument();
    expect(within(taskPanel).getByText("agent-1.txt")).toBeInTheDocument();

    const agentTrigger = screen.getByRole("button", { name: /Agent 1 Local test agent/ });
    expect(screen.queryByRole("listbox", { name: "Select Agent" })).not.toBeInTheDocument();

    fireEvent.click(agentTrigger);
    expect(screen.getByRole("listbox", { name: "Select Agent" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("listbox", { name: "Select Agent" })).not.toBeInTheDocument();
    expect(agentTrigger).toHaveFocus();

    fireEvent.click(agentTrigger);
    fireEvent.pointerDown(screen.getByText("Sessions"));
    expect(screen.queryByRole("listbox", { name: "Select Agent" })).not.toBeInTheDocument();

    fireEvent.click(agentTrigger);
    fireEvent.click(screen.getByRole("option", { name: /Agent 2 Remote test agent/ }));

    await screen.findByText("Agent 2 transcript");
    taskPanel = screen.getByLabelText("Task panel");
    expect(screen.queryByText("Agent 1 transcript")).not.toBeInTheDocument();
    expect(within(taskPanel).getByText("Agent 2 progress")).toBeInTheDocument();
    expect(within(taskPanel).getByText("agent-2.txt")).toBeInTheDocument();
    expect(within(taskPanel).queryByText("Agent 1 progress")).not.toBeInTheDocument();
    expect(within(taskPanel).queryByText("agent-1.txt")).not.toBeInTheDocument();
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

    await screen.findByRole("button", { name: "Send" });

    fireEvent.click(screen.getByRole("button", { name: "Settings" }));

    await screen.findByText("Connection");
    expect(screen.getByText("http://127.0.0.1:8765")).toBeInTheDocument();
    expect(screen.getByText("/tmp/openppx_root")).toBeInTheDocument();
    expect(screen.getByText("This Mac (local)")).toBeInTheDocument();
    expect(screen.getByText("v1 / compatible")).toBeInTheDocument();
    expect(screen.getByText("0.5.0-beta.1")).toBeInTheDocument();
    expect(screen.getByText("0.4")).toBeInTheDocument();
  });

  it("renders desktop-owned interface copy in English", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.queryByText("Connections & Settings")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("Search sessions")).toBeInTheDocument();
    expect(screen.queryByText("工作区")).not.toBeInTheDocument();
  });

  it("uses a balanced settings grid without redundant helper copy", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));

    await screen.findByText("Runtime status");
    expect(document.querySelector(".settings-card-runtime")).toBeInTheDocument();
    expect(document.querySelector(".settings-card-config")).toBeInTheDocument();
    expect(document.querySelector(".settings-card-connection")).toBeInTheDocument();
    expect(document.querySelector(".settings-card-diagnostics")).toBeInTheDocument();
    expect(document.querySelector(".settings-card-paths")).toBeInTheDocument();
    expect(document.querySelectorAll(".settings-column")).toHaveLength(0);
    expect(screen.queryByText("detail")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh diagnostics" })).toBeInTheDocument();
  });

  it("renders LAN target diagnostics when provided", async () => {
    installClient({
      getDiagnostics: async () => ({
        ...buildDiagnostics(),
        mode: "lan",
        target: { id: "remote-default", type: "remote", name: "Ops Gateway" },
        clientApiManagedByClient: false,
        clientApiBaseUrl: "http://10.0.0.8:8765",
        clientApiProcessRunning: false,
      }),
    });

    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));

    await screen.findByText("Ops Gateway (lan)");
    expect(screen.getByText("external / LAN")).toBeInTheDocument();
  });

  it("saves connection settings from the settings form", async () => {
    const saveConnectionSettings = vi.fn(async () => ({
      ...buildDiagnostics(),
      mode: "lan" as const,
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

    await screen.findByRole("button", { name: "Send" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));

    fireEvent.change(screen.getByDisplayValue("This Mac"), {
      target: { value: "Ops Gateway" },
    });
    fireEvent.change(screen.getByDisplayValue("http://127.0.0.1:8765"), {
      target: { value: "http://10.0.0.8:8765" },
    });
    fireEvent.change(screen.getByLabelText("Run location"), {
      target: { value: "lan" },
    });
    fireEvent.change(screen.getByLabelText("Access Token"), {
      target: { value: "test-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save & apply" }));

    await waitFor(() => {
      expect(saveConnectionSettings).toHaveBeenCalledWith({
        targetType: "lan",
        targetId: "lan-ops-gateway",
        targetName: "Ops Gateway",
        clientApiBaseUrl: "http://10.0.0.8:8765",
        accessToken: "test-token",
      });
    });
  });

  it("tests a LAN connection without saving it", async () => {
    const testConnectionSettings = vi.fn(async () => ({
      ...buildDiagnostics(),
      mode: "lan" as const,
      target: { id: "lan-studio", type: "remote" as const, name: "Studio Node" },
      nodeName: "Studio Node",
    }));
    const saveConnectionSettings = vi.fn(async () => buildDiagnostics());
    installClient({ testConnectionSettings, saveConnectionSettings });

    render(<App />);
    await screen.findByRole("button", { name: "Send" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.change(screen.getByLabelText("Run location"), { target: { value: "lan" } });
    fireEvent.change(screen.getByDisplayValue("http://127.0.0.1:8765"), {
      target: { value: "http://192.168.1.8:8765" },
    });
    fireEvent.change(screen.getByLabelText("Access Token"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await screen.findByText(/Connection successful: Studio Node/);
    expect(testConnectionSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        targetType: "lan",
        clientApiBaseUrl: "http://192.168.1.8:8765",
        accessToken: "secret",
      }),
    );
    expect(saveConnectionSettings).not.toHaveBeenCalled();
  });
});
