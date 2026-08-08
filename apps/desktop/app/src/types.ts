import type {
  AgentProfile,
  ChatMessage,
  RunEvent,
  SendMessageInput,
  SessionSummary,
  ExtensionDetail,
  ExtensionPresentation,
  ExtensionSummary,
  ExtensionStarter,
  AgentEnablementKind,
  InstallableExtensionKind,
  ExtensionSourceRef,
  ExtensionPreview,
  ExtensionReadinessResult,
  ExtensionProbeResult,
  ExtensionHealthHistory,
  McpServerResource,
  McpValueBinding,
  AppConnectionDetail,
  AppCredentialSpec,
  AppToolSpec,
  ProjectedSlashCommand,
  SlashCommandResult,
  SetupApplyRequest,
  SetupApplyResult,
  SetupHelloResult,
  SetupStatusResult,
  OperationsOverviewResult,
  OperationsTaskListResult,
  OperationsTaskDetailResult,
  OperationsTaskControlInput,
  OperationsCronResult,
  OperationsHeartbeatResult,
  OperationsUsageResult,
  CronCreateInput,
  CronUpdateInput,
  HeartbeatConfiguration,
  ModelCatalogResult,
  ProviderAuthStatus,
  AgentCreateResult,
  AgentResourceSummary,
  AgentUpdateInput,
  ArtifactSummary,
  ArtifactUploadInput,
  ModelProfileResourceResult,
  ModelProfileCreateInput,
  ModelProfileUpdateInput,
  PluginHookStatus,
  PluginMarketplaceEntry,
  PluginMarketplaceSource,
  PluginMarketplaceSourceSpec,
  GoalDetail,
  AutomationCreateInput,
  AutomationDetail,
  AutomationRunSummary,
  AutomationStatus,
  AutomationSummary,
  AutomationTemplateSummary,
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
  ExtensionPresentation,
  ExtensionSummary,
  ExtensionStarter,
  PluginHookStatus,
  PluginMarketplaceEntry,
  PluginMarketplaceSource,
  PluginMarketplaceSourceSpec,
  AgentEnablementKind,
  InstallableExtensionKind,
  ExtensionSourceRef,
  ExtensionPreview,
  ExtensionReadinessResult,
  ExtensionProbeResult,
  ExtensionHealthHistory,
  McpServerResource,
  McpValueBinding,
  AppConnectionDetail,
  AppCredentialSpec,
  AppToolSpec,
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
  OperationsTaskItem,
  OperationsTaskListResult,
  OperationsTaskDetailResult,
  OperationsTaskControlAction,
  OperationsTaskControlInput,
  OperationsCronJob,
  OperationsCronResult,
  OperationsHeartbeatResult,
  OperationsUsageResult,
  CronScheduleInput,
  CronCreateInput,
  CronUpdateInput,
  HeartbeatConfiguration,
  ModelCatalogResult,
  ProviderAuthStatus,
  ProviderModel,
  AgentCreateResult,
  AgentResourceSummary,
  AgentUpdateInput,
  ArtifactSummary,
  ArtifactUploadInput,
  ModelCapability,
  ModelProfileDocument,
  ModelProfileResourceResult,
  ModelProfileCreateInput,
  ModelProfileUpdateInput,
  SetupProvider,
  GoalDetail,
  GoalStatus,
  GoalSummary,
  TaskFlowDetail,
  TaskFlowStep,
  AutomationCreateInput,
  AutomationDetail,
  AutomationRunSummary,
  AutomationStatus,
  AutomationSummary,
  AutomationTemplateSummary,
} from "@openppx/client";

export type RuntimeState =
  | "stopped"
  | "starting"
  | "reconnecting"
  | "needs_configuration"
  | "healthy"
  | "error";
export type DesktopPlatform = "macos" | "windows" | "linux" | "other";

/** Stable local human identity shared by Desktop and CLI control surfaces. */
export const LOCAL_USER_ID = "ppx-client-user";

export interface UserProfile {
  id: string;
  displayName: string;
  accountKind: "local" | "remote";
  avatarUrl?: string;
}

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

