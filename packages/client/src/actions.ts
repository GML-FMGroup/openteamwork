import { CLIENT_API_PROTOCOL_VERSION } from "./contract";
import { ClientApiProtocolError } from "./contract";
import { ClientApiHttpTransport, type ClientApiHttpTransportOptions } from "./http-transport";
import { ExtensionClient } from "./extensions";

export type ActionScope = "node" | "agent" | "session" | "run" | "task" | "extension";
export type ActionRisk = "low" | "medium" | "high";
export type ActionProjection = "cli" | "slash" | "desktop" | "mobile";
export type SlashCommandLifecycle = "side_channel" | "finalize_active_turn" | "stop_active_turn";

export interface SlashCommandItem {
  command: string;
  title: string;
  description: string;
  icon: string;
  argHint: string;
  lifecycle: SlashCommandLifecycle;
  acceptsArgs: boolean;
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

export interface SetupProvider {
  id: string;
  displayName: string;
  runtime: string;
  credentialMode: "api_key" | "oauth" | "none";
  credentialRequired: boolean;
  defaultModel: string;
}

export interface SetupStatusResult extends Record<string, unknown> {
  state: "ready" | "configured" | "needs_configuration";
  steps: Record<string, string>;
  revisions: { node: string | null; agent: string | null; profile: string | null };
  recommendedWorkspace: string;
  current: { node: Record<string, unknown> | null; agent: Record<string, unknown> | null; profile: Record<string, unknown> | null };
  providers: SetupProvider[];
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

export class ModelClient {
  public constructor(private readonly actions: ActionClient) {}

  public list(): Promise<ActionEnvelope<{ items: Array<Record<string, unknown>> }>> {
    return this.actions.invoke("model.list", {});
  }

  public readiness(input: ModelSelectionInput): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("model.readiness", input);
  }

  public select(input: ModelSelectionInput): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("model.select", input);
  }

  public readProfile(profileId: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("model.profile.read", { profileId });
  }

  public applyProfile(
    profileId: string,
    candidate: Record<string, unknown>,
    expectedRevision: string | null,
  ): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("model.profile.apply", { profileId, candidate, expectedRevision });
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

  public tasks(sessionId: string | null = null, limit = 20): Promise<ActionEnvelope<{ items: Array<Record<string, unknown>> }>> {
    return this.actions.invoke("operations.task.list", { sessionId, limit });
  }

  public cron(includeDisabled = true, historyLimit = 20): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.cron.list", { includeDisabled, historyLimit });
  }

  public createCron(input: CronCreateInput, confirmed = false): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.cron.create", input, { confirmed });
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

  public heartbeat(): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.heartbeat.status", {});
  }

  public runHeartbeat(reason = "manual", confirmed = false): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.heartbeat.run", { reason }, { confirmed });
  }

  public usage(limit = 20, provider: string | null = null): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("operations.usage.read", { limit, provider });
  }

  public audit(input: Record<string, unknown> = {}): Promise<ActionEnvelope<{ items: Array<Record<string, unknown>> }>> {
    return this.actions.invoke("operations.audit.list", { limit: 50, ...input });
  }
}

export class SessionClient {
  public constructor(private readonly actions: ActionClient) {}

  public create(agentId: string, userId: string): Promise<ActionEnvelope<SessionNewResult>> {
    return this.actions.invoke("session.new", { agentId, userId });
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

  public readonly model: ModelClient;

  public readonly session: SessionClient;

  public readonly run: RunClient;

  public readonly extensions: ExtensionClient;

  public readonly commands: CommandClient;

  public readonly setup: SetupClient;

  public readonly secrets: SecretClient;

  public readonly operations: OperationsClient;

  public constructor(options: ClientApiHttpTransportOptions & ActionClientOptions) {
    this.transport = new ClientApiHttpTransport(options);
    this.actions = new ActionClient(this.transport, options);
    this.system = new SystemClient(this.actions);
    this.model = new ModelClient(this.actions);
    this.session = new SessionClient(this.actions);
    this.run = new RunClient(this.actions);
    this.extensions = new ExtensionClient(this.actions);
    this.commands = new CommandClient(this.actions);
    this.setup = new SetupClient(this.actions);
    this.secrets = new SecretClient(this.actions);
    this.operations = new OperationsClient(this.actions);
  }
}
