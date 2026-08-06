import { CLIENT_API_PROTOCOL_VERSION } from "./contract";
import { ClientApiProtocolError } from "./contract";
import { ClientApiHttpTransport, type ClientApiHttpTransportOptions } from "./http-transport";
import { ExtensionClient } from "./extensions";
import { ArtifactClient } from "./artifacts";

export type ActionScope = "node" | "agent" | "session" | "run" | "task" | "goal" | "flow" | "automation" | "extension";
export type ActionRisk = "low" | "medium" | "high";
export type ActionProjection = "cli" | "slash" | "desktop" | "mobile";
export type SlashCommandLifecycle = "side_channel" | "finalize_active_turn" | "stop_active_turn" | "agent_turn";

export interface SlashCommandArgumentItem {
  name: string;
  valueType: "string" | "text" | "integer" | "boolean" | "enum" | "resource_id";
  description: string;
  required: boolean;
  choices: string[];
}

export interface SlashCommandItem {
  command: string;
  title: string;
  description: string;
  icon: string;
  argHint: string;
  lifecycle: SlashCommandLifecycle;
  acceptsArgs: boolean;
  arguments: SlashCommandArgumentItem[];
  noArgsBehavior: "invoke" | "show_usage";
  usage: string;
  order: number;
}

export interface ActionCatalogItem {
  actionId: string;
  namespace: string;
  title: string;
  description: string;
  scope: ActionScope;
  inputSchema: Record<string, unknown>;
  requiredCapabilities: string[];
  permission: string;
  risk: ActionRisk;
  confirmation: "never" | "required";
  execution: "sync" | "long_running";
  operation: "read" | "mutation";
  previewActionId: string | null;
  successPresentation: "inline" | "panel" | "navigate" | "toast";
  projections: ActionProjection[];
  slashCommands: SlashCommandItem[];
  available: boolean;
  availabilityReason: string | null;
}

export interface ActionEnvelope<TResult extends Record<string, unknown>> {
  protocolVersion: number;
  requestId: string;
  correlationId: string;
  ok: true;
  result: TResult;
}

export interface ActionInvocationOptions {
  confirmed?: boolean;
  requestId?: string;
  correlationId?: string;
}

export interface SessionNewResult extends Record<string, unknown> {
  session: {
    id: string;
    agentId: string;
    subjectPrincipalId: string;
    title: string;
    updatedAt: string;
    lastMessagePreview: string;
    archived: boolean;
  };
}

export interface RunStopResult extends Record<string, unknown> {
  run: {
    id: string;
    agentId: string;
    sessionId: string;
    snapshotRevision: string;
    startedAt: string;
    state: string;
  };
}

export interface SlashCommandContext {
  userId: string;
  agentId?: string | null;
  sessionId?: string | null;
  runId?: string | null;
}

export interface SlashCommandResult extends Record<string, unknown> {
  command: string;
  lifecycle: SlashCommandLifecycle;
  targetActionId: string;
  result: Record<string, unknown>;
}

export type GoalStatus = "active" | "waiting" | "paused" | "blocked" | "completed" | "cancelled" | "failed";

export interface TaskFlowStep extends Record<string, unknown> {
  stepId: string;
  title: string;
  description: string;
  status: string;
  dependsOn: string[];
  completionCriteria: string[];
}

export interface TaskFlowDetail extends Record<string, unknown> {
  flowId: string;
  goalId: string;
  status: string;
  revision: number;
  steps: TaskFlowStep[];
  taskRunRefs: Array<Record<string, unknown>>;
  artifactRefs: Array<Record<string, unknown>>;
  waitReason: Record<string, unknown>;
  recoveryState: Record<string, unknown>;
  lastEvent: string;
  createdAtMs: number;
  updatedAtMs: number;
}

export interface GoalSummary extends Record<string, unknown> {
  goalId: string;
  sessionId: string;
  agentId: string;
  userId: string;
  objective: string;
  status: GoalStatus;
  revision: number;
  activeFlowId: string;
  completionCriteria: string[];
  budgetState: Record<string, unknown>;
  createdAtMs: number;
  updatedAtMs: number;
  completedAtMs: number | null;
  cancelledAtMs: number | null;
}

export interface GoalDetail extends GoalSummary {
  workspaceRef: string;
  constraints: string[];
  budgetPolicy: Record<string, unknown>;
  permissionRevision: string;
  modelProfileRevision: string;
  extensionSnapshotDigest: string;
  completionEvidence: Array<Record<string, unknown>>;
  correlationId: string;
  createdBy: string;
  flow: TaskFlowDetail | null;
}

export type AutomationStatus = "active" | "paused" | "blocked";
export type AutomationRunStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "skipped" | "blocked";

