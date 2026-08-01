import type {
  AgentProfile,
  BootstrapPayload,
  ChatMessage,
  ClientDiagnostics,
  ConnectionSettings,
  MessagePart,
  RuntimeCommand,
  RuntimeStatus,
  RunEvent,
  SendMessageInput,
  SessionSummary,
} from "../types";

interface StoreState {
  runtime: RuntimeStatus;
  agents: AgentProfile[];
  sessionsByAgent: Record<string, SessionSummary[]>;
  messagesBySession: Record<string, ChatMessage[]>;
  selectedAgentId: string;
  selectedSessionId: string;
}

type EventSink = (event: RunEvent) => void;

const now = () => new Date().toISOString();

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

const firstAgentId = "builder";
const firstSessionId = "builder-session-1";

const state: StoreState = {
  runtime: {
    target: {
      id: "local-default",
      type: "local",
      name: "This Mac",
    },
    state: "healthy",
    summary: "Local openppx runtime is ready.",
    detail: "Renderer talks to Electron host API. Runtime adapter is local-only in v1.",
  },
  agents: [
    {
      id: "builder",
      name: "Builder",
      description: "General local execution agent for planning and implementation.",
      enabled: true,
      status: "healthy",
      tags: ["default", "coding"],
    },
    {
      id: "operator",
      name: "Operator",
      description: "Operational agent focused on diagnostics and orchestration.",
      enabled: true,
      status: "idle",
      tags: ["ops", "runtime"],
    },
  ],
  sessionsByAgent: {
    builder: [
      {
        id: firstSessionId,
        agentId: "builder",
        title: "Build the first ppx-client shell",
        updatedAt: now(),
        lastMessagePreview: "Start from a local-first Electron desktop shell.",
      },
    ],
    operator: [
      {
        id: "operator-session-1",
        agentId: "operator",
        title: "Runtime diagnostics",
        updatedAt: now(),
        lastMessagePreview: "Local runtime is healthy and waiting.",
      },
    ],
  },
  messagesBySession: {
    [firstSessionId]: [
      {
        id: "msg-welcome",
        sessionId: firstSessionId,
        role: "assistant",
        status: "completed",
        createdAt: now(),
        parts: [
          {
            type: "markdown",
            text: "### Ready for local mode\n\nChoose an agent, open a session, and send a task. This first version keeps the machine model but runs everything locally.",
          },
          {
            type: "step_ref",
            stepId: "boot-local",
            title: "Local adapter online",
            status: "completed",
            detail: "The initial client uses an Electron-hosted local adapter so the UI contract stays stable while the real runtime API is added.",
          },
        ],
      },
    ],
    "operator-session-1": [
      {
        id: "msg-operator",
        sessionId: "operator-session-1",
        role: "assistant",
        status: "completed",
        createdAt: now(),
        parts: [
          {
            type: "markdown",
            text: "Runtime diagnostics are available here. The first version focuses on status, sessions, and chat.",
          },
        ],
      },
    ],
  },
  selectedAgentId: firstAgentId,
  selectedSessionId: firstSessionId,
};

let listeners = new Set<EventSink>();

function emit(event: RunEvent): void {
  listeners.forEach((listener) => listener(event));
}

function getSessions(agentId: string): SessionSummary[] {
  return [...(state.sessionsByAgent[agentId] ?? [])].sort((left, right) =>
    right.updatedAt.localeCompare(left.updatedAt),
  );
}

function getMessages(sessionId: string): ChatMessage[] {
  return [...(state.messagesBySession[sessionId] ?? [])];
}

function createAssistantMessage(sessionId: string): ChatMessage {
  return {
    id: `assistant-${crypto.randomUUID()}`,
    sessionId,
    role: "assistant",
    status: "streaming",
    createdAt: now(),
    parts: [],
  };
}

function formatReplyParts(text: string): MessagePart[] {
  const trimmed = text.trim();
  return [
    {
      type: "markdown",
      text: `I received the task: **${trimmed || "Untitled task"}**.\n\nThe desktop client is currently connected in local mode, so execution and feedback will run on this computer.`,
    },
    {
      type: "step_ref",
      stepId: `step-${crypto.randomUUID()}`,
      title: "Analyze request",
      status: "completed",
      detail: "Parsed the request and selected the local-only execution path.",
    },
    {
      type: "tool_result",
      toolName: "client_api_hint",
      summary: "The local client API launch command is ready.",
      detail: "This mock response demonstrates how tool results appear in the desktop client.",
      rawText: JSON.stringify({ command: "ppx client-api serve --host 127.0.0.1 --port 8765" }, null, 2),
    },
    {
      type: "file",
      text: "Planned artifact",
      fileName: "client_session_notes.md",
      mimeType: "text/markdown",
      sizeBytes: 2048,
    },
    {
      type: "image",
      text: "Runtime architecture preview",
      url:
        "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='640' height='360'><rect width='100%25' height='100%25' rx='28' fill='%23f3f4f6'/><rect x='28' y='28' width='180' height='64' rx='18' fill='%23ffffff' stroke='%23d1d5db'/><text x='48' y='68' font-size='22' fill='%23111827'>Client</text><rect x='230' y='120' width='180' height='64' rx='18' fill='%23ffffff' stroke='%23d1d5db'/><text x='250' y='160' font-size='22' fill='%23111827'>Gateway</text><rect x='432' y='212' width='180' height='64' rx='18' fill='%23ffffff' stroke='%23d1d5db'/><text x='452' y='252' font-size='22' fill='%23111827'>Agent</text><path d='M208 60 C250 80 250 120 230 152' stroke='%236b7280' stroke-width='4' fill='none'/><path d='M410 152 C452 174 452 212 432 244' stroke='%236b7280' stroke-width='4' fill='none'/></svg>",
      mimeType: "image/svg+xml",
    },
  ];
}