export interface ConnectionProfileSummary {
  targetType: "local" | "lan";
  targetId: string;
  targetName: string;
  clientApiBaseUrl: string;
  active: boolean;
  credentialConfigured: boolean;
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

export interface ExtensionPreviewRequest {
  kind: InstallableExtensionKind;
  source: ExtensionSourceRef;
}

export interface ExtensionInstallRequest extends ExtensionPreviewRequest {
  expectedDigest: string;
  expectedRevision: string | null;
}

export interface ExtensionRemoveRequest {
  kind: AgentEnablementKind;
  extensionId: string;
  expectedRevision: string;
}

export interface McpMutationRequest {
  resource: McpServerResource;
  secretValues: Record<string, string>;
  expectedRevision: string | null;
}

export interface McpOAuthStatus {
  serverId: string;
  status: "needs_auth" | "starting" | "authorizing" | "connected" | "error";
  authorizeUrl: string;
  error: string;
}

export interface AppConnectionSaveRequest {
  appId: string;
  connectionId: string;
  displayName: string;
  enabledTools: string[] | null;
  requireConfirmation: boolean;
  credentialValues: Record<string, string>;
  expectedRevision: string | null;
}

export interface AppConnectionEnablementRequest {
  connectionId: string;
  agentId: string;
  expectedRevision: string;
  enabled: boolean;
}

export interface AppConnectionRemoveRequest {
  connectionId: string;
  expectedRevision: string;
}

export interface ExtensionMutationResult extends Record<string, unknown> {
  revision?: string;
  status?: string;
  removed?: boolean;
}

export interface AgentCreateRequest {
  agentId: string;
  displayName: string;
  workspace: string | null;
  privilegeLevel: "low" | "medium" | "high" | "root";
  modelProfileId: string;
  instruction?: string;
}

export interface SessionMutationRequest {
  agentId: string;
  sessionId: string;
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
  displayName: string;
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

export interface OperationsDashboard {
  overview: OperationsOverviewResult;
  tasks: OperationsTaskListResult;
  cron: OperationsCronResult;
  heartbeat: OperationsHeartbeatResult;
  usage: OperationsUsageResult;
  audit: OperationsAuditItem[];
}

export interface AutomationUpdateRequest extends Record<string, unknown> {
  automationId: string;
  userId: string;
  expectedRevision: number;
}

export interface GoalUpdateRequest {
  goalId: string;
  expectedRevision: number;
  objective: string;
}

export type GoalTransitionOperation = "pause" | "resume" | "cancel";
export type GoalMutationOperation = "update" | "retry" | GoalTransitionOperation;

export interface DesktopHostPreferences {
  backgroundBehavior: "keep-running" | "confirm-before-close";
  notificationsEnabled: boolean;
  notificationSound: boolean;
}

export interface PpxClientApi {
  readonly platform: DesktopPlatform;
  bootstrap(): Promise<BootstrapPayload>;
  getUserProfile(): Promise<UserProfile>;
  getDiagnostics(): Promise<ClientDiagnostics>;
  setDesktopHostPreferences(preferences: DesktopHostPreferences): Promise<void>;
  testConnectionSettings(settings: ConnectionSettings): Promise<ClientDiagnostics>;
  saveConnectionSettings(settings: ConnectionSettings): Promise<ClientDiagnostics>;
  listConnectionProfiles(): Promise<{ profiles: ConnectionProfileSummary[] }>;
  activateConnectionProfile(targetId: string): Promise<ClientDiagnostics>;
  removeConnectionProfile(targetId: string): Promise<{ removed: boolean }>;
  runRuntimeCommand(command: RuntimeCommand): Promise<RuntimeStatus>;
  createAgent(input: AgentCreateRequest): Promise<AgentCreateResult>;
  listManagedAgents(): Promise<{ agents: AgentResourceSummary[] }>;
  updateAgent(input: AgentUpdateInput): Promise<AgentResourceSummary>;
  setAgentEnabled(agentId: string, enabled: boolean): Promise<AgentResourceSummary>;
  removeAgent(agentId: string, expectedRevision: string): Promise<Record<string, unknown>>;
  listSessions(agentId: string): Promise<{ sessions: SessionSummary[] }>;
  createSession(agentId: string): Promise<{ session: SessionSummary }>;
  renameSession(input: SessionMutationRequest & { title: string }): Promise<Record<string, unknown>>;
  archiveSession(input: SessionMutationRequest & { archived: boolean }): Promise<Record<string, unknown>>;
  forkSession(input: SessionMutationRequest): Promise<{ session: SessionSummary }>;
  exportSession(input: SessionMutationRequest): Promise<Record<string, unknown>>;
  deleteSession(input: SessionMutationRequest): Promise<Record<string, unknown>>;
  loadSession(sessionId: string): Promise<{ messages: ChatMessage[] }>;
  getCurrentGoal(sessionId: string): Promise<{ goal: GoalDetail | null }>;
  updateGoal(input: GoalUpdateRequest): Promise<GoalDetail>;
  transitionGoal(operation: GoalTransitionOperation, goalId: string, expectedRevision: number): Promise<GoalDetail>;
  retryGoalStep(goalId: string, expectedRevision: number, stepId?: string | null): Promise<GoalDetail>;
  listAutomations(statuses?: AutomationStatus[]): Promise<{ automations: AutomationSummary[] }>;
  getAutomation(automationId: string): Promise<AutomationDetail>;
  createAutomation(input: AutomationCreateInput): Promise<AutomationDetail>;
  updateAutomation(input: AutomationUpdateRequest): Promise<AutomationDetail>;
  transitionAutomation(operation: "pause" | "resume" | "delete", automationId: string, expectedRevision: number): Promise<Record<string, unknown>>;
  runAutomation(automationId: string, input?: Record<string, unknown>): Promise<AutomationRunSummary>;
  getAutomationHistory(automationId: string): Promise<Record<string, unknown>>;
  listAutomationTemplates(): Promise<{ templates: AutomationTemplateSummary[] }>;
  uploadArtifact(input: ArtifactUploadInput): Promise<ArtifactSummary>;
  listArtifacts(agentId: string, sessionId: string): Promise<{ artifacts: ArtifactSummary[] }>;
  downloadArtifact(agentId: string, sessionId: string, artifact: ArtifactSummary): Promise<{ dataBase64: string; mimeType: string }>;
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
  readModelProfile(profileId: string): Promise<ModelProfileResourceResult>;
  createModelProfile(input: ModelProfileCreateInput): Promise<ModelProfileResourceResult>;
  updateModelProfile(input: ModelProfileUpdateInput): Promise<ModelProfileResourceResult>;
  getOperationsOverview(): Promise<OperationsOverviewResult>;
  listOperationsAudit(limit?: number): Promise<{ items: OperationsAuditItem[] }>;
  getOperationsDashboard(): Promise<OperationsDashboard>;
  getOperationsTask(taskId: string): Promise<OperationsTaskDetailResult>;
  getOperationsTaskOutput(taskId: string): Promise<Record<string, unknown>>;
  controlOperationsTask(input: OperationsTaskControlInput): Promise<Record<string, unknown>>;
  createOperationsCron(input: CronCreateInput): Promise<Record<string, unknown>>;
  updateOperationsCron(input: CronUpdateInput): Promise<Record<string, unknown>>;
  setOperationsCronEnabled(jobId: string, enabled: boolean): Promise<Record<string, unknown>>;
  runOperationsCron(jobId: string): Promise<Record<string, unknown>>;
  removeOperationsCron(jobId: string): Promise<Record<string, unknown>>;
  runOperationsHeartbeat(): Promise<Record<string, unknown>>;
  configureOperationsHeartbeat(input: HeartbeatConfiguration): Promise<Record<string, unknown>>;
  listExtensions(): Promise<{ extensions: ExtensionSummary[] }>;
  listExtensionStarters(kind?: ExtensionSummary["kind"], query?: string): Promise<{ starters: ExtensionStarter[]; counts: Record<ExtensionSummary["kind"], number> }>;
  installAppStarter(starterId: string): Promise<ExtensionMutationResult>;
  getExtension(kind: ExtensionSummary["kind"], extensionId: string): Promise<{ extension: ExtensionDetail }>;
  getExtensionReadiness(kind: ExtensionSummary["kind"], extensionId: string): Promise<ExtensionReadinessResult>;
  getExtensionHealthHistory(kind: "mcp" | "app_connection", extensionId: string, limit?: number): Promise<ExtensionHealthHistory>;
  previewExtension(input: ExtensionPreviewRequest): Promise<ExtensionPreview>;
  installExtension(input: ExtensionInstallRequest): Promise<ExtensionMutationResult>;
  setExtensionAgentEnabled(input: ExtensionEnablementRequest): Promise<{ revision: string; status: string }>;
  removeExtension(input: ExtensionRemoveRequest): Promise<ExtensionMutationResult>;
  getPluginHookStatus(pluginId: string, expectedRevision: string): Promise<PluginHookStatus>;
  setPluginHookTrust(pluginId: string, expectedRevision: string, trusted: boolean): Promise<PluginHookStatus>;
  listPluginMarketplaces(): Promise<{ marketplaces: PluginMarketplaceSource[] }>;
  listPluginMarketplaceEntries(query?: string): Promise<{ entries: PluginMarketplaceEntry[] }>;
  savePluginMarketplace(input: { marketplaceId: string; spec: PluginMarketplaceSourceSpec; expectedRevision: string | null }): Promise<PluginMarketplaceSource>;
  refreshPluginMarketplace(marketplaceId: string, expectedRevision: string): Promise<PluginMarketplaceSource>;
  removePluginMarketplace(marketplaceId: string, expectedRevision: string): Promise<void>;
  createMcpServer(input: McpMutationRequest): Promise<ExtensionMutationResult>;
  updateMcpServer(input: McpMutationRequest): Promise<ExtensionMutationResult>;
  beginMcpOAuth(serverId: string): Promise<McpOAuthStatus>;
  getMcpOAuthStatus(serverId: string): Promise<McpOAuthStatus>;
  signOutMcpOAuth(serverId: string): Promise<McpOAuthStatus>;
  testMcpServer(serverId: string): Promise<ExtensionProbeResult>;
  saveAppConnection(input: AppConnectionSaveRequest): Promise<ExtensionMutationResult>;
  testAppConnection(connectionId: string): Promise<ExtensionProbeResult>;
  setAppConnectionAgentEnabled(input: AppConnectionEnablementRequest): Promise<ExtensionMutationResult>;
  removeAppConnection(input: AppConnectionRemoveRequest): Promise<ExtensionMutationResult>;
  onRunEvent(listener: (event: RunEvent) => void): () => void;
}