export interface AutomationTriggerView extends Record<string, unknown> {
  triggerId: string;
  type: "schedule" | "local_event";
  enabled: boolean;
  schedule: {
    kind: "every" | "cron" | "at";
    everySeconds: number | null;
    cronExpr: string;
    atMs: number | null;
    timezone: string;
  } | null;
  eventKey: string;
  inputSchema: Record<string, unknown>;
  nextRunAtMs: number | null;
  lastRunAtMs: number | null;
}

export interface AutomationRunSummary extends Record<string, unknown> {
  automationRunId: string;
  automationId: string;
  definitionRevision: number;
  triggerType: string;
  triggerOccurrenceId: string;
  status: AutomationRunStatus;
  attempt: number;
  sessionId: string;
  adkRunId: string;
  outputSummary: string;
  errorSummary: string;
  blockedReason: string;
  createdAtMs: number;
  startedAtMs: number | null;
  endedAtMs: number | null;
}

export interface AutomationSummary extends Record<string, unknown> {
  automationId: string;
  name: string;
  description: string;
  status: AutomationStatus;
  agentId: string;
  userId: string;
  revision: number;
  trigger: AutomationTriggerView | null;
  latestRun: AutomationRunSummary | null;
  createdAtMs: number;
  updatedAtMs: number;
}

export interface AutomationDetail extends AutomationSummary {
  instructions: string;
  outputRequirements: string[];
  workspaceRef: string;
  contextMode: "isolated" | "rolling" | "session";
  modelProfileRef: string;
  extensionPolicy: Record<string, unknown>;
  permissionPolicy: Record<string, unknown>;
  deliveryPolicy: Record<string, unknown>;
  concurrencyPolicy: Record<string, unknown>;
  missedRunPolicy: Record<string, unknown>;
  retryPolicy: Record<string, unknown>;
  budgetPolicy: Record<string, unknown>;
  monitorPolicy: Record<string, unknown>;
  readiness: { ready: boolean; reasons: Array<{ code: string; message: string }> };
}

export interface AutomationTemplateSummary extends Record<string, unknown> {
  templateId: string;
  name: string;
  description: string;
  instructions: string;
  outputRequirements: string[];
  recommendedSchedule: Record<string, unknown>;
  requiredExtensions: string[];
  deliveryHint: string;
  behavior: "task" | "monitor";
  provenance: string;
  version: number;
}

export interface AutomationCreateInput extends Record<string, unknown> {
  userId: string;
  agentId: string;
  name: string;
  description?: string;
  instructions: string;
  outputRequirements?: string[];
  workspaceRef?: string;
  contextMode?: "isolated" | "rolling" | "session";
  modelProfileRef?: string | null;
  extensionPolicy?: Record<string, unknown>;
  permissionPolicy?: Record<string, unknown>;
  permissionsConfirmed?: boolean;
  deliveryPolicy?: Record<string, unknown>;
  concurrencyPolicy?: Record<string, unknown>;
  missedRunPolicy?: Record<string, unknown>;
  retryPolicy?: Record<string, unknown>;
  budgetPolicy?: Record<string, unknown>;
  monitorPolicy?: Record<string, unknown>;
  schedule?: Record<string, unknown> | null;
  localEvent?: {
    eventKey: string;
    inputSchema: Record<string, unknown>;
  } | null;
}

export interface ProjectedSlashCommand extends SlashCommandItem {
  actionId: string;
  available: boolean;
  availabilityReason: string | null;
}

export interface ModelSelectionInput extends Record<string, unknown> {
  agentId: string;
  role?: "fast" | "reasoning" | "vision";
  runOverride?: string;
  requiredCapabilities?: string[];
  privacy?: "any" | "local_only";
  minContextTokens?: number;
  maxInputCostPerMillionUsd?: string;
  maxOutputCostPerMillionUsd?: string;
}

export interface SessionModelSelectionResult extends Record<string, unknown> {
  items: Array<Record<string, unknown>>;
  sessionSelection: {
    profileId: string | null;
    revision: number;
  };
  effectiveSelection: Record<string, unknown>;
  effect: "next_run";
}

export interface SetupProvider {
  id: string;
  displayName: string;
  runtime: string;
  credentialMode: "api_key" | "oauth" | "none";
  credentialRequired: boolean;
  defaultModel: string;
}

export type ModelCapability =
  | "text"
  | "vision"
  | "audio_input"
  | "audio_output"
  | "tool_calling"
  | "structured_output"
  | "reasoning"
  | "long_context";

export interface ModelSecretRef {
  store: "system";
  name: string;
}

export interface ModelProfileDocument extends Record<string, unknown> {
  apiVersion: "openppx.io/v1alpha1";
  kind: "ModelProfile";
  metadata: { name: string };
  spec: {
    displayName: string;
    provider: string;
    model: string;
    credential: ModelSecretRef | null;
    executionLocation: "local" | "remote";
    apiBase: string | null;
    capabilities: ModelCapability[];
    contextWindowTokens: number | null;
    inputCostPerMillionUsd: string | null;
    outputCostPerMillionUsd: string | null;
    fallbackProfiles: string[];
    enabled: boolean;
  };
}

