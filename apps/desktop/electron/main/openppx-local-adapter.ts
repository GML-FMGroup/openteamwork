import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  ActionClient,
  AgentClient,
  AutomationClient,
  ArtifactClient,
  CLIENT_API_PROTOCOL_VERSION,
  CommandClient,
  ExtensionClient,
  GoalClient,
  ModelClient,
  OperationsClient,
  SecretClient,
  SessionClient,
  SetupClient,
  normalizeClientApiMessage,
  normalizeClientApiPart,
  normalizeClientApiSession,
} from "@openppx/client";
import { normalizeClientApiRuntime } from "../../app/src/lib/client-api-projection";
import { normalizeAgentProfile, normalizeWorkspaceAgents } from "../../app/src/lib/agent-projection";
import { isLoopbackClientApiHostname } from "../../app/src/lib/connection-profile";
import { productProfile } from "../../product";
import type {
  AgentProfile,
  AgentCreateRequest,
  AgentCreateResult,
  AgentResourceSummary,
  AgentUpdateInput,
  AutomationCreateInput,
  AutomationDetail,
  AutomationRunSummary,
  AutomationStatus,
  AutomationSummary,
  AutomationTemplateSummary,
  AutomationUpdateRequest,
  ArtifactSummary,
  ArtifactUploadInput,
  BootstrapPayload,
  ChatMessage,
  ClientDiagnostics,
  ConnectionSettings,
  ConnectionTarget,
  ExtensionDetail,
  ExtensionEnablementRequest,
  ExtensionInstallRequest,
  ExtensionMutationResult,
  ExtensionPreview,
  ExtensionPreviewRequest,
  ExtensionProbeResult,
  ExtensionHealthHistory,
  ExtensionReadinessResult,
  ExtensionRemoveRequest,
  ExtensionSummary,
  GoalDetail,
  GoalTransitionOperation,
  GoalUpdateRequest,
  McpServerResource,
  McpMutationRequest,
  McpOAuthStatus,
  AppConnectionDetail,
  AppConnectionEnablementRequest,
  AppConnectionRemoveRequest,
  AppConnectionSaveRequest,
  PluginHookStatus,
  PluginMarketplaceEntry,
  PluginMarketplaceSource,
  PluginMarketplaceSourceSpec,
  ModelProfileSummary,
  ModelProfileResourceResult,
  ModelProfileCreateInput,
  ModelProfileUpdateInput,
  OperationsAuditItem,
  OperationsDashboard,
  OperationsOverviewResult,
  OperationsTaskDetailResult,
  OperationsTaskControlInput,
  CronCreateInput,
  CronUpdateInput,
  HeartbeatConfiguration,
  ContextCompactionConfiguration,
  ModelCatalogResult,
  ProviderAuthStatus,
  ResponseFeedbackInput,
  MessagePart,
  PpxClientApi,
  RunEvent,
  RuntimeCommand,
  RuntimeStatus,
  SendMessageInput,
  SessionSummary,
  SessionMutationRequest,
  SlashCommandRequest,
  ProjectedSlashCommand,
  SlashCommandResult,
  SetupApplyRequest,
  SetupApplyResult,
  SetupHelloResult,
  SetupReadinessResult,
  SetupStatusResult,
  UserProfile,
  UserLoginRequest,
} from "../../app/src/types";
import { shouldStartManagedNode } from "./node-start-policy";
import {
  ClientApiConnection,
  classifyClientApiConnectionFailure,
} from "./client-api-connection";
import { submitClientApiRunWithRecovery } from "./client-api-run-submission";
import { ClientApiRunStream } from "./client-api-run-stream";
import { ClientApiSessionCache } from "./client-api-session-cache";
import { LocalNodeSupervisor } from "./local-node-supervisor";
import {
  findLatestPersistedRunMessage,
  isTerminalRunStatus,
  monitorPersistedTerminalRun,
} from "./run-terminal-reconciliation";

type EventSink = (event: RunEvent) => void;
type StepPart = Extract<MessagePart, { type: "step_ref" }>;

export interface OpenPpxLocalAdapterOptions {
  fetch?: typeof globalThis.fetch;
}

function now(): string {
  return new Date().toISOString();
}

/** Replace a streamed step without changing the chronological order in which it first appeared. */
function upsertStepPart(parts: StepPart[], nextPart: StepPart): StepPart[] {
  const existingIndex = parts.findIndex((part) => part.stepId === nextPart.stepId);
  if (existingIndex < 0) {
    return [...parts, nextPart];
  }
  return parts.map((part, index) => (index === existingIndex ? nextPart : part));
}

/** Replace a streamed step in-place while retaining surrounding commentary. */
function upsertOrderedStepPart(parts: MessagePart[], nextPart: StepPart): MessagePart[] {
  const existingIndex = parts.findIndex(
    (part) => part.type === "step_ref" && part.stepId === nextPart.stepId,
  );
  if (existingIndex < 0) {
    return [...parts, nextPart];
  }
  return parts.map((part, index) => (index === existingIndex ? nextPart : part));
}

/** Maintain one terminal markdown snapshot without moving earlier run events. */
function upsertFinalMarkdownPart(parts: MessagePart[], text: string): MessagePart[] {
  if (!text.trim()) {
    return parts;
  }
  let existingIndex = -1;
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    if (parts[index]?.type === "markdown") {
      existingIndex = index;
      break;
    }
  }
  const nextPart: MessagePart = { type: "markdown", text };
  if (existingIndex < 0) {
    return [...parts, nextPart];
  }
  return parts.map((part, index) => (index === existingIndex ? nextPart : part));
}

/** Avoid duplicate commentary snapshots while preserving distinct milestones. */
function appendCommentaryPart(parts: MessagePart[], nextPart: Extract<MessagePart, { type: "commentary" }>): MessagePart[] {
  const previous = parts.at(-1);
  if (previous?.type === "commentary" && previous.text === nextPart.text) {
    return parts;
  }
  return [...parts, nextPart];
}

function ensureRenderableAssistantParts(parts: MessagePart[]): MessagePart[] {
  return parts;
}

function latestMarkdownText(parts: MessagePart[]): string {
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    const part = parts[index];
    if (part?.type === "markdown") {
      return part.text;
    }
  }
  return "";
}

function appCredentialRefName(connectionId: string, slot: string): string {
  const base = `app-${connectionId}-${slot}`.replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
  const suffix = randomBytes(4).toString("hex");
  return `${base.slice(0, 54).replace(/-+$/g, "")}-${suffix}`;
}

function mcpSecretRefName(serverId: string, alias: string): string {
  const base = `mcp-${serverId}-${alias}`.replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
  const suffix = randomBytes(4).toString("hex");
  return `${base.slice(0, 54).replace(/-+$/g, "")}-${suffix}`;
}

async function cleanupSecrets(
  secrets: SecretClient,
  references: Array<{ store: "system"; name: string }>,
): Promise<void> {
  await Promise.allSettled(references.map((reference) => secrets.delete(reference, true)));
}

/**
 * Stage write-only MCP values under fresh SecretRefs and return an immutable resource rewrite.
 * Existing references are never overwritten, so a failed MCP revision cannot change the live server.
 */
async function stageMcpSecrets(
  secrets: SecretClient,
  resource: McpServerResource,
  secretValues: Record<string, string>,
): Promise<{
  resource: McpServerResource;
  createdRefs: Array<{ store: "system"; name: string }>;
}> {
  const bindingGroups = resource.spec.transport.type === "stdio"
    ? [resource.spec.transport.environment]
    : [resource.spec.transport.headers, resource.spec.transport.query ?? {}];
  const referencedAliases = new Set(
    bindingGroups.flatMap((bindings) => Object.values(bindings))
      .filter((binding) => binding.kind === "secret")
      .map((binding) => binding.kind === "secret" ? binding.secretRef.name : ""),
  );
  const replacements = new Map<string, { store: "system"; name: string }>();
  const createdRefs: Array<{ store: "system"; name: string }> = [];

  try {
    for (const [alias, rawValue] of Object.entries(secretValues)) {
      if (!rawValue.trim()) continue;
      if (!referencedAliases.has(alias)) {
        throw new Error(`MCP secret value '${alias}' is not referenced by an environment variable, header, or query parameter.`);
      }
      const reference = {
        store: "system" as const,
        name: mcpSecretRefName(resource.metadata.name, alias),
      };
      await secrets.put(reference, rawValue);
      replacements.set(alias, reference);
      createdRefs.push(reference);
    }
  } catch (error) {
    await cleanupSecrets(secrets, createdRefs);
    throw error;
  }

  const rewriteBindings = (bindings: Record<string, import("@openppx/client").McpValueBinding>) => Object.fromEntries(
    Object.entries(bindings).map(([name, binding]) => {
      if (binding.kind !== "secret") return [name, binding];
      const replacement = replacements.get(binding.secretRef.name);
      return [name, replacement ? { ...binding, secretRef: replacement } : binding];
    }),
  );
  const transport: McpServerResource["spec"]["transport"] = resource.spec.transport.type === "stdio"
    ? { ...resource.spec.transport, environment: rewriteBindings(resource.spec.transport.environment) }
    : {
        ...resource.spec.transport,
        headers: rewriteBindings(resource.spec.transport.headers),
        query: rewriteBindings(resource.spec.transport.query ?? {}),
      };
  return {
    resource: {
      ...resource,
      metadata: { ...resource.metadata },
      spec: { ...resource.spec, transport },
    },
    createdRefs,
  };
}

