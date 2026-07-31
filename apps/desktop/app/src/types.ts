export type RuntimeState = "stopped" | "starting" | "healthy" | "error";

export type MessageRole = "user" | "assistant" | "system" | "tool";

export type MessageStatus = "streaming" | "completed" | "failed" | "cancelled";

export type MessagePart =
  | { type: "markdown"; text: string }
  | { type: "code"; text: string; language?: string }
  | { type: "file"; text: string; fileName: string; sizeBytes?: number; mimeType?: string }
  | { type: "image"; text: string; url: string; mimeType?: string }
  | { type: "error"; text: string; errorCode?: string }
  | { type: "tool_result"; toolName: string; summary: string; detail?: string; rawText?: string }
  | { type: "step_ref"; stepId: string; title: string; status: "running" | "completed" | "failed"; detail: string };

export interface ConnectionTarget {
  id: string;
  type: "local" | "remote";
  name: string;
}

export interface RuntimeStatus {
  target: ConnectionTarget;
  state: RuntimeState;
  summary: string;
  detail?: string;
  lastError?: string;
}

export interface ClientDiagnostics {
  mode: "local" | "remote" | "mock";
  target: ConnectionTarget;
  openppxRoot: string;
  openppxRootExists: boolean;
  pythonBin: string;
  globalConfigPath: string;
  globalConfigExists: boolean;
  clientApiBaseUrl: string;
  clientApiManagedByClient: boolean;
  clientApiHealthy: boolean;
  clientApiProcessRunning: boolean;
  bridgeScriptPath: string;
  bridgeScriptExists: boolean;
  agentCount: number;
  sessionCacheEntries: number;
  messageCacheEntries: number;
  debugEnabled: boolean;
}

export interface ConnectionSettings {
  targetType: "local" | "remote";
  targetId: string;
  targetName: string;
  clientApiBaseUrl: string;
}

export interface AgentProfile {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  status: "healthy" | "idle" | "starting" | "disabled";
  tags: string[];
}

export interface SessionSummary {
  id: string;
  agentId: string;
  title: string;
  updatedAt: string;
  lastMessagePreview: string;
}

export interface ChatMessage {
  id: string;
  sessionId: string;
  role: MessageRole;
  status: MessageStatus;
  createdAt: string;
  parts: MessagePart[];
}

export interface BootstrapPayload {
  runtime: RuntimeStatus;
  agents: AgentProfile[];
  sessions: SessionSummary[];
  messages: ChatMessage[];
  selectedAgentId: string;
  selectedSessionId: string;
}

export type RuntimeCommand = "start" | "stop" | "restart";

export type RunEvent =
  | {
      type: "message.created";
      runId: string;
      sessionId: string;
      message: ChatMessage;
    }
  | {
      type: "message.updated";
      runId: string;
      sessionId: string;
      messageId: string;
      status?: MessageStatus;
      appendParts?: MessagePart[];
      replaceParts?: MessagePart[];
    }
  | {
      type: "session.updated";
      runId: string;
      session: SessionSummary;
    }
  | {
      type: "run.finished";
      runId: string;
      sessionId: string;
    };

export interface SendMessageInput {
  agentId: string;
  sessionId: string;
  text: string;
}

export interface PpxClientApi {
  bootstrap(): Promise<BootstrapPayload>;
  getDiagnostics(): Promise<ClientDiagnostics>;
  saveConnectionSettings(settings: ConnectionSettings): Promise<ClientDiagnostics>;
  runRuntimeCommand(command: RuntimeCommand): Promise<RuntimeStatus>;
  listSessions(agentId: string): Promise<{ sessions: SessionSummary[] }>;
  createSession(agentId: string): Promise<{ session: SessionSummary }>;
  loadSession(sessionId: string): Promise<{ messages: ChatMessage[] }>;
  sendMessage(input: SendMessageInput): Promise<{ runId: string }>;
  onRunEvent(listener: (event: RunEvent) => void): () => void;
}