export interface ModelProfileResourceResult extends Record<string, unknown> {
  resourceId: string;
  revision: string;
  document: ModelProfileDocument;
  credentialState?: string;
  effect?: "next_run";
}

export interface ModelProfileWriteInput extends Record<string, unknown> {
  displayName: string;
  providerId: string;
  model: string;
  executionLocation: "local" | "remote";
  apiBase: string | null;
  capabilities: ModelCapability[];
  contextWindowTokens: number | null;
  inputCostPerMillionUsd: string | null;
  outputCostPerMillionUsd: string | null;
  fallbackProfileIds: string[];
  enabled: boolean;
  apiKey: string | null;
}

export interface ModelProfileCreateInput extends ModelProfileWriteInput {}

export interface ModelProfileUpdateInput extends ModelProfileWriteInput {
  profileId: string;
  expectedRevision: string;
}

export interface ProviderModel {
  id: string;
  displayName: string;
  description: string;
  defaultReasoningEffort: string | null;
  reasoningEfforts: string[];
}

export interface ModelCatalogResult extends Record<string, unknown> {
  providerId: string;
  source: string;
  authoritative: boolean;
  defaultModel: string;
  items: ProviderModel[];
}

export interface ProviderAuthSession {
  id: string;
  state: "starting" | "pending" | "completed" | "failed" | "cancelled";
  verificationUrl: string | null;
  userCode: string | null;
  expiresAt: string | null;
  error: string | null;
}

export interface ProviderAuthStatus extends Record<string, unknown> {
  providerId: string;
  state: "authenticated" | "not_authenticated" | "expired" | "pending";
  source: "codex_cli" | "openppx_cache" | null;
  expiresAt: string | null;
  loginMode: "device_code";
  session: ProviderAuthSession | null;
}

export interface AgentCreateInput extends Record<string, unknown> {
  agentId: string;
  displayName: string;
  workspace: string | null;
  ownerPrincipalId: string;
  privilegeLevel: "low" | "medium" | "high" | "root";
  modelProfileId: string;
  instruction?: string;
}

export interface AgentResourceSummary extends Record<string, unknown> {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  status: "healthy" | "disabled";
  workspace: string;
  instruction: string;
  privilegeLevel: "low" | "medium" | "high" | "root";
  modelProfileId: string;
  avatar: string | null;
  tags: string[];
  revision: string;
  nodeRevision: string;
  effect: "none" | "next_run";
}

export interface AgentUpdateInput extends Record<string, unknown> {
  agentId: string;
  displayName: string;
  workspace: string;
  instruction: string;
  privilegeLevel: "low" | "medium" | "high" | "root";
  modelProfileId: string;
  expectedRevision: string;
}

export interface AgentCreateResult extends Record<string, unknown> {
  agent: {
    id: string;
    name: string;
    description: string;
    enabled: boolean;
    status: "healthy" | "idle" | "starting" | "disabled";
    workspace: string;
    avatar: string | null;
    tags: string[];
    revision: string;
  };
  nodeRevision: string;
  effect: "next_run";
}

export interface SetupStatusResult extends Record<string, unknown> {
  state: "ready" | "configured" | "needs_configuration";
  steps: Record<string, string>;
  revisions: { node: string | null; agent: string | null; profile: string | null };
  recommendedWorkspace: string;
  diagnostic: SetupStatusDiagnostic | null;
  current: { node: Record<string, unknown> | null; agent: Record<string, unknown> | null; profile: Record<string, unknown> | null };
  providers: SetupProvider[];
}

export interface SetupStatusDiagnostic {
  component: "node" | "agent" | "model" | "hello";
  errorKind: string;
  issues: Array<{
    code: string;
    path: Array<string | number>;
    message: string;
    source: string;
    line?: number;
    column?: number;
  }>;
}

export interface SetupApplyResult extends Record<string, unknown> {
  state: "configured";
  revisions: { node: string; agent: string; profile: string };
  secretState: string;
  restartRequired: boolean;
}

export interface SetupApplyRequest extends Record<string, unknown> {
  node: {
    apiVersion: "openppx.io/v1alpha1";
    kind: "NodeConfig";
    metadata: { name: string };
    spec: {
      displayName: string;
      enabledAgents: string[];
      clientApi: { listenHost: string; port: number; authentication: "required" | "disabled" };
    };
  };
  agent: {
    apiVersion: "openppx.io/v1alpha1";
    kind: "AgentConfig";
    metadata: { name: string };
    spec: {
      displayName: string;
      workspace: string;
      ownerPrincipalId: string;
      privilegeLevel: "low" | "medium" | "high" | "root";
      modelPolicy: { defaultProfile: string };
    };
  };
  profile: {
    apiVersion: "openppx.io/v1alpha1";
    kind: "ModelProfile";
    metadata: { name: string };
    spec: {
      displayName: string;
      provider: string;
      model: string;
      credential?: { store: "system"; name: string };
      executionLocation: "local" | "remote";
      capabilities: string[];
    };
  };
  secret: { ref: { store: "system"; name: string }; value: string } | null;
  expectedRevisions: { node: string | null; agent: string | null; profile: string | null };
}