function emptyBootstrap(runtime: RuntimeStatus): BootstrapPayload {
  return {
    runtime,
    agents: [],
    sessions: [],
    messages: [],
    selectedAgentId: "",
    selectedSessionId: "",
  };
}

function clientDebugEnabled(): boolean {
  const raw = productEnvironment("CLIENT_DEBUG") ?? "";
  return ["1", "true", "yes", "on"].includes(raw.trim().toLowerCase());
}

function productEnvironment(suffix: string): string | undefined {
  return process.env[`${productProfile.environmentPrefix}_${suffix}`];
}

function clientDebugLog(tag: string, payload: unknown): void {
  if (!clientDebugEnabled()) {
    return;
  }
  console.log(`[ppx-client][debug] ${tag}`, payload);
}

function detectOpenPpxRoot(): string {
  const configured = productEnvironment("ROOT")?.trim();
  if (configured) {
    return path.resolve(configured);
  }
  return path.resolve(process.cwd(), "../..");
}

function dataRootPath(): string {
  const configured = productEnvironment("NODE_ROOT")?.trim();
  if (configured) {
    return path.resolve(configured);
  }
  return path.join(os.homedir(), productProfile.nodeRootDirectory);
}

function resolvePythonBin(openppxRoot: string): string {
  const venvPython = path.join(openppxRoot, ".venv", "bin", "python");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return "python3";
}

export class OpenPpxLocalAdapter implements Omit<
  PpxClientApi,
  "openExternalUrl" | "platform" | "listConnectionProfiles" | "activateConnectionProfile" | "removeConnectionProfile" | "setDesktopHostPreferences"
