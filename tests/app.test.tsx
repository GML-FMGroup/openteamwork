import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
      lastMessagePreview: "A",
    },
    {
      id: "session-b",
      agentId: "agent-1",
      title: "Session B",
      updatedAt: "2026-04-02T09:00:00.000Z",
      lastMessagePreview: "B",
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
    globalConfigPath: "/tmp/.openpipixia/global_config.json",
    globalConfigExists: true,
    clientApiBaseUrl: "http://127.0.0.1:8765",
    clientApiManagedByClient: true,
    clientApiHealthy: true,
    clientApiProcessRunning: true,
    bridgeScriptPath: "/tmp/ppx-client/scripts/openppx_bridge.py",
    bridgeScriptExists: true,
    agentCount: 1,
    sessionCacheEntries: 1,
    messageCacheEntries: 1,
    debugEnabled: false,
  };
}

function installClient(overrides: Partial<PpxClientApi> = {}): { client: PpxClientApi; emit: (event: RunEvent) => void } {
  let listener: ((event: RunEvent) => void) | null = null;
  const client: PpxClientApi = {
    bootstrap: async () => buildBootstrapPayload(),
    getDiagnostics: async () => buildDiagnostics(),
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
  it("only marks the active session as running", async () => {
    installClient();

    render(<App />);

    await screen.findByText("openppx workbench");

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
      expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
    });
  });

  it("clears the current session sending state after a completed message event", async () => {
    const { emit } = installClient();

    render(<App />);

    await screen.findByText("openppx workbench");

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

    await screen.findByText("openppx workbench");

    fireEvent.click(screen.getByRole("button", { name: "设置" }));

    await screen.findByText("Connection");
    expect(screen.getByText("http://127.0.0.1:8765")).toBeInTheDocument();
    expect(screen.getByText("/tmp/openppx_root")).toBeInTheDocument();
    expect(screen.getByText("This Mac (local)")).toBeInTheDocument();
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

    await screen.findByText("openppx workbench");
    fireEvent.click(screen.getByRole("button", { name: "设置" }));

    await screen.findByText("Ops Gateway (remote)");
    expect(screen.getByText("external / remote")).toBeInTheDocument();
  });
});