export interface SetupHelloResult extends Record<string, unknown> {
  sessionId: string;
  reply: string;
  state: "ready";
}

export type HealthState = "healthy" | "degraded" | "unavailable" | "disabled";

export interface HealthComponent {
  component: string;
  state: HealthState;
  code: string;
  reason: string;
  remediation: string | null;
}

export interface OperationsHealthResult extends Record<string, unknown> {
  state: "healthy" | "degraded" | "unavailable";
  components: HealthComponent[];
}

export interface OperationsOverviewResult extends OperationsHealthResult {
  tasks: { total: number; byStatus: Record<string, number> };
  automation: { cronJobs: number; heartbeatEnabled: boolean };
}

export interface CronScheduleInput extends Record<string, unknown> {
  kind: "every" | "cron" | "at";
  everySeconds?: number;
  cronExpression?: string;
  atMs?: number;
  timezone?: string;
}

export interface CronCreateInput extends Record<string, unknown> {
  name: string;
  agentId: string;
  userId: string;
  message: string;
  schedule: CronScheduleInput;
  deleteAfterRun?: boolean;
}

export interface CronUpdateInput extends CronCreateInput {
  jobId: string;
}

export interface HeartbeatConfiguration extends Record<string, unknown> {
  enabled: boolean;
  everySeconds: number;
  prompt: string;
  activeHours: { start: string | null; end: string | null; timezone: string };
}

export type OperationsTaskControlAction = "interrupt" | "cancel" | "pause" | "resume" | "restart" | "send_input";

export interface OperationsTaskAction {
  action: OperationsTaskControlAction | "inspect_output";
  label: string;
  enabled: boolean;
  readOnly: boolean;
  reason: string;
}

export interface OperationsTaskItem extends Record<string, unknown> {
  taskId: string;
  kind: string;
  status: string;
  title: string;
  progressSummary: string;
  terminalSummary: string;
  lastError: string;
  checkpointRef: string;
  resumePolicy: string;
  updatedAtMs: number;
  actions: OperationsTaskAction[];
}

export interface OperationsTaskListResult extends Record<string, unknown> {
  ok: boolean;
  items: OperationsTaskItem[];
}

export interface OperationsTaskDetailResult extends OperationsTaskListResult {
  task: Record<string, unknown>;
  events: Array<Record<string, unknown>>;
  checkpoints: Array<Record<string, unknown>>;
  deliveries: Array<Record<string, unknown>>;
}

export interface OperationsCronJob extends Record<string, unknown> {
  id: string;
  name: string;
  enabled: boolean;
  agentId: string | null;
  userId: string | null;
  message: string;
  schedule: {
    kind: "every" | "cron" | "at";
    everySeconds: number | null;
    cronExpr: string | null;
    atMs: number | null;
    tz: string | null;
  };
  state: {
    nextRunAtMs: number | null;
    lastRunAtMs: number | null;
    lastStatus: string | null;
    lastError: string | null;
  };
  deleteAfterRun: boolean;
}

export interface OperationsCronResult extends Record<string, unknown> {
  status: Record<string, unknown>;
  items: OperationsCronJob[];
  history: Array<Record<string, unknown>>;
}

export interface OperationsHeartbeatResult extends Record<string, unknown> {
  running: boolean;
  enabled: boolean;
  intervalMs: number | null;
  wakePending: boolean;
  lastRunAtMs: number | null;
  lastStatus: string | null;
  lastReason: string | null;
  lastDurationMs: number | null;
  configuration: HeartbeatConfiguration;
}

export interface OperationsUsageResult extends Record<string, unknown> {
  requests: number;
  requestTokens: number;
  responseTokens: number;
  totalTokens: number;
  recent: Array<Record<string, unknown>>;
}

export interface OperationsAuditResult extends Record<string, unknown> {
  items: Array<Record<string, unknown>>;
}

export interface OperationsTaskControlInput extends Record<string, unknown> {
  taskId: string;
  action: OperationsTaskControlAction;
  content?: string;
  inlineBudgetMs?: number;
}

export interface ActionClientOptions {
  idFactory?: () => string;
}

export interface ActionTransport {
  requestJson(pathname: string, init?: RequestInit): Promise<Record<string, unknown>>;
}

let fallbackId = 0;

