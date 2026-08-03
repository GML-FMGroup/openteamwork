import type {
  AgentProfile,
  ChatMessage,
  RunEvent,
  SendMessageInput,
  SessionSummary,
} from "@openppx/client";

export type {
  AgentProfile,
  ChatMessage,
  MessagePart,
  MessageRole,
  MessageStatus,
  RunEvent,
  SendMessageInput,
  SessionSummary,
} from "@openppx/client";

export type RuntimeState = "stopped" | "starting" | "reconnecting" | "healthy" | "error";

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
  desktopVersion?: string;
  mode: "local" | "lan" | "mock";
  target: ConnectionTarget;
  openppxRoot: string;
  openppxRootExists: boolean;
  pythonBin: string;
  clientApiBaseUrl: string;
  clientApiManagedByClient: boolean;
  clientApiHealthy: boolean;
  clientApiProductVersion?: string;
  clientApiProtocolVersion?: number;
  clientApiCompatibility?: "compatible" | "incompatible" | "unknown";
  clientApiLastError?: string;
  clientApiAuthState?: "authenticated" | "not-required" | "missing" | "unauthorized" | "unknown";
  clientApiCredentialConfigured?: boolean;
  nodeId?: string;
  nodeName?: string;
  clientApiProcessRunning: boolean;
  bridgeScriptPath: string;
  bridgeScriptExists: boolean;
  agentCount: number;
  sessionCacheEntries: number;
  messageCacheEntries: number;
  debugEnabled: boolean;
  mockEnabled?: boolean;
  legacyBridgeEnabled?: boolean;
}

export interface ConnectionSettings {
  targetType: "local" | "lan";
  targetId: string;
  targetName: string;
  clientApiBaseUrl: string;
  accessToken?: string;
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

export interface PpxClientApi {
  bootstrap(): Promise<BootstrapPayload>;
  getDiagnostics(): Promise<ClientDiagnostics>;
  testConnectionSettings(settings: ConnectionSettings): Promise<ClientDiagnostics>;
  saveConnectionSettings(settings: ConnectionSettings): Promise<ClientDiagnostics>;
  runRuntimeCommand(command: RuntimeCommand): Promise<RuntimeStatus>;
  listSessions(agentId: string): Promise<{ sessions: SessionSummary[] }>;
  createSession(agentId: string): Promise<{ session: SessionSummary }>;
  loadSession(sessionId: string): Promise<{ messages: ChatMessage[] }>;
  sendMessage(input: SendMessageInput): Promise<{ runId: string }>;
  cancelRun(runId: string): Promise<{ runId: string; status: "cancelled" }>;
  onRunEvent(listener: (event: RunEvent) => void): () => void;
}