export async function bootstrap(): Promise<BootstrapPayload> {
  return {
    runtime: state.runtime,
    agents: [...state.agents],
    sessions: getSessions(state.selectedAgentId),
    messages: getMessages(state.selectedSessionId),
    selectedAgentId: state.selectedAgentId,
    selectedSessionId: state.selectedSessionId,
  };
}

export async function runRuntimeCommand(command: RuntimeCommand): Promise<RuntimeStatus> {
  if (command === "start") {
    state.runtime = {
      ...state.runtime,
      state: "healthy",
      summary: "Local openppx runtime is running.",
      detail: "The local adapter is ready to serve sessions and message streams.",
      lastError: undefined,
    };
  } else if (command === "stop") {
    state.runtime = {
      ...state.runtime,
      state: "stopped",
      summary: "Local openppx runtime is stopped.",
      detail: "Start it again from the runtime card to continue chatting.",
    };
  } else {
    state.runtime = {
      ...state.runtime,
      state: "healthy",
      summary: "Local openppx runtime restarted.",
      detail: "The runtime card restarted the local adapter successfully.",
      lastError: undefined,
    };
  }
  return state.runtime;
}

export async function createSession(agentId: string): Promise<{ session: SessionSummary }> {
  const session: SessionSummary = {
    id: `${agentId}-${crypto.randomUUID()}`,
    agentId,
    title: "New chat",
    updatedAt: now(),
    lastMessagePreview: "",
  };
  state.sessionsByAgent[agentId] = [session, ...(state.sessionsByAgent[agentId] ?? [])];
  state.messagesBySession[session.id] = [];
  state.selectedAgentId = agentId;
  state.selectedSessionId = session.id;
  return { session };
}

export async function listSessions(agentId: string): Promise<{ sessions: SessionSummary[] }> {
  return { sessions: getSessions(agentId) };
}

export async function loadSession(sessionId: string): Promise<{ messages: ChatMessage[] }> {
  return { messages: getMessages(sessionId) };
}

export async function getDiagnostics(): Promise<ClientDiagnostics> {
  return {
    mode: "mock",
    target: { id: "mock-default", type: "local", name: "Mock Runtime" },
    openppxRoot: "",
    openppxRootExists: false,
    pythonBin: "",
    globalConfigPath: "",
    globalConfigExists: false,
    clientApiBaseUrl: "http://127.0.0.1:8765",
    clientApiManagedByClient: false,
    clientApiHealthy: false,
    clientApiCompatibility: "unknown",
    clientApiAuthState: "unknown",
    clientApiCredentialConfigured: false,
    clientApiProcessRunning: false,
    bridgeScriptPath: "",
    bridgeScriptExists: false,
    agentCount: state.agents.length,
    sessionCacheEntries: 0,
    messageCacheEntries: 0,
    debugEnabled: false,
    mockEnabled: true,
    legacyBridgeEnabled: false,
  };
}

export async function saveConnectionSettings(_settings: ConnectionSettings): Promise<ClientDiagnostics> {
  return getDiagnostics();
}

export async function testConnectionSettings(_settings: ConnectionSettings): Promise<ClientDiagnostics> {
  return getDiagnostics();
}

export function subscribe(listener: EventSink): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export async function sendMessage(input: SendMessageInput): Promise<{ runId: string }> {
  const runId = `run-${crypto.randomUUID()}`;
  const userMessage: ChatMessage = {
    id: `user-${crypto.randomUUID()}`,
    sessionId: input.sessionId,
    role: "user",
    status: "completed",
    createdAt: now(),
    parts: [{ type: "markdown", text: input.text }],
  };
  const sessionList = state.sessionsByAgent[input.agentId] ?? [];
  const session = sessionList.find((item) => item.id === input.sessionId);
  if (session) {
    session.updatedAt = now();
    if (isGenericSessionTitle(session.title)) {
      session.title = compactSessionTitle(input.text);
    }
    session.lastMessagePreview = input.text.trim() || "Empty message";
  }
  state.messagesBySession[input.sessionId] = [...getMessages(input.sessionId), userMessage];
  const assistant = createAssistantMessage(input.sessionId);
  state.messagesBySession[input.sessionId] = [...state.messagesBySession[input.sessionId], assistant];
  emit({
    type: "message.created",
    runId,
    sessionId: input.sessionId,
    message: assistant,
  });

  const parts = formatReplyParts(input.text);
  parts.forEach((part, index) => {
    setTimeout(() => {
      emit({
        type: "message.updated",
        runId,
        sessionId: input.sessionId,
        messageId: assistant.id,
        appendParts: [part],
        status: index === parts.length - 1 ? "completed" : "streaming",
      });
      if (index === parts.length - 1 && session) {
        session.updatedAt = now();
        session.lastMessagePreview = "Assistant replied with local runtime guidance.";
        emit({
          type: "session.updated",
          runId,
          session: { ...session },
        });
        emit({
          type: "run.finished",
          runId,
          sessionId: input.sessionId,
        });
      }
    }, 250 * (index + 1));
  });

  return { runId };
}