function nextWireId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  fallbackId += 1;
  return `client-${Date.now().toString(36)}-${fallbackId.toString(36)}`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

/** Parse one successful common Action envelope without weakening the shared wire contract. */
export function parseActionEnvelope<TResult extends Record<string, unknown>>(
  payload: unknown,
): ActionEnvelope<TResult> {
  const envelope = asRecord(payload);
  const result = asRecord(envelope?.result);
  if (
    envelope?.ok !== true ||
    envelope.protocolVersion !== CLIENT_API_PROTOCOL_VERSION ||
    typeof envelope.requestId !== "string" ||
    !envelope.requestId ||
    typeof envelope.correlationId !== "string" ||
    !envelope.correlationId ||
    !result
  ) {
    throw new ClientApiProtocolError("Action response is not a successful compatible v1 envelope.");
  }
  return {
    protocolVersion: CLIENT_API_PROTOCOL_VERSION,
    requestId: envelope.requestId,
    correlationId: envelope.correlationId,
    ok: true,
    result: result as TResult,
  };
}

/** Transport-neutral entry point for catalog discovery and typed Action invocation. */
export class ActionClient {
  private readonly idFactory: () => string;

  public constructor(
    private readonly transport: ActionTransport,
    options: ActionClientOptions = {},
  ) {
    this.idFactory = options.idFactory ?? nextWireId;
  }

  public async catalog(namespace?: string, projection?: ActionProjection): Promise<ActionCatalogItem[]> {
    const params = new URLSearchParams();
    if (namespace) params.set("namespace", namespace);
    if (projection) params.set("projection", projection);
    const query = params.size ? `?${params.toString()}` : "";
    const envelope = parseActionEnvelope<{ items: ActionCatalogItem[] }>(
      await this.transport.requestJson(`/api/v1/actions${query}`),
    );
    if (!Array.isArray(envelope.result.items)) {
      throw new ClientApiProtocolError("Action catalog result is missing items.");
    }
    return envelope.result.items;
  }

  public async invoke<TInput extends Record<string, unknown>, TResult extends Record<string, unknown>>(
    actionId: string,
    input: TInput,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<TResult>> {
    const requestId = options.requestId ?? this.idFactory();
    const correlationId = options.correlationId ?? requestId;
    return parseActionEnvelope<TResult>(
      await this.transport.requestJson("/api/v1/actions/invoke", {
        method: "POST",
        body: JSON.stringify({
          requestId,
          correlationId,
          actionId,
          input,
          confirmed: options.confirmed ?? false,
        }),
      }),
    );
  }
}

export class SystemClient {
  public constructor(private readonly actions: ActionClient) {}

  public status(): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("system.status", {});
  }
}

export class AgentClient {
  public constructor(private readonly actions: ActionClient) {}

  public create(input: AgentCreateInput): Promise<ActionEnvelope<AgentCreateResult>> {
    return this.actions.invoke("agent.create", input);
  }

  public list(): Promise<ActionEnvelope<{ items: AgentResourceSummary[] }>> {
    return this.actions.invoke("agent.list", {});
  }

  public update(input: AgentUpdateInput): Promise<ActionEnvelope<AgentResourceSummary>> {
    return this.actions.invoke("agent.update", input);
  }

  public setEnabled(agentId: string, enabled: boolean): Promise<ActionEnvelope<AgentResourceSummary>> {
    return this.actions.invoke("agent.enable", { agentId, enabled });
  }

  public remove(agentId: string, expectedRevision: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("agent.delete", { agentId, expectedRevision }, { confirmed: true });
  }
}

export class ModelClient {
  public constructor(private readonly actions: ActionClient) {}

  public list(): Promise<ActionEnvelope<{ items: Array<Record<string, unknown>> }>> {
    return this.actions.invoke("model.list", {});
  }

  public catalog(providerId: string): Promise<ActionEnvelope<ModelCatalogResult>> {
    return this.actions.invoke("model.catalog.list", { providerId });
  }

  public authStatus(providerId: string): Promise<ActionEnvelope<ProviderAuthStatus>> {
    return this.actions.invoke("model.auth.status", { providerId });
  }

  public beginAuth(providerId: string): Promise<ActionEnvelope<ProviderAuthStatus>> {
    return this.actions.invoke("model.auth.begin", { providerId });
  }

  public refreshAuth(providerId: string): Promise<ActionEnvelope<ProviderAuthStatus>> {
    return this.actions.invoke("model.auth.refresh", { providerId });
  }