> {
  private readonly listeners = new Set<EventSink>();

  private readonly openppxRoot = detectOpenPpxRoot();

  private readonly pythonBin = resolvePythonBin(this.openppxRoot);

  private readonly configuredClientApiBaseUrl = productEnvironment("CLIENT_API_BASE_URL")?.trim() || "";

  private readonly configuredClientApiAccessToken = productEnvironment("CLIENT_API_TOKEN")?.trim() || "";

  private readonly managedLocalAccessToken = randomBytes(32).toString("base64url");

  private readonly clientApiHost = productEnvironment("CLIENT_API_HOST")?.trim() || "127.0.0.1";

  private readonly clientApiPort = Number(
    productEnvironment("CLIENT_API_PORT")?.trim() || productProfile.defaultClientApiPort,
  );

  private target: ConnectionTarget;

  private readonly connection: ClientApiConnection;

  private readonly runStream: ClientApiRunStream;

  private readonly actions: ActionClient;

  private readonly extensions: ExtensionClient;

  private readonly goals: GoalClient;

  private readonly automations: AutomationClient;

  private readonly agentManagement: AgentClient;

  private readonly sessions: SessionClient;

  private readonly artifacts: ArtifactClient;

  private readonly commands: CommandClient;

  private readonly setup: SetupClient;

  private readonly models: ModelClient;

  private readonly operations: OperationsClient;

  private readonly secrets: SecretClient;

  private readonly nodeSupervisor: LocalNodeSupervisor;

  private readonly sessionCache = new ClientApiSessionCache();

  private readonly activeRunStreams = new Map<string, AbortController>();

  private authenticatedUser: UserProfile | null = null;

  private inflightHealthCheck: Promise<boolean> | null = null;

  public constructor(initialSettings?: ConnectionSettings, options: OpenPpxLocalAdapterOptions = {}) {
    this.target = this.buildTarget();
    this.connection = new ClientApiConnection({
      baseUrl: this.configuredClientApiBaseUrl || `http://${this.clientApiHost}:${this.clientApiPort}`,
      accessToken:
        this.configuredClientApiAccessToken || (this.target.type === "local" ? this.managedLocalAccessToken : ""),
      fetch: options.fetch,
    });
    this.runStream = new ClientApiRunStream({
      request: (pathname, init) => this.connection.request(pathname, init),
      maxReconnectAttempts: 12,
    });
    this.actions = new ActionClient(this.connection);
    this.extensions = new ExtensionClient(this.actions);
    this.goals = new GoalClient(this.actions);
    this.automations = new AutomationClient(this.actions);
    this.agentManagement = new AgentClient(this.actions);
    this.sessions = new SessionClient(this.actions);
    this.artifacts = new ArtifactClient(this.connection);
    this.commands = new CommandClient(this.actions);
    this.setup = new SetupClient(this.actions);
    this.models = new ModelClient(this.actions);
    this.operations = new OperationsClient(this.actions);
    this.secrets = new SecretClient(this.actions);
    this.nodeSupervisor = new LocalNodeSupervisor({
      openppxRoot: this.openppxRoot,
      nodeRoot: dataRootPath(),
      pythonBin: this.pythonBin,
      spawnProcess: spawn,
      log: clientDebugLog,
      onExit: ({ baseUrl }) => {
        if (baseUrl === this.connection.baseUrl) {
          this.connection.invalidateHealth();
        }
      },
    });
    if (initialSettings) {
      this.applyConnectionSettings(initialSettings);
    }
  }

  private buildTarget(): ConnectionTarget {
    const rawType = productEnvironment("TARGET_TYPE")?.trim().toLowerCase() || "local";
    if (rawType === "remote" || rawType === "lan") {
      return {
        id: productEnvironment("TARGET_ID")?.trim() || "remote-default",
        type: "remote",
        name: productEnvironment("TARGET_NAME")?.trim() || "Remote Node",
      };
    }
    return {
      id: productEnvironment("TARGET_ID")?.trim() || "local-default",
      type: "local",
      name: productEnvironment("TARGET_NAME")?.trim() || "This Mac",
    };
  }

  private isRemoteTarget(): boolean {
    return this.target.type === "remote";
  }

  public applyConnectionSettings(settings: ConnectionSettings): void {
    const isLan = settings.targetType === "lan";
    const nextBaseUrl = settings.clientApiBaseUrl.trim() || `http://${this.clientApiHost}:${this.clientApiPort}`;
    const shouldStopManagedProcess =
      this.target.type === "local" && (isLan || nextBaseUrl !== this.connection.baseUrl);
    this.target = {
      id: settings.targetId.trim() || (isLan ? "lan-default" : "local-default"),
      type: isLan ? "remote" : "local",
      name: settings.targetName.trim() || (isLan ? `LAN ${productProfile.displayName} Node` : "This Mac"),
    };
    this.connection.configure({
      baseUrl: nextBaseUrl,
      accessToken: settings.accessToken?.trim() || (
        isLan
          ? this.configuredClientApiAccessToken
          : this.configuredClientApiAccessToken || this.managedLocalAccessToken
      ),
    });
    this.authenticatedUser = settings.userId && settings.userEmail && settings.userPrivilegeLevel
      ? {
          id: settings.userId,
          displayName: settings.userEmail,
          accountKind: "product",
          email: settings.userEmail,
          privilegeLevel: settings.userPrivilegeLevel,
        }
      : null;
    this.sessionCache.clear();
    if (shouldStopManagedProcess) {
      this.stopManagedClientApiImmediately();
    }
    this.abortActiveRunStreams();
  }

  private emit(event: RunEvent): void {
    this.sessionCache.applyEvent(event);
    this.listeners.forEach((listener) => listener(event));
  }

  private requireUserId(): string {
    if (!this.authenticatedUser?.id) {
      throw new Error("Sign in to the OpenTeamwork Node first.");
    }
    return this.authenticatedUser.id;
  }

  private async fetchClientApiJson(pathname: string, init?: RequestInit): Promise<Record<string, unknown>> {
    return this.connection.requestJson(pathname, init);
  }

  private stopManagedClientApiImmediately(): void {
    this.connection.invalidateHealth();
    this.nodeSupervisor.stopImmediately();
  }

  private async stopManagedClientApi(): Promise<void> {
    this.connection.invalidateHealth();
    await this.nodeSupervisor.stop();
  }

  private managedClientApiBindAddress(): { host: string; port: number } | null {
    if (!this.nodeSupervisor.managementEnabled || this.isRemoteTarget()) {
      return null;
    }
    try {
      const endpoint = new URL(this.connection.baseUrl);
      if (endpoint.protocol !== "http:" || !isLoopbackClientApiHostname(endpoint.hostname)) {
        return null;
      }
      return {
        host: endpoint.hostname.replace(/^\[|\]$/g, ""),
        port: Number(endpoint.port || "80"),
      };
    } catch {
      return null;
    }
  }

  private rememberClientApiError(error: unknown): void {
    this.connection.rememberError(error);
  }

  private clientApiUnavailableError(operation: string): Error {
    return this.connection.unavailableError(operation);
  }

  private async isClientApiHealthy(): Promise<boolean> {
    return this.connection.checkHealth({ timeoutMs: this.isRemoteTarget() ? 2_000 : 500 });
  }

  private async ensureClientApiAvailable(): Promise<boolean> {
    if (this.connection.isHealthCached()) {
      return true;
    }
    if (this.inflightHealthCheck) {
      return this.inflightHealthCheck;
    }
    this.inflightHealthCheck = this.ensureClientApiAvailableImpl();
    try {
      return await this.inflightHealthCheck;
    } finally {
      this.inflightHealthCheck = null;
    }
  }

  private async ensureClientApiAvailableImpl(): Promise<boolean> {
    if (await this.isClientApiHealthy()) {
      clientDebugLog("client-api.health", {
        baseUrl: this.connection.baseUrl,
        status: "healthy",
      });
      return true;
    }
    const openppxRootExists = fs.existsSync(this.openppxRoot);
    const managedBindAddress = this.managedClientApiBindAddress();
    const connection = this.connection.getSnapshot();
    if (
      !managedBindAddress ||
      !shouldStartManagedNode({
        targetType: this.target.type,
        failure: connection.lastFailure,
        openppxRootExists,
      })
    ) {
      const status = connection.reachable
        ? `reachable-${connection.authState}`
        : this.isRemoteTarget()
          ? "remote-unreachable"
          : !openppxRootExists
            ? "openppx-root-missing"
            : "local-endpoint-not-managed";
      clientDebugLog("client-api.health", {
        baseUrl: this.connection.baseUrl,
        status,
        target: this.target,
        openppxRoot: this.openppxRoot,
        error: connection.lastError,
      });
      return false;
    }
    return this.nodeSupervisor.ensureReady({
      host: managedBindAddress.host,
      port: managedBindAddress.port,
      accessToken: this.connection.accessToken,
      baseUrl: this.connection.baseUrl,
      probe: () => this.isClientApiHealthy(),
    });
  }

  private getFallbackRuntimeStatus(): RuntimeStatus {
    const connection = this.connection.getSnapshot();
    if (this.isRemoteTarget()) {
      return {
        target: this.target,
        state: "error",
        summary: `Remote ${productProfile.displayName} Node is unavailable.`,
        detail: connection.lastError || `Check the remote Node at ${connection.baseUrl}.`,
        lastError: "REMOTE_NODE_UNAVAILABLE",
      };
    }
    if (!fs.existsSync(this.openppxRoot)) {
      return {
        target: this.target,
        state: "error",
        summary: `${productProfile.displayName} source root was not found.`,
        detail: `Set ${productProfile.environmentPrefix}_ROOT or run Desktop from apps/desktop in this repository.`,
        lastError: `${productProfile.environmentPrefix}_ROOT_NOT_FOUND`,
      };
    }
    return {
      target: this.target,
      state: "error",
      summary: `${productProfile.displayName} Client API is unavailable.`,
      detail: connection.lastError || `Start a compatible ${productProfile.displayName} Node using protocol v${CLIENT_API_PROTOCOL_VERSION}.`,
      lastError:
        connection.handshake?.compatibility === "incompatible"
          ? "CLIENT_API_PROTOCOL_INCOMPATIBLE"
          : "CLIENT_API_UNAVAILABLE",
    };
  }

  private async fetchRuntimeStatus(): Promise<RuntimeStatus> {
    if (await this.ensureClientApiAvailable()) {
      try {
        const payload = await this.fetchClientApiJson("/api/v1/runtime/status");
        const runtime = normalizeClientApiRuntime((payload.data as Record<string, unknown> | undefined) ?? {});
        if (!runtime) {
          throw new Error("Client API returned an invalid runtime status payload.");
        }
        return runtime;
      } catch (error) {
        this.rememberClientApiError(error);
      }
    }
    return this.getFallbackRuntimeStatus();
  }

  public async bootstrap(): Promise<BootstrapPayload> {
    const runtime = await this.fetchRuntimeStatus();
    if (runtime.state !== "healthy") {
      return emptyBootstrap(runtime);
    }
    const clientApiHealthy = await this.isClientApiHealthy();
    if (!clientApiHealthy) {
      return emptyBootstrap(runtime);
    }

    let agents: AgentProfile[] = [];
    if (clientApiHealthy) {
      try {
        const payload = await this.fetchClientApiJson("/api/v1/agents");
        const items = Array.isArray((payload.data as Record<string, unknown> | undefined)?.items)
          ? ((payload.data as Record<string, unknown>).items as Array<Record<string, unknown>>)
          : [];
        agents = normalizeWorkspaceAgents(items);
      } catch (error) {
        this.rememberClientApiError(error);
        throw this.clientApiUnavailableError("Loading agents");
      }
    }

    const selectedAgentId = agents[0]?.id ?? "";
    const sessions = selectedAgentId ? (await this.listSessions(selectedAgentId)).sessions : [];
    const selectedSessionId = sessions[0]?.id ?? "";
    const messages = selectedSessionId ? (await this.loadSession(selectedSessionId)).messages : [];
    return {
      runtime,
      agents,
      sessions,
      messages,
      selectedAgentId,
      selectedSessionId,
    };
  }

  public async getUserProfile(): Promise<UserProfile> {
    const session = await this.connection.getAuthenticatedSession();
    const user = session.user;
    const profile: UserProfile = {
      id: user.userId,
      displayName: user.email,
      accountKind: "product",
      email: user.email,
      privilegeLevel: user.privilegeLevel,
      sessionExpiresAtMs: session.expiresAtMs,
    };
    this.authenticatedUser = profile;
    return profile;
  }

  public async login(request: UserLoginRequest): Promise<UserProfile> {
    this.applyConnectionSettings({ ...request.connection, accessToken: "" });
    const login = await this.connection.login(request.email.trim(), request.secret);
    const authenticatedSettings: ConnectionSettings = {
      ...request.connection,
      accessToken: login.accessToken,
      userId: login.user.userId,
      userEmail: login.user.email,
      userPrivilegeLevel: login.user.privilegeLevel,
    };
    this.applyConnectionSettings(authenticatedSettings);
    const profile: UserProfile = {
      id: login.user.userId,
      displayName: login.user.email,
      accountKind: "product",
      email: login.user.email,
      privilegeLevel: login.user.privilegeLevel,
      sessionExpiresAtMs: login.expiresAtMs,
    };
    this.authenticatedUser = profile;
    return profile;
  }

  public authenticatedConnectionSettings(): ConnectionSettings {
    const profile = this.authenticatedUser;
    if (!profile) throw new Error("No authenticated user session is available.");
    return {
      targetType: this.isRemoteTarget() ? "lan" : "local",
      targetId: this.target.id,
      targetName: this.target.name,
      clientApiBaseUrl: this.connection.baseUrl,
      accessToken: this.connection.accessToken,
      userId: profile.id,
      userEmail: profile.email,
      userPrivilegeLevel: profile.privilegeLevel,
    };
  }

  public async logout(): Promise<void> {
    try {
      await this.connection.logout();
    } finally {
      this.authenticatedUser = null;
      this.abortActiveRunStreams();
      this.sessionCache.clear();
    }
  }

  public async recordUserActivity(): Promise<{ expiresAtMs: number }> {
    return this.connection.recordUserActivity();
  }

  public async getDiagnostics(): Promise<ClientDiagnostics> {
    const clientApiHealthy = await this.isClientApiHealthy();
    const connection = this.connection.getSnapshot();
    const cacheEntries = this.sessionCache.getEntryCounts();
    return {
      mode: this.isRemoteTarget() ? "lan" : "local",
      target: this.target,
      openppxRoot: this.openppxRoot,
      openppxRootExists: fs.existsSync(this.openppxRoot),
      pythonBin: this.pythonBin,
      clientApiBaseUrl: connection.baseUrl,
      clientApiManagedByClient: Boolean(this.managedClientApiBindAddress()),
      clientApiHealthy,
      clientApiProductVersion: connection.nodeInfo?.productVersion ?? connection.handshake?.productVersion,
      clientApiProtocolVersion: connection.handshake?.protocolVersion,
      clientApiCompatibility: connection.nodeInfo?.compatibility ?? connection.handshake?.compatibility ?? "unknown",
      clientApiLastError: connection.lastError || undefined,
      clientApiAuthState: connection.authState,
      clientApiCredentialConfigured: connection.credentialConfigured,
      nodeId: connection.nodeInfo?.nodeId,
      nodeName: connection.nodeInfo?.displayName,
      clientApiProcessRunning: this.nodeSupervisor.processRunning,
      agentCount: connection.nodeInfo?.agents ?? 0,
      sessionCacheEntries: cacheEntries.sessions,
      messageCacheEntries: cacheEntries.messages,
      debugEnabled: clientDebugEnabled(),
    };
  }

  public async createAgent(input: AgentCreateRequest): Promise<AgentCreateResult> {
    await this.ensureClientApiAvailable();
    const result = (await this.agentManagement.create({
      ...input,
      ownerPrincipalId: this.requireUserId(),
    })).result;
    return {
      ...result,
      agent: {
        ...result.agent,
        ...normalizeAgentProfile(result.agent),
      },
    };
  }

  public async listManagedAgents(): Promise<{ agents: AgentResourceSummary[] }> {
    await this.ensureClientApiAvailable();
    const result = (await this.agentManagement.list()).result;
    return { agents: result.items };
  }

  public async updateAgent(input: AgentUpdateInput): Promise<AgentResourceSummary> {
    await this.ensureClientApiAvailable();
    return (await this.agentManagement.update(input)).result;
  }

  public async setAgentEnabled(agentId: string, enabled: boolean): Promise<AgentResourceSummary> {
    await this.ensureClientApiAvailable();
    return (await this.agentManagement.setEnabled(agentId, enabled)).result;
  }

  public async removeAgent(agentId: string, expectedRevision: string): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.agentManagement.remove(agentId, expectedRevision)).result;
  }

  public async saveConnectionSettings(settings: ConnectionSettings): Promise<ClientDiagnostics> {
    this.applyConnectionSettings(settings);
    return this.getDiagnostics();
  }

  public async testConnectionSettings(settings: ConnectionSettings): Promise<ClientDiagnostics> {
    if (settings.targetType === "local" && settings.clientApiBaseUrl === this.connection.baseUrl) {
      await this.ensureClientApiAvailable();
      const diagnostics = await this.getDiagnostics();
      if (!diagnostics.clientApiHealthy) {
        throw this.clientApiUnavailableError("Testing the local connection");
      }
      return diagnostics;
    }

    const candidate = new OpenPpxLocalAdapter(settings);
    // A changed local endpoint may be probed, but a temporary test must not own its process lifecycle.
    candidate.nodeSupervisor.setEnabled(false);
    try {
      await candidate.ensureClientApiAvailable();
      const diagnostics = await candidate.getDiagnostics();
      if (!diagnostics.clientApiHealthy) {
        const mode = settings.targetType === "local" ? "local" : "LAN";
        throw candidate.clientApiUnavailableError(`Testing the ${mode} connection`);
      }
      return diagnostics;
    } finally {
      candidate.dispose();
    }
  }

  public async runRuntimeCommand(command: RuntimeCommand): Promise<RuntimeStatus> {
    if (this.isRemoteTarget()) {
      return {
        ...this.getFallbackRuntimeStatus(),
        summary: "Remote Node process control is not supported from Desktop.",
        detail: `This target is configured as remote. Manage the Node process on ${this.connection.baseUrl}.`,
      };
    }
    if (command === "stop") {
      await this.stopManagedClientApi();
      return {
        ...this.getFallbackRuntimeStatus(),
        state: "stopped",
        summary: "Local client-api process was stopped.",
        detail: "The next request can start it again on demand.",
      };
    }
    if (command === "restart") {
      await this.stopManagedClientApi();
    }
    if (command === "start" || command === "restart") {
      await this.ensureClientApiAvailable();
    }
    return this.fetchRuntimeStatus();
  }

  public async getSetupStatus(): Promise<SetupStatusResult> {
    await this.ensureClientApiAvailable();
    return (await this.setup.status()).result;
  }

  public async getSetupReadiness(): Promise<SetupReadinessResult> {
    await this.ensureClientApiAvailable();
    return (await this.setup.readiness()).result;
  }

  public async applySetup(request: SetupApplyRequest): Promise<SetupApplyResult> {
    await this.ensureClientApiAvailable();
    return (await this.setup.apply(request)).result;
  }

  public async runSetupHello(agentId: string, userId: string, text: string): Promise<SetupHelloResult> {
    await this.ensureClientApiAvailable();
    const result = (await this.setup.hello(agentId, userId, text)).result;
    this.sessionCache.clear();
    return result;
  }

  public async getProviderModels(providerId: string): Promise<ModelCatalogResult> {
    await this.ensureClientApiAvailable();
    return (await this.models.catalog(providerId)).result;
  }

  public async getProviderAuthStatus(providerId: string): Promise<ProviderAuthStatus> {
    await this.ensureClientApiAvailable();
    return (await this.models.authStatus(providerId)).result;
  }

  public async beginProviderAuth(providerId: string): Promise<ProviderAuthStatus> {
    await this.ensureClientApiAvailable();
    return (await this.models.beginAuth(providerId)).result;
  }

  public async refreshProviderAuth(providerId: string): Promise<ProviderAuthStatus> {
    await this.ensureClientApiAvailable();
    return (await this.models.refreshAuth(providerId)).result;
  }

  public async listModelProfiles(): Promise<{ profiles: ModelProfileSummary[] }> {
    await this.ensureClientApiAvailable();
    const envelope = await this.models.list();
    const profiles = envelope.result.items.map((item) => ({
      id: String(item.id ?? ""),
      displayName: String(item.displayName ?? ""),
      revision: String(item.revision ?? ""),
      provider: String(item.provider ?? ""),
      model: String(item.model ?? ""),
      enabled: item.enabled !== false,
      credentialState: String(item.credentialState ?? "unknown"),
    }));
    return { profiles };
  }

  public async readModelProfile(profileId: string): Promise<ModelProfileResourceResult> {
    await this.ensureClientApiAvailable();
    return (await this.models.readProfile(profileId)).result;
  }

  public async createModelProfile(input: ModelProfileCreateInput): Promise<ModelProfileResourceResult> {
    await this.ensureClientApiAvailable();
    return (await this.models.createProfile(input)).result;
  }

  public async updateModelProfile(input: ModelProfileUpdateInput): Promise<ModelProfileResourceResult> {
    await this.ensureClientApiAvailable();
    return (await this.models.updateProfile(input)).result;
  }

  public async getOperationsOverview(): Promise<OperationsOverviewResult> {
    await this.ensureClientApiAvailable();
    return (await this.operations.overview()).result;
  }

  public async listOperationsAudit(limit = 20): Promise<{ items: OperationsAuditItem[] }> {
    await this.ensureClientApiAvailable();
    const result = await this.operations.audit({ limit });
    return {
      items: result.result.items.map((item) => ({
        id: String(item.id ?? ""),
        recordedAt: String(item.recordedAt ?? ""),
        completedAt: typeof item.completedAt === "string" ? item.completedAt : null,
        actorId: String(item.actorId ?? ""),
        actionId: String(item.actionId ?? ""),
        risk: item.risk === "high" || item.risk === "medium" ? item.risk : "low",
        decisionCode: String(item.decisionCode ?? "unknown"),
        outcomeCode: typeof item.outcomeCode === "string" ? item.outcomeCode : null,
        ok: typeof item.ok === "boolean" ? item.ok : null,
      })),
    };
  }

  public async getOperationsDashboard(): Promise<OperationsDashboard> {
    await this.ensureClientApiAvailable();
    const [overview, tasks, cron, heartbeat, compaction, usage, audit] = await Promise.all([
      this.operations.overview(),
      this.operations.tasks(null, 50),
      this.operations.cron(true, 30),
      this.operations.heartbeat(),
      this.operations.contextCompaction(),
      this.operations.usage(30),
      this.operations.audit({ limit: 50 }),
    ]);
    return {
      overview: overview.result,
      tasks: tasks.result,
      cron: cron.result,
      heartbeat: heartbeat.result,
      compaction: compaction.result,
      usage: usage.result,
      audit: audit.result.items.map((item) => this.projectAuditItem(item)),
    };
  }

  /** Read the unfinished Goal bound to one Session from the Node-owned Goal store. */
  public async getCurrentGoal(sessionId: string): Promise<{ goal: GoalDetail | null }> {
    await this.ensureClientApiAvailable();
    const listed = await this.goals.list(this.requireUserId(), {
      sessionId,
      statuses: ["active", "waiting", "paused", "blocked"],
      limit: 1,
    });
    const summary = listed.result.items[0];
    if (!summary) {
      return { goal: null };
    }
    return { goal: (await this.goals.read(summary.goalId, this.requireUserId())).result };
  }

  /** Update one unfinished Goal without letting the Renderer choose its owner. */
  public async updateGoal(input: GoalUpdateRequest): Promise<GoalDetail> {
    await this.ensureClientApiAvailable();
    return (await this.goals.update({ ...input, userId: this.requireUserId() })).result;
  }

  /** Apply one explicit Goal lifecycle transition under optimistic concurrency. */
  public async transitionGoal(
    operation: GoalTransitionOperation,
    goalId: string,
    expectedRevision: number,
  ): Promise<GoalDetail> {
    await this.ensureClientApiAvailable();
    return (await this.goals.transition(operation, {
      goalId,
      userId: this.requireUserId(),
      expectedRevision,
    })).result;
  }

  /** Retry the recoverable blocked TaskFlow step selected by the Node supervisor. */
  public async retryGoalStep(
    goalId: string,
    expectedRevision: number,
    stepId: string | null = null,
  ): Promise<GoalDetail> {
    await this.ensureClientApiAvailable();
    return (await this.goals.retryStep({
      goalId,
      userId: this.requireUserId(),
      expectedRevision,
      stepId,
    })).result;
  }

  /** List visible user Automations without exposing internal Cron records. */
  public async listAutomations(statuses: AutomationStatus[] = []): Promise<{ automations: AutomationSummary[] }> {
    await this.ensureClientApiAvailable();
    const envelope = await this.automations.list(this.requireUserId(), statuses);
    return { automations: envelope.result.items };
  }

  public async getAutomation(automationId: string): Promise<AutomationDetail> {
    await this.ensureClientApiAvailable();
    return (await this.automations.read(automationId, this.requireUserId())).result;
  }

  public async createAutomation(input: AutomationCreateInput): Promise<AutomationDetail> {
    await this.ensureClientApiAvailable();
    return (await this.automations.create({ ...input, userId: this.requireUserId() })).result;
  }

  public async updateAutomation(input: AutomationUpdateRequest): Promise<AutomationDetail> {
    await this.ensureClientApiAvailable();
    return (await this.automations.update({ ...input, userId: this.requireUserId() })).result;
  }

  public async transitionAutomation(
    operation: "pause" | "resume" | "delete",
    automationId: string,
    expectedRevision: number,
  ): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.automations.transition(
      operation,
      { automationId, userId: this.requireUserId(), expectedRevision },
      operation === "delete",
    )).result;
  }

  public async runAutomation(automationId: string, input: Record<string, unknown> = {}): Promise<AutomationRunSummary> {
    await this.ensureClientApiAvailable();
    return (await this.automations.run(automationId, this.requireUserId(), input)).result.run;
  }

  public async getAutomationHistory(automationId: string): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.automations.history(automationId, this.requireUserId())).result;
  }

  public async listAutomationTemplates(): Promise<{ templates: AutomationTemplateSummary[] }> {
    await this.ensureClientApiAvailable();
    return { templates: (await this.automations.templates()).result.items };
  }

  public async getOperationsTask(taskId: string): Promise<OperationsTaskDetailResult> {
    await this.ensureClientApiAvailable();
    return (await this.operations.task(taskId)).result;
  }

  public async getOperationsTaskOutput(taskId: string): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.operations.taskOutput(taskId)).result;
  }

  public async controlOperationsTask(input: OperationsTaskControlInput): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.operations.controlTask(input, true)).result;
  }

  public async createOperationsCron(input: CronCreateInput): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.operations.createCron(input, true)).result;
  }

  public async updateOperationsCron(input: CronUpdateInput): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.operations.updateCron(input, true)).result;
  }

  public async setOperationsCronEnabled(jobId: string, enabled: boolean): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.operations.setCronEnabled(jobId, enabled, true)).result;
  }

  public async runOperationsCron(jobId: string): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.operations.runCron(jobId, true, true)).result;
  }

  public async removeOperationsCron(jobId: string): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.operations.removeCron(jobId, true)).result;
  }

  public async runOperationsHeartbeat(): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.operations.runHeartbeat("desktop", true)).result;
  }

  public async configureOperationsHeartbeat(input: HeartbeatConfiguration): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.operations.configureHeartbeat(input, true)).result;
  }

  public async configureOperationsContextCompaction(input: ContextCompactionConfiguration): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.operations.configureContextCompaction(input, true)).result;
  }

  private projectAuditItem(item: Record<string, unknown>): OperationsAuditItem {
    return {
      id: String(item.id ?? ""),
      recordedAt: String(item.recordedAt ?? ""),
      completedAt: typeof item.completedAt === "string" ? item.completedAt : null,
      actorId: String(item.actorId ?? ""),
      actionId: String(item.actionId ?? ""),
      risk: item.risk === "high" || item.risk === "medium" ? item.risk : "low",
      decisionCode: String(item.decisionCode ?? "unknown"),
      outcomeCode: typeof item.outcomeCode === "string" ? item.outcomeCode : null,
      ok: typeof item.ok === "boolean" ? item.ok : null,
    };
  }

  public async listExtensions(): Promise<{ extensions: ExtensionSummary[] }> {
    await this.ensureClientApiAvailable();
    const envelope = await this.extensions.list();
    return { extensions: envelope.result.items };
  }

  public async listExtensionStarters(
    kind?: ExtensionSummary["kind"],
    query?: string,
  ): Promise<{ starters: import("@openppx/client").ExtensionStarter[]; counts: Record<ExtensionSummary["kind"], number> }> {
    await this.ensureClientApiAvailable();
    const envelope = await this.extensions.listStarters({ kind, query });
    return { starters: envelope.result.items, counts: envelope.result.counts };
  }

  public async installAppStarter(starterId: string): Promise<ExtensionMutationResult> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.installAppStarter(starterId)).result as ExtensionMutationResult;
  }

  public async getExtension(
    kind: ExtensionSummary["kind"],
    extensionId: string,
  ): Promise<{ extension: ExtensionDetail }> {
    await this.ensureClientApiAvailable();
    const envelope = await this.extensions.get(kind, extensionId);
    return { extension: envelope.result };
  }

  public async getExtensionReadiness(
    kind: ExtensionSummary["kind"],
    extensionId: string,
  ): Promise<ExtensionReadinessResult> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.readiness(kind, extensionId)).result;
  }

  public async getExtensionHealthHistory(
    kind: "mcp" | "app_connection",
    extensionId: string,
    limit = 10,
  ): Promise<ExtensionHealthHistory> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.healthHistory(kind, extensionId, limit)).result;
  }

  public async previewExtension(input: ExtensionPreviewRequest): Promise<ExtensionPreview> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.preview(input.kind, input.source)).result;
  }

  public async installExtension(input: ExtensionInstallRequest): Promise<ExtensionMutationResult> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.install(
      input.kind,
      input.source,
      input.expectedDigest,
      input.expectedRevision,
    )).result;
  }

  public async setExtensionAgentEnabled(
    input: ExtensionEnablementRequest,
  ): Promise<{ revision: string; status: string }> {
    await this.ensureClientApiAvailable();
    const envelope = input.enabled
      ? await this.extensions.enable(
          input.kind,
          input.extensionId,
          input.agentId,
          input.expectedRevision,
        )
      : await this.extensions.disable(
          input.kind,
          input.extensionId,
          input.agentId,
          input.expectedRevision,
        );
    return {
      revision: String(envelope.result.revision ?? ""),
      status: String(envelope.result.status ?? ""),
    };
  }

  public async removeExtension(input: ExtensionRemoveRequest): Promise<ExtensionMutationResult> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.remove(
      input.kind,
      input.extensionId,
      input.expectedRevision,
    )).result;
  }

  public async getPluginHookStatus(pluginId: string, expectedRevision: string): Promise<PluginHookStatus> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.getPluginHookStatus(pluginId, expectedRevision)).result;
  }

  public async setPluginHookTrust(
    pluginId: string,
    expectedRevision: string,
    trusted: boolean,
  ): Promise<PluginHookStatus> {
    await this.ensureClientApiAvailable();
    return trusted
      ? (await this.extensions.trustPluginHooks(pluginId, expectedRevision)).result
      : (await this.extensions.untrustPluginHooks(pluginId, expectedRevision)).result;
  }

  public async listPluginMarketplaces(): Promise<{ marketplaces: PluginMarketplaceSource[] }> {
    await this.ensureClientApiAvailable();
    return { marketplaces: (await this.extensions.listPluginMarketplaces()).result.items };
  }

  public async listPluginMarketplaceEntries(query?: string): Promise<{ entries: PluginMarketplaceEntry[] }> {
    await this.ensureClientApiAvailable();
    return { entries: (await this.extensions.listPluginMarketplaceEntries(query)).result.items };
  }

  public async savePluginMarketplace(input: {
    marketplaceId: string;
    spec: PluginMarketplaceSourceSpec;
    expectedRevision: string | null;
  }): Promise<PluginMarketplaceSource> {
    await this.ensureClientApiAvailable();
    return input.expectedRevision
      ? (await this.extensions.updatePluginMarketplace(
          input.marketplaceId,
          input.spec,
          input.expectedRevision,
        )).result
      : (await this.extensions.createPluginMarketplace(input.marketplaceId, input.spec)).result;
  }

  public async refreshPluginMarketplace(
    marketplaceId: string,
    expectedRevision: string,
  ): Promise<PluginMarketplaceSource> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.refreshPluginMarketplace(marketplaceId, expectedRevision)).result;
  }

  public async removePluginMarketplace(marketplaceId: string, expectedRevision: string): Promise<void> {
    await this.ensureClientApiAvailable();
    await this.extensions.removePluginMarketplace(marketplaceId, expectedRevision);
  }

  public async createMcpServer(input: McpMutationRequest): Promise<ExtensionMutationResult> {
    await this.ensureClientApiAvailable();
    if (input.expectedRevision !== null) {
      throw new TypeError("A new MCP Server requires an empty revision precondition.");
    }
    const desiredAgentIds = [...input.resource.spec.enabledAgentIds];
    const staged = await stageMcpSecrets(this.secrets, {
      ...input.resource,
      spec: { ...input.resource.spec, enabledAgentIds: [] },
    }, input.secretValues);
    try {
      const created = (await this.extensions.createMcp(staged.resource)).result;
      if (staged.resource.spec.transport.type !== "stdio" && staged.resource.spec.transport.auth === "oauth") {
        return created;
      }
      return await this.reconcileMcpAgents(
        staged.resource.metadata.name,
        String(created.revision ?? ""),
        [],
        desiredAgentIds,
      );
    } catch (error) {
      await cleanupSecrets(this.secrets, staged.createdRefs);
      throw error;
    }
  }

  public async updateMcpServer(input: McpMutationRequest): Promise<ExtensionMutationResult> {
    await this.ensureClientApiAvailable();
    if (!input.expectedRevision) {
      throw new TypeError("An existing MCP Server requires its current revision.");
    }
    const desiredAgentIds = [...input.resource.spec.enabledAgentIds];
    const current = (await this.extensions.get("mcp", input.resource.metadata.name)).result;
    const currentAgentIds = [...current.enabledAgentIds];
    const staged = await stageMcpSecrets(this.secrets, input.resource, input.secretValues);
    try {
      const updated = (await this.extensions.updateMcp(staged.resource, input.expectedRevision)).result;
      if (staged.resource.spec.transport.type !== "stdio" && staged.resource.spec.transport.auth === "oauth") {
        return updated;
      }
      return await this.reconcileMcpAgents(
        staged.resource.metadata.name,
        String(updated.revision ?? ""),
        currentAgentIds,
        desiredAgentIds,
      );
    } catch (error) {
      await cleanupSecrets(this.secrets, staged.createdRefs);
      throw error;
    }
  }

  public async beginMcpOAuth(serverId: string): Promise<McpOAuthStatus> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.beginMcpOAuth(serverId, this.connection.baseUrl)).result;
  }

  public async getMcpOAuthStatus(serverId: string): Promise<McpOAuthStatus> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.getMcpOAuthStatus(serverId)).result;
  }

  public async signOutMcpOAuth(serverId: string): Promise<McpOAuthStatus> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.signOutMcpOAuth(serverId)).result;
  }

  private async reconcileMcpAgents(
    serverId: string,
    initialRevision: string,
    currentAgentIds: string[],
    desiredAgentIds: string[],
  ): Promise<ExtensionMutationResult> {
    /** Apply Agent access one revision at a time after the MCP definition is durable. */
    let revision = initialRevision;
    let status = "installed";
    const current = new Set(currentAgentIds);
    const desired = new Set(desiredAgentIds);
    for (const agentId of currentAgentIds.filter((item) => !desired.has(item))) {
      const result = (await this.extensions.disable("mcp", serverId, agentId, revision)).result;
      revision = String(result.revision ?? revision);
      status = String(result.status ?? status);
    }
    for (const agentId of desiredAgentIds.filter((item) => !current.has(item))) {
      const result = (await this.extensions.enable("mcp", serverId, agentId, revision)).result;
      revision = String(result.revision ?? revision);
      status = String(result.status ?? status);
    }
    return { revision, status };
  }

  public async testMcpServer(serverId: string): Promise<ExtensionProbeResult> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.testMcp(serverId)).result;
  }

  public async saveAppConnection(input: AppConnectionSaveRequest): Promise<ExtensionMutationResult> {
    await this.ensureClientApiAvailable();
    const app = (await this.extensions.get("app", input.appId)).result;
    const details = app.details;
    const credentials = Array.isArray(details.credentials)
      ? details.credentials.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
      : [];
    const connections = Array.isArray(details.connections)
      ? details.connections.filter((item): item is AppConnectionDetail => Boolean(item) && typeof item === "object" && !Array.isArray(item))
      : [];
    const current = connections.find((item) => item.id === input.connectionId) ?? null;
    if ((input.expectedRevision === null) === Boolean(current)) {
      throw new Error(current
        ? "This App Connection already exists. Refresh Extensions and edit it instead."
        : "This App Connection changed or no longer exists. Refresh Extensions and retry.");
    }

    const credentialRefs = { ...(current?.credentialRefs ?? {}) };
    const createdRefs: Array<{ store: "system"; name: string }> = [];
    try {
      for (const credential of credentials) {
        const slot = String(credential.name ?? "");
        if (!slot) continue;
        const nextValue = input.credentialValues[slot] ?? "";
        if (nextValue.trim()) {
          const reference = {
            store: "system" as const,
            name: appCredentialRefName(input.connectionId, slot),
          };
          await this.secrets.put(reference, nextValue);
          credentialRefs[slot] = reference;
          createdRefs.push(reference);
        }
        if (credential.required === true && !credentialRefs[slot]) {
          throw new Error(`${String(credential.label ?? slot)} is required.`);
        }
      }
    } catch (error) {
      await cleanupSecrets(this.secrets, createdRefs);
      throw error;
    }

    const resource = {
      apiVersion: "openppx.io/v1alpha1" as const,
      kind: "AppConnection" as const,
      metadata: { name: input.connectionId },
      spec: {
        appId: input.appId,
        displayName: input.displayName,
        credentialRefs: current?.credentialRefs ?? credentialRefs,
        enabledTools: input.enabledTools,
        requireConfirmation: input.requireConfirmation,
        enabledAgentIds: [],
      },
    };

    try {
      if (!current) {
        return (await this.extensions.createAppConnection(resource)).result;
      }
      const updated = await this.extensions.updateAppConnection(resource, input.expectedRevision!);
      if (!createdRefs.length) {
        return updated.result;
      }
      return (await this.extensions.reauthorizeAppConnection(
        input.connectionId,
        credentialRefs,
        String(updated.result.revision ?? ""),
      )).result;
    } catch (error) {
      await cleanupSecrets(this.secrets, createdRefs);
      throw error;
    }
  }

  public async testAppConnection(connectionId: string): Promise<ExtensionProbeResult> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.testAppConnection(connectionId)).result;
  }

  public async setAppConnectionAgentEnabled(
    input: AppConnectionEnablementRequest,
  ): Promise<ExtensionMutationResult> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.setAppConnectionEnabled(
      input.connectionId,
      input.agentId,
      input.expectedRevision,
      input.enabled,
    )).result;
  }

  public async removeAppConnection(input: AppConnectionRemoveRequest): Promise<ExtensionMutationResult> {
    await this.ensureClientApiAvailable();
    return (await this.extensions.removeAppConnection(
      input.connectionId,
      input.expectedRevision,
    )).result;
  }

  public async listSessions(agentId: string): Promise<{ sessions: SessionSummary[] }> {
    const cached = this.sessionCache.readSessions(agentId);
    if (cached) {
      clientDebugLog("sessions.cache.hit", {
        agentId,
        count: cached.length,
      });
      return { sessions: cached };
    }
    if (!(await this.ensureClientApiAvailable())) {
      throw this.clientApiUnavailableError("Listing sessions");
    }
    try {
      const payload = await this.fetchClientApiJson(`/api/v1/agents/${agentId}/sessions`);
      const items = Array.isArray((payload.data as Record<string, unknown> | undefined)?.items)
        ? ((payload.data as Record<string, unknown>).items as unknown[])
        : [];
      const sessions = items
        .map((item) => normalizeClientApiSession(item))
        .filter((item): item is SessionSummary => item !== null);
      this.sessionCache.writeSessions(agentId, sessions);
      return { sessions };
    } catch (error) {
      this.rememberClientApiError(error);
      throw this.clientApiUnavailableError("Listing sessions");
    }
  }

  public async createSession(agentId: string): Promise<{ session: SessionSummary }> {
    if (!(await this.ensureClientApiAvailable())) {
      throw this.clientApiUnavailableError("Creating a session");
    }
    try {
      const outcome = await this.actions.invoke<
        { agentId: string; userId: string },
        { session: Record<string, unknown> }
      >("session.new", { agentId, userId: this.requireUserId() });
      const session = normalizeClientApiSession(outcome.result.session);
      if (!session) {
        throw new Error("Node returned an invalid session payload.");
      }
      this.sessionCache.invalidate(agentId);
      return { session };
    } catch (error) {
      this.rememberClientApiError(error);
      throw this.clientApiUnavailableError("Creating a session");
    }
  }

  public async renameSession(input: SessionMutationRequest & { title: string }): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    const result = (await this.sessions.rename(input.agentId, this.requireUserId(), input.sessionId, input.title)).result;
    this.sessionCache.invalidate(input.agentId, input.sessionId);
    return result;
  }

  public async archiveSession(input: SessionMutationRequest & { archived: boolean }): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    const result = (await this.sessions.archive(input.agentId, this.requireUserId(), input.sessionId, input.archived)).result;
    this.sessionCache.invalidate(input.agentId, input.sessionId);
    return result;
  }

  public async forkSession(input: SessionMutationRequest): Promise<{ session: SessionSummary }> {
    await this.ensureClientApiAvailable();
    const result = (await this.sessions.fork(input.agentId, this.requireUserId(), input.sessionId)).result;
    const session = normalizeClientApiSession(result.session);
    if (!session) throw new Error("Node returned an invalid forked Session.");
    this.sessionCache.invalidate(input.agentId);
    return { session };
  }

  public async exportSession(input: SessionMutationRequest): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    return (await this.sessions.export(input.agentId, this.requireUserId(), input.sessionId)).result;
  }

  public async deleteSession(input: SessionMutationRequest): Promise<Record<string, unknown>> {
    await this.ensureClientApiAvailable();
    const result = (await this.sessions.remove(input.agentId, this.requireUserId(), input.sessionId)).result;
    this.sessionCache.invalidate(input.agentId, input.sessionId);
    return result;
  }

  public async loadSession(sessionId: string): Promise<{ messages: ChatMessage[] }> {
    const cached = this.sessionCache.readMessages(sessionId);
    if (cached) {
      clientDebugLog("messages.cache.hit", {
        sessionId,
        count: cached.length,
      });
      return { messages: cached };
    }
    if (await this.ensureClientApiAvailable()) {
      try {
        const payload = await this.fetchClientApiJson(`/api/v1/sessions/${sessionId}/messages`);
        const items = Array.isArray((payload.data as Record<string, unknown> | undefined)?.items)
          ? ((payload.data as Record<string, unknown>).items as unknown[])
          : [];
        const messages = items
          .map((item) => normalizeClientApiMessage(item))
          .filter((item): item is ChatMessage => item !== null);
        this.sessionCache.writeMessages(sessionId, messages);
        return { messages };
      } catch (error) {
        this.rememberClientApiError(error);
      }
    }
    throw this.clientApiUnavailableError("Loading session messages");
  }

  public async setResponseFeedback(
    input: ResponseFeedbackInput,
  ): Promise<{ responseId: string; rating: "up" | "down" | null }> {
    await this.ensureClientApiAvailable();
    const payload = await this.fetchClientApiJson(
      `/api/v1/sessions/${encodeURIComponent(input.sessionId)}/responses/${encodeURIComponent(input.responseId)}/feedback`,
      {
        method: "POST",
        body: JSON.stringify({
          messageId: input.messageId,
          runId: input.runId ?? null,
          rating: input.rating,
        }),
      },
    );
    const data = payload.data && typeof payload.data === "object" && !Array.isArray(payload.data)
      ? payload.data as Record<string, unknown>
      : {};
    const responseId = String(data.response_id ?? data.responseId ?? "").trim();
    const rating = data.rating === "up" || data.rating === "down" ? data.rating : null;
    if (!responseId) throw new Error("Node returned an invalid response feedback result.");
    this.sessionCache.invalidate("", input.sessionId);
    return { responseId, rating };
  }

  public async uploadArtifact(input: ArtifactUploadInput): Promise<ArtifactSummary> {
    await this.ensureClientApiAvailable();
    return this.artifacts.upload(input);
  }

  public async listArtifacts(agentId: string, sessionId: string): Promise<{ artifacts: ArtifactSummary[] }> {
    await this.ensureClientApiAvailable();
    return { artifacts: await this.artifacts.list(agentId, sessionId) };
  }

  public async downloadArtifact(
    agentId: string,
    sessionId: string,
    artifact: ArtifactSummary,
  ): Promise<{ dataBase64: string; mimeType: string }> {
    await this.ensureClientApiAvailable();
    const result = await this.artifacts.download(agentId, sessionId, artifact);
    return {
      dataBase64: Buffer.from(result.bytes).toString("base64"),
      mimeType: result.mimeType,
    };
  }

  public async sendMessage(input: SendMessageInput): Promise<{ runId: string }> {
    clientDebugLog("send.start", {
      agentId: input.agentId,
      sessionId: input.sessionId,
      textPreview: input.text.slice(0, 240),
      mode: this.isRemoteTarget() ? "remote" : "local",
    });
    this.sessionCache.invalidate(input.agentId, input.sessionId);
    return submitClientApiRunWithRecovery({
      submit: () => this.sendMessageViaClientApi(input),
      recoverAfterUndeliveredRequest: async (error) => {
        this.rememberClientApiError(error);
        clientDebugLog("send.client-api.failed", {
          agentId: input.agentId,
          sessionId: input.sessionId,
          error: error instanceof Error ? error.message : String(error),
        });
        return this.recoverManagedNodeAfterConnectionRefused(error);
      },
    });
  }

  private async recoverManagedNodeAfterConnectionRefused(error: unknown): Promise<boolean> {
    const openppxRootExists = fs.existsSync(this.openppxRoot);
    const managedBindAddress = this.managedClientApiBindAddress();
    if (
      !managedBindAddress
      || !shouldStartManagedNode({
        targetType: this.target.type,
        failure: classifyClientApiConnectionFailure(error),
        openppxRootExists,
      })
    ) {
      return false;
    }
    return this.nodeSupervisor.ensureReady({
      host: managedBindAddress.host,
      port: managedBindAddress.port,
      accessToken: this.connection.accessToken,
      baseUrl: this.connection.baseUrl,
      probe: () => this.isClientApiHealthy(),
    });
  }

  private async sendMessageViaClientApi(input: SendMessageInput): Promise<{ runId: string }> {
    const payload = await this.fetchClientApiJson(`/api/v1/agents/${input.agentId}/sessions/${input.sessionId}/runs`, {
      method: "POST",
      body: JSON.stringify({ text: input.text, artifact_refs: input.artifactRefs ?? [] }),
    });
    const run = ((payload.data as Record<string, unknown> | undefined)?.run ?? {}) as Record<string, unknown>;
    const runId = String(run.id ?? `run-${crypto.randomUUID()}`);
    clientDebugLog("send.client-api.run-created", {
      runId,
      agentId: input.agentId,
      sessionId: input.sessionId,
    });
    const session: SessionSummary = {
      id: input.sessionId,
      agentId: input.agentId,
      title: "New chat",
      updatedAt: now(),
      lastMessagePreview: input.text,
    };

    clientDebugLog("send.client-api.stream-open", {
      runId,
      url: `${this.connection.baseUrl}/api/v1/runs/${runId}/events`,
    });
    let assistantMessage: ChatMessage | null = null;
    let finalText = "";
    let orderedParts: MessagePart[] = [];
    let terminal = false;
    const syncAssistant = (status: ChatMessage["status"]): void => {
      if (!assistantMessage) {
        return;
      }
      assistantMessage.status = status;
      assistantMessage.parts = ensureRenderableAssistantParts(orderedParts);
      this.emit({
        type: "message.updated",
        runId,
        sessionId: assistantMessage.sessionId,
        messageId: assistantMessage.id,
        replaceParts: assistantMessage.parts,
        status,
      });
    };
    const handleClientApiEvent = (eventName: string, data: Record<string, unknown>): void => {
      clientDebugLog("send.client-api.event", { runId, eventName, keys: Object.keys(data) });
      if (eventName === "message.created") {
        const message = normalizeClientApiMessage(data.message);
        if (!message) {
          return;
        }
        assistantMessage = message;
        orderedParts = [...message.parts];
        finalText = latestMarkdownText(message.parts);
        this.emit({ type: "message.created", runId, sessionId: message.sessionId, message });
      } else if (eventName === "step.updated" && assistantMessage) {
        const stepPart = normalizeClientApiPart(data.step);
        if (stepPart?.type === "step_ref") {
          orderedParts = upsertOrderedStepPart(orderedParts, stepPart);
          syncAssistant("streaming");
        }
      } else if (eventName === "message.delta" && assistantMessage) {
        const part = normalizeClientApiPart(data.part);
        if (part?.type === "commentary") {
          orderedParts = appendCommentaryPart(orderedParts, part);
          syncAssistant("streaming");
        } else if (part?.type === "markdown") {
          finalText = part.text;
          orderedParts = upsertFinalMarkdownPart(orderedParts, finalText);
          syncAssistant("streaming");
        }
      } else if (eventName === "message.completed" && assistantMessage) {
        const message = normalizeClientApiMessage(data.message);
        finalText = message ? latestMarkdownText(message.parts) || finalText : finalText;
        orderedParts = upsertFinalMarkdownPart(orderedParts, finalText).map((part) => (
          part.type === "step_ref" && part.status === "running"
            ? { ...part, status: "completed" }
            : part
        ));
        syncAssistant("completed");
      } else if (eventName === "message.failed" && assistantMessage) {
        const errorPart = normalizeClientApiPart(data.error);
        if (errorPart?.type === "error") {
          this.emit({
            type: "message.updated",
            runId,
            sessionId: assistantMessage.sessionId,
            messageId: assistantMessage.id,
            replaceParts: [errorPart],
            status: "failed",
          });
        }
      } else if (eventName === "message.cancelled" && assistantMessage) {
        this.emit({
          type: "message.updated",
          runId,
          sessionId: assistantMessage.sessionId,
          messageId: assistantMessage.id,
          replaceParts: ensureRenderableAssistantParts(orderedParts.map((part) => (
            part.type === "step_ref" && part.status === "running"
              ? { ...part, status: "failed" }
              : part
          ))),
          status: "cancelled",
        });
      } else if (eventName === "run.finished") {
        const status = isTerminalRunStatus(data.status) ? data.status : "completed";
        terminal = true;
        session.updatedAt = now();
        session.lastMessagePreview = finalText || input.text;
        this.emit({ type: "session.updated", runId, session });
        this.emit({ type: "run.finished", runId, sessionId: input.sessionId, status });
        clientDebugLog("send.client-api.finished", {
          runId,
          status,
          finalTextLength: finalText.length,
        });
      } else if (eventName === "run.cancelled") {
        terminal = true;
        this.emit({ type: "run.finished", runId, sessionId: input.sessionId, status: "cancelled" });
      }
    };
    const reconcilePersistedTerminalState = async (): Promise<boolean> => {
      const statusPayload = await this.fetchClientApiJson(`/api/v1/runs/${encodeURIComponent(runId)}`);
      const runState = ((statusPayload.data as Record<string, unknown> | undefined)?.run ?? {}) as Record<string, unknown>;
      const authoritativeStatus = runState.status;
      if (!isTerminalRunStatus(authoritativeStatus)) {
        return false;
      }

      this.sessionCache.invalidate(input.agentId, input.sessionId);
      const persisted = findLatestPersistedRunMessage(
        (await this.loadSession(input.sessionId)).messages,
        runId,
      );
      const terminalMessage = persisted
        ? { ...persisted, status: authoritativeStatus }
        : null;

      const liveAssistantMessageId = assistantMessage?.id ?? null;
      if (terminalMessage) {
        assistantMessage = terminalMessage;
        orderedParts = [...terminalMessage.parts];
        finalText = latestMarkdownText(terminalMessage.parts) || finalText;
      }
      terminal = true;
      if (liveAssistantMessageId && assistantMessage) {
        this.emit({
          type: "message.updated",
          runId,
          sessionId: input.sessionId,
          messageId: liveAssistantMessageId,
          replaceParts: ensureRenderableAssistantParts(orderedParts),
          status: authoritativeStatus,
        });
      } else if (terminalMessage) {
        this.emit({ type: "message.created", runId, sessionId: input.sessionId, message: terminalMessage });
      }
      session.updatedAt = now();
      session.lastMessagePreview = finalText || input.text;
      this.emit({ type: "session.updated", runId, session });
      this.emit({ type: "run.finished", runId, sessionId: input.sessionId, status: authoritativeStatus });
      clientDebugLog("send.client-api.stream-reconciled", {
        runId,
        messageId: terminalMessage?.id ?? null,
        status: authoritativeStatus,
      });
      return true;
    };
    const streamController = new AbortController();
    this.activeRunStreams.set(runId, streamController);
    void monitorPersistedTerminalRun({
      signal: streamController.signal,
      intervalMs: 3_000,
      reconcile: async () => {
        if (terminal) {
          return true;
        }
        try {
          const reconciled = await reconcilePersistedTerminalState();
          if (reconciled) {
            streamController.abort();
          }
          return reconciled;
        } catch (error) {
          clientDebugLog("send.client-api.stream-reconcile-failed", {
            runId,
            error: error instanceof Error ? error.message : String(error),
          });
          return false;
        }
      },
    });
    void this.runStream
      .consume(runId, ({ event, data }) => handleClientApiEvent(event, data), { signal: streamController.signal })
      .then(() => clientDebugLog("send.client-api.stream-closed", { runId }))
      .catch(async (error: unknown) => {
        clientDebugLog("send.client-api.stream-error", {
          runId,
          error: error instanceof Error ? error.message : String(error),
        });
        if (terminal) {
          return;
        }
        try {
          if (await reconcilePersistedTerminalState()) {
            return;
          }
        } catch (reconciliationError) {
          clientDebugLog("send.client-api.stream-reconcile-failed", {
            runId,
            error: reconciliationError instanceof Error
              ? reconciliationError.message
              : String(reconciliationError),
          });
        }
        this.rememberClientApiError(error);
        terminal = true;
        const errorPart: MessagePart = {
          type: "error",
          text: error instanceof Error ? error.message : String(error),
          errorCode: "CLIENT_API_STREAM_ERROR",
        };
        if (assistantMessage) {
          this.emit({
            type: "message.updated",
            runId,
            sessionId: input.sessionId,
            messageId: assistantMessage.id,
            replaceParts: [errorPart],
            status: "failed",
          });
        } else {
          const failedMessage: ChatMessage = {
            id: `assistant-${runId}`,
            sessionId: input.sessionId,
            runId,
            role: "assistant",
            status: "failed",
            createdAt: now(),
            parts: [errorPart],
          };
          this.emit({ type: "message.created", runId, sessionId: input.sessionId, message: failedMessage });
        }
        this.emit({ type: "run.finished", runId, sessionId: input.sessionId, status: "failed" });
      })
      .finally(() => {
        streamController.abort();
        if (this.activeRunStreams.get(runId) === streamController) {
          this.activeRunStreams.delete(runId);
        }
      });

    return { runId };
  }

  public async cancelRun(runId: string): Promise<{ runId: string; status: "cancelled" }> {
    if (!(await this.ensureClientApiAvailable())) {
      throw this.clientApiUnavailableError("Cancelling a Run");
    }
    const outcome = await this.actions.invoke<
      { runId: string },
      { run: Record<string, unknown> }
    >("run.stop", { runId });
    const run = outcome.result.run;
    return { runId: String(run.id ?? runId), status: "cancelled" };
  }

  public async listSlashCommands(agentId?: string | null): Promise<{ commands: ProjectedSlashCommand[] }> {
    await this.ensureClientApiAvailable();
    return { commands: await this.commands.list({ agentId }) };
  }

  public async invokeSlashCommand(input: SlashCommandRequest): Promise<SlashCommandResult> {
    await this.ensureClientApiAvailable();
    const envelope = await this.commands.invoke(input.rawCommand, {
      userId: this.requireUserId(),
      agentId: input.agentId,
      sessionId: input.sessionId,
      runId: input.runId,
    });
    if (input.agentId) {
      this.sessionCache.invalidate(input.agentId, input.sessionId ?? undefined);
    }
    return envelope.result;
  }

  public onRunEvent(listener: (event: RunEvent) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  public dispose(): void {
    this.abortActiveRunStreams();
    this.sessionCache.clear();
    this.stopManagedClientApiImmediately();
  }

  private abortActiveRunStreams(): void {
    this.activeRunStreams.forEach((controller) => controller.abort());
    this.activeRunStreams.clear();
  }
}
