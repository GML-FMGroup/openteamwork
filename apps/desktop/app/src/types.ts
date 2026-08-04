import type {
  AgentProfile,
  ChatMessage,
  RunEvent,
  SendMessageInput,
  SessionSummary,
  ExtensionDetail,
  ExtensionSummary,
  AgentEnablementKind,
  ProjectedSlashCommand,
  SlashCommandResult,
  SetupApplyRequest,
  SetupApplyResult,
  SetupHelloResult,
  SetupStatusResult,
  OperationsOverviewResult,
  ModelCatalogResult,
  ProviderAuthStatus,
  AgentCreateResult,
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
  ExtensionDetail,
  ExtensionSummary,
  AgentEnablementKind,
  ProjectedSlashCommand,
  SlashCommandResult,
  SetupApplyRequest,
  SetupApplyResult,
  SetupHelloResult,
  SetupStatusResult,
  HealthComponent,
  HealthState,
  OperationsHealthResult,
  OperationsOverviewResult,
  ModelCatalogResult,
  ProviderAuthStatus,
  ProviderModel,
  AgentCreateResult,
} from "@openppx/client";

export type RuntimeState = "stopped" | "starting" | "reconnecting" | "healthy" | "error";

/** Stable local human identity shared by Desktop and CLI control surfaces. */
export const LOCAL_USER_ID = "ppx-client-user";

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
  mode: "local" | "lan";
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
  agentCount: number;
  sessionCacheEntries: number;
  messageCacheEntries: number;
  debugEnabled: boolean;
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

export interface ExtensionEnablementRequest {
  kind: AgentEnablementKind;
  extensionId: string;
  agentId: string;
  expectedRevision: string;
  enabled: boolean;
}

export interface AgentCreateRequest {
  agentId: string;
  displayName: string;
  workspace: string | null;
  privilegeLevel: "low" | "medium" | "high" | "root";
  modelProfileId: string;
}

export interface SlashCommandRequest {
  rawCommand: string;
  agentId?: string | null;
  sessionId?: string | null;
  runId?: string | null;
}

export interface SetupForm {
  nodeId: string;
  nodeName: string;
  agentId: string;
  agentName: string;
  workspace: string;
  ownerPrincipalId: string;
  privilegeLevel: "low" | "medium" | "high" | "root";
  profileId: string;
  provider: string;
  model: string;
  executionLocation: "local" | "remote";
  credentialName: string;
  apiKey: string;
  hello: string;
}

export interface ModelProfileSummary {
  id: string;
  revision: string;
  provider: string;
  model: string;
  enabled: boolean;
  credentialState: string;
}

export interface OperationsAuditItem {
  id: string;
  recordedAt: string;
  completedAt: string | null;
  actorId: string;
  actionId: string;
  risk: "low" | "medium" | "high";
  decisionCode: string;
  outcomeCode: string | null;
  ok: boolean | null;
}

export interface PpxClientApi {
  bootstrap(): Promise<BootstrapPayload>;
  getDiagnostics(): Promise<ClientDiagnostics>;
  testConnectionSettings(settings: ConnectionSettings): Promise<ClientDiagnostics>;
  saveConnectionSettings(settings: ConnectionSettings): Promise<ClientDiagnostics>;
  runRuntimeCommand(command: RuntimeCommand): Promise<RuntimeStatus>;
  createAgent(input: AgentCreateRequest): Promise<AgentCreateResult>;
  listSessions(agentId: string): Promise<{ sessions: SessionSummary[] }>;
  createSession(agentId: string): Promise<{ session: SessionSummary }>;
  loadSession(sessionId: string): Promise<{ messages: ChatMessage[] }>;
  sendMessage(input: SendMessageInput): Promise<{ runId: string }>;
  cancelRun(runId: string): Promise<{ runId: string; status: "cancelled" }>;
  listSlashCommands(): Promise<{ commands: ProjectedSlashCommand[] }>;
  invokeSlashCommand(input: SlashCommandRequest): Promise<SlashCommandResult>;
  getSetupStatus(): Promise<SetupStatusResult>;
  applySetup(request: SetupApplyRequest): Promise<SetupApplyResult>;
  runSetupHello(agentId: string, userId: string, text: string): Promise<SetupHelloResult>;
  getProviderModels(providerId: string): Promise<ModelCatalogResult>;
  getProviderAuthStatus(providerId: string): Promise<ProviderAuthStatus>;
  beginProviderAuth(providerId: string): Promise<ProviderAuthStatus>;
  refreshProviderAuth(providerId: string): Promise<ProviderAuthStatus>;
  openExternalUrl(url: string): Promise<void>;
  listModelProfiles(): Promise<{ profiles: ModelProfileSummary[] }>;
  getOperationsOverview(): Promise<OperationsOverviewResult>;
  listOperationsAudit(limit?: number): Promise<{ items: OperationsAuditItem[] }>;
  listExtensions(): Promise<{ extensions: ExtensionSummary[] }>;
  getExtension(kind: ExtensionSummary["kind"], extensionId: string): Promise<{ extension: ExtensionDetail }>;
  setExtensionAgentEnabled(input: ExtensionEnablementRequest): Promise<{ revision: string; status: string }>;
  onRunEvent(listener: (event: RunEvent) => void): () => void;
}