  public readiness(input: ModelSelectionInput): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("model.readiness", input);
  }

  public select(input: ModelSelectionInput): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("model.select", input);
  }

  public sessionStatus(
    agentId: string,
    userId: string,
    sessionId: string,
  ): Promise<ActionEnvelope<SessionModelSelectionResult>> {
    return this.actions.invoke("model.session.command", {
      agentId,
      userId,
      sessionId,
      operation: "status",
    });
  }

  public selectForSession(
    agentId: string,
    userId: string,
    sessionId: string,
    profileId: string,
    expectedRevision?: number,
  ): Promise<ActionEnvelope<SessionModelSelectionResult>> {
    return this.actions.invoke("model.session.command", {
      agentId,
      userId,
      sessionId,
      operation: "select",
      profileId,
      expectedRevision: expectedRevision ?? null,
    });
  }

  public resetSession(
    agentId: string,
    userId: string,
    sessionId: string,
    expectedRevision?: number,
  ): Promise<ActionEnvelope<SessionModelSelectionResult>> {
    return this.actions.invoke("model.session.command", {
      agentId,
      userId,
      sessionId,
      operation: "reset",
      expectedRevision: expectedRevision ?? null,
    });
  }

  public readProfile(profileId: string): Promise<ActionEnvelope<ModelProfileResourceResult>> {
    return this.actions.invoke("model.profile.read", { profileId });
  }

  public applyProfile(
    profileId: string,
    candidate: ModelProfileDocument,
    expectedRevision: string | null,
  ): Promise<ActionEnvelope<ModelProfileResourceResult>> {
    return this.actions.invoke("model.profile.apply", { profileId, candidate, expectedRevision });
  }

  public createProfile(input: ModelProfileCreateInput): Promise<ActionEnvelope<ModelProfileResourceResult>> {
    return this.actions.invoke("model.profile.create", input);
  }

  public updateProfile(input: ModelProfileUpdateInput): Promise<ActionEnvelope<ModelProfileResourceResult>> {
    return this.actions.invoke("model.profile.update", input);
  }
}

export class SetupClient {
  public constructor(private readonly actions: ActionClient) {}

  public status(): Promise<ActionEnvelope<SetupStatusResult>> {
    return this.actions.invoke("setup.status", {});
  }

  public apply(request: SetupApplyRequest): Promise<ActionEnvelope<SetupApplyResult>> {
    return this.actions.invoke("setup.apply", { request });
  }

  public hello(agentId: string, userId: string, text = "Hello"): Promise<ActionEnvelope<SetupHelloResult>> {
    return this.actions.invoke("setup.hello", { agentId, userId, text });
  }
}

export class SecretClient {
  public constructor(private readonly actions: ActionClient) {}

  public status(ref: Record<string, unknown>): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("secret.status", { ref });
  }

  public put(ref: Record<string, unknown>, value: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("secret.put", { ref, value });
  }

  public delete(ref: Record<string, unknown>, confirmed = false): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("secret.delete", { ref }, { confirmed });
  }
}

/** Typed Node Operations facade shared by Desktop and future Mobile clients. */
export class OperationsClient {
  public constructor(private readonly actions: ActionClient) {}

  public overview(): Promise<ActionEnvelope<OperationsOverviewResult>> {
    return this.actions.invoke("operations.overview", {});
  }

  public health(): Promise<ActionEnvelope<OperationsHealthResult>> {
    return this.actions.invoke("operations.health", {});
  }

  public tasks(sessionId: string | null = null, limit = 20): Promise<ActionEnvelope<OperationsTaskListResult>> {
    return this.actions.invoke("operations.task.list", { sessionId, limit });
  }

  public task(taskId: string): Promise<ActionEnvelope<OperationsTaskDetailResult>> {
    return this.actions.invoke("operations.task.get", { taskId });
  }

  public taskOutput(taskId: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.task.output", { taskId });
  }

  public controlTask(input: OperationsTaskControlInput, confirmed = false): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.task.control", {
      taskId: input.taskId,
      action: input.action,
      content: input.content ?? "",
      inlineBudgetMs: input.inlineBudgetMs ?? null,
    }, { confirmed });
  }

  public cron(includeDisabled = true, historyLimit = 20): Promise<ActionEnvelope<OperationsCronResult>> {
    return this.actions.invoke("operations.cron.list", { includeDisabled, historyLimit });
  }

  public createCron(input: CronCreateInput, confirmed = false): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.cron.create", input, { confirmed });
  }

  public updateCron(input: CronUpdateInput, confirmed = false): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.cron.update", input, { confirmed });
  }

  public setCronEnabled(jobId: string, enabled: boolean, confirmed = false): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.cron.enable", { jobId, enabled }, { confirmed });
  }

  public removeCron(jobId: string, confirmed = false): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.cron.remove", { jobId }, { confirmed });
  }

  public runCron(jobId: string, force = false, confirmed = false): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.cron.run", { jobId, force }, { confirmed });
  }

  public heartbeat(): Promise<ActionEnvelope<OperationsHeartbeatResult>> {
    return this.actions.invoke("operations.heartbeat.status", {});
  }

  public runHeartbeat(reason = "manual", confirmed = false): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.heartbeat.run", { reason }, { confirmed });
  }

  public configureHeartbeat(input: HeartbeatConfiguration, confirmed = false): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.heartbeat.configure", input, { confirmed });
  }

  public usage(limit = 20, provider: string | null = null): Promise<ActionEnvelope<OperationsUsageResult>> {
    return this.actions.invoke("operations.usage.read", { limit, provider });
  }

  public audit(input: Record<string, unknown> = {}): Promise<ActionEnvelope<OperationsAuditResult>> {
    return this.actions.invoke("operations.audit.list", { limit: 50, ...input });
  }
}

