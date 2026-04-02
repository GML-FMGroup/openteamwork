import type {
  AgentProfile,
  BootstrapPayload,
  ChatMessage,
  ClientDiagnostics,
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
      text: `我已经收到任务：**${trimmed || "未命名任务"}**。\n\n第一版客户端目前通过本地模式连接运行时，所以我会先在本机完成执行与反馈。`,
    },
    {
      type: "step_ref",
      stepId: `step-${crypto.randomUUID()}`,
      title: "Analyze request",
      status: "completed",
      detail: "Parsed the request and selected the local-only execution path.",
    },
    {
      type: "code",
      language: "bash",
      text: "ppx client-api serve --mode local",
    },
    {
      type: "file",
      text: "Planned artifact",
      fileName: "client_session_notes.md",
      mimeType: "text/markdown",
      sizeBytes: 2048,
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
    title: "New local session",
    updatedAt: now(),
    lastMessagePreview: "Start a task for this agent.",
  };
  state.sessionsByAgent[agentId] = [session, ...(state.sessionsByAgent[agentId] ?? [])];
  state.messagesBySession[session.id] = [];
  state.selectedAgentId = agentId;
  state.selectedSessionId = session.id;
  return { session };
}

export async function loadSession(sessionId: string): Promise<{ messages: ChatMessage[] }> {
  return { messages: getMessages(sessionId) };
}

export async function getDiagnostics(): Promise<ClientDiagnostics> {
  return {
    mode: "mock",
    openppxRoot: "",
    openppxRootExists: false,
    pythonBin: "",
    globalConfigPath: "",
    globalConfigExists: false,
    clientApiBaseUrl: "http://127.0.0.1:8765",
    clientApiHealthy: false,
    clientApiProcessRunning: false,
    bridgeScriptPath: "",
    bridgeScriptExists: false,
    agentCount: state.agents.length,
    sessionCacheEntries: 0,
    messageCacheEntries: 0,
    debugEnabled: false,
  };
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