export class SessionClient {
  public constructor(private readonly actions: ActionClient) {}

  public create(agentId: string, userId: string): Promise<ActionEnvelope<SessionNewResult>> {
    return this.actions.invoke("session.new", { agentId, userId });
  }

  public rename(agentId: string, userId: string, sessionId: string, title: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("session.rename", { agentId, userId, sessionId, title });
  }

  public archive(agentId: string, userId: string, sessionId: string, archived: boolean): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("session.archive", { agentId, userId, sessionId, archived });
  }

  public fork(agentId: string, userId: string, sessionId: string): Promise<ActionEnvelope<SessionNewResult>> {
    return this.actions.invoke("session.fork", { agentId, userId, sessionId });
  }

  public export(agentId: string, userId: string, sessionId: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("session.export", { agentId, userId, sessionId });
  }

  public remove(agentId: string, userId: string, sessionId: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("session.delete", { agentId, userId, sessionId }, { confirmed: true });
  }
}

/** Durable Goal and TaskFlow client backed by formal product Actions. */
export class GoalClient {
  public constructor(private readonly actions: ActionClient) {}

  public list(
    userId: string,
    input: { sessionId?: string | null; statuses?: GoalStatus[]; limit?: number } = {},
  ): Promise<ActionEnvelope<{ items: GoalSummary[] } & Record<string, unknown>>> {
    return this.actions.invoke("goal.list", {
      userId,
      sessionId: input.sessionId ?? null,
      statuses: input.statuses ?? [],
      limit: input.limit ?? 20,
    });
  }

  public read(goalId: string, userId: string): Promise<ActionEnvelope<GoalDetail>> {
    return this.actions.invoke("goal.read", { goalId, userId });
  }

  public create(input: {
    userId: string;
    agentId: string;
    sessionId: string;
    objective: string;
    completionCriteria?: string[];
    constraints?: string[];
    workspaceRef?: string;
    budgetPolicy?: Record<string, unknown>;
  }): Promise<ActionEnvelope<GoalDetail>> {
    return this.actions.invoke("goal.create", {
      ...input,
      completionCriteria: input.completionCriteria ?? [],
      constraints: input.constraints ?? [],
      workspaceRef: input.workspaceRef ?? "",
      budgetPolicy: input.budgetPolicy ?? {},
    });
  }

  public update(input: Record<string, unknown>): Promise<ActionEnvelope<GoalDetail>> {
    return this.actions.invoke("goal.update", input);
  }

  public transition(
    operation: "pause" | "resume" | "cancel",
    input: { goalId: string; userId: string; expectedRevision: number; reason?: string },
  ): Promise<ActionEnvelope<GoalDetail>> {
    return this.actions.invoke(`goal.${operation}`, { ...input, reason: input.reason ?? "" });
  }

  public complete(input: Record<string, unknown>): Promise<ActionEnvelope<GoalDetail>> {
    return this.actions.invoke("goal.complete", input);
  }

  public history(goalId: string, userId: string, limit = 100): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("goal.history", { goalId, userId, limit });
  }

  public readFlow(flowId: string, userId: string): Promise<ActionEnvelope<TaskFlowDetail>> {
    return this.actions.invoke("task_flow.read", { flowId, userId });
  }

  public updateFlow(input: Record<string, unknown>): Promise<ActionEnvelope<TaskFlowDetail>> {
    return this.actions.invoke("task_flow.update", input);
  }
}

/** Durable User Automations client backed by formal product Actions. */
export class AutomationClient {
  public constructor(private readonly actions: ActionClient) {}

  public list(userId: string, statuses: AutomationStatus[] = [], limit = 100): Promise<ActionEnvelope<{ items: AutomationSummary[] } & Record<string, unknown>>> {
    return this.actions.invoke("automation.list", { userId, statuses, limit });
  }

  public read(automationId: string, userId: string): Promise<ActionEnvelope<AutomationDetail>> {
    return this.actions.invoke("automation.read", { automationId, userId });
  }

  public create(input: AutomationCreateInput): Promise<ActionEnvelope<AutomationDetail>> {
    return this.actions.invoke("automation.create", {
      description: "",
      outputRequirements: [],
      workspaceRef: "",
      contextMode: "isolated",
      modelProfileRef: null,
      extensionPolicy: {},
      permissionPolicy: {},
      permissionsConfirmed: false,
      deliveryPolicy: {},
      concurrencyPolicy: { mode: "skip", limit: 1 },
      missedRunPolicy: { mode: "run-latest", maxCatchUp: 1 },
      retryPolicy: { maxAttempts: 1, backoffSeconds: 30 },
      budgetPolicy: { timeoutSeconds: 1800 },
      monitorPolicy: { enabled: false, notifyOnChangeOnly: true, stopWhenContains: "" },
      schedule: null,
      localEvent: null,
      ...input,
    });
  }

  public update(input: Record<string, unknown>): Promise<ActionEnvelope<AutomationDetail>> {
    return this.actions.invoke("automation.update", input);
  }

  public transition(
    operation: "pause" | "resume" | "delete",
    input: { automationId: string; userId: string; expectedRevision: number },
    confirmed = false,
  ): Promise<ActionEnvelope<AutomationDetail | { automationId: string; deleted: true }>> {
    return this.actions.invoke(`automation.${operation}`, input, { confirmed });
  }

  public run(automationId: string, userId: string, input: Record<string, unknown> = {}): Promise<ActionEnvelope<{ run: AutomationRunSummary } & Record<string, unknown>>> {
    return this.actions.invoke("automation.run", { automationId, userId, input });
  }

  public history(automationId: string, userId: string, limit = 50): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("automation.history", { automationId, userId, limit });
  }

  public templates(): Promise<ActionEnvelope<{ items: AutomationTemplateSummary[] } & Record<string, unknown>>> {
    return this.actions.invoke("automation.template.list", {});
  }

  public permissionPreview(automationId: string, userId: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("automation.permission.preview", { automationId, userId });
  }

  public revokePermissions(automationId: string, userId: string, expectedRevision: number): Promise<ActionEnvelope<AutomationDetail>> {
    return this.actions.invoke(
      "automation.permission.revoke",
      { automationId, userId, expectedRevision },
      { confirmed: true },
    );
  }
}

export class RunClient {
  public constructor(private readonly actions: ActionClient) {}

  public stop(runId: string): Promise<ActionEnvelope<RunStopResult>> {
    return this.actions.invoke("run.stop", { runId });
  }
}

/** Action-backed slash command discovery and invocation for any client surface. */
export class CommandClient {
  public constructor(private readonly actions: ActionClient) {}

  public async list(namespace?: string): Promise<ProjectedSlashCommand[]> {
    const catalog = await this.actions.catalog(namespace, "slash");
    return catalog
      .flatMap((action) =>
        action.slashCommands.map((command) => ({
          ...command,
          actionId: action.actionId,
          available: action.available,
          availabilityReason: action.availabilityReason,
        })),
      )
      .sort((left, right) => left.order - right.order || left.command.localeCompare(right.command));
  }

  public invoke(
    rawCommand: string,
    context: SlashCommandContext,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<SlashCommandResult>> {
    return this.actions.invoke(
      "system.command.invoke",
      {
        rawCommand,
        userId: context.userId,
        agentId: context.agentId ?? null,
        sessionId: context.sessionId ?? null,
        runId: context.runId ?? null,
      },
      options,
    );
  }
}

/** Public TypeScript SDK facade shared by Desktop and future Mobile clients. */
export class OpenPpxClient {
  public readonly transport: ClientApiHttpTransport;

  public readonly actions: ActionClient;

  public readonly system: SystemClient;

  public readonly agent: AgentClient;

  public readonly model: ModelClient;

  public readonly session: SessionClient;

  public readonly run: RunClient;

  public readonly goal: GoalClient;

  public readonly automation: AutomationClient;

  public readonly extensions: ExtensionClient;

  public readonly commands: CommandClient;

  public readonly setup: SetupClient;

  public readonly secrets: SecretClient;

  public readonly operations: OperationsClient;

  public readonly artifacts: ArtifactClient;

  public constructor(options: ClientApiHttpTransportOptions & ActionClientOptions) {
    this.transport = new ClientApiHttpTransport(options);
    this.actions = new ActionClient(this.transport, options);
    this.system = new SystemClient(this.actions);
    this.agent = new AgentClient(this.actions);
    this.model = new ModelClient(this.actions);
    this.session = new SessionClient(this.actions);
    this.run = new RunClient(this.actions);
    this.goal = new GoalClient(this.actions);
    this.automation = new AutomationClient(this.actions);
    this.extensions = new ExtensionClient(this.actions);
    this.commands = new CommandClient(this.actions);
    this.setup = new SetupClient(this.actions);
    this.secrets = new SecretClient(this.actions);
    this.operations = new OperationsClient(this.actions);
    this.artifacts = new ArtifactClient(this.transport);
  }
}
