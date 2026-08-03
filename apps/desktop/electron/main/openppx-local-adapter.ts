import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  ActionClient,
  CLIENT_API_PROTOCOL_VERSION,
  CommandClient,
  ExtensionClient,
  ModelClient,
  SetupClient,
  normalizeClientApiMessage,
  normalizeClientApiPart,
  normalizeClientApiSession,
} from "@openppx/client";
import {
  bootstrap as mockBootstrap,
  cancelRun as mockCancelRun,
  createSession as mockCreateSession,
  listSessions as mockListSessions,
  loadSession as mockLoadSession,
  runRuntimeCommand as mockRunRuntimeCommand,
  sendMessage as mockSendMessage,
  subscribe as subscribeMock,
} from "../../app/src/lib/mock-client";
import { normalizeClientApiRuntime } from "../../app/src/lib/client-api-projection";
import { isLoopbackClientApiHostname } from "../../app/src/lib/connection-profile";
import {
  mergeAssistantParts,
  projectBridgeEventToStepParts,
} from "../../app/src/lib/openppx-projection";
import type {
  AgentProfile,
  BootstrapPayload,
  ChatMessage,
  ClientDiagnostics,
  ConnectionSettings,
  ConnectionTarget,
  ExtensionDetail,
  ExtensionEnablementRequest,
  ExtensionSummary,
  ModelProfileSummary,
  MessagePart,
  PpxClientApi,
  RunEvent,
  RuntimeCommand,
  RuntimeStatus,
  SendMessageInput,
  SessionSummary,
  SlashCommandRequest,
  ProjectedSlashCommand,
  SlashCommandResult,
  SetupApplyRequest,
  SetupApplyResult,
  SetupHelloResult,
  SetupStatusResult,
} from "../../app/src/types";
import {
  canUseLegacyBridge,
  resolveDesktopDevelopmentModes,
  shouldStartManagedClientApi,
} from "./development-modes";
import { ClientApiConnection } from "./client-api-connection";
import { ClientApiRunStream } from "./client-api-run-stream";
import { ClientApiSessionCache } from "./client-api-session-cache";
import { LegacyBridgeClient, type LegacyBridgeStreamEvent } from "./legacy-bridge-client";
import { LocalNodeSupervisor } from "./local-node-supervisor";

type EventSink = (event: RunEvent) => void;
type StepPart = Extract<MessagePart, { type: "step_ref" }>;

function now(): string {
  return new Date().toISOString();
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
  const raw = process.env.OPENPPX_CLIENT_DEBUG ?? process.env.PPX_CLIENT_DEBUG ?? "";
  return ["1", "true", "yes", "on"].includes(raw.trim().toLowerCase());
}

function clientDebugLog(tag: string, payload: unknown): void {
  if (!clientDebugEnabled()) {
    return;
  }
  console.log(`[ppx-client][debug] ${tag}`, payload);
}

function detectOpenPpxRoot(): string {
  if (process.env.OPENPPX_ROOT?.trim()) {
    return path.resolve(process.env.OPENPPX_ROOT);
  }
  return path.resolve(process.cwd(), "../..");
}

function dataRootPath(): string {
  const configured = process.env.OPENPPX_DATA_DIR?.trim() || process.env.OPENPIPIXIA_DATA_DIR?.trim();
  if (configured) {
    return path.resolve(configured);
  }
  const openppxDataRoot = path.join(os.homedir(), ".openppx");
  if (fs.existsSync(openppxDataRoot)) {
    return openppxDataRoot;
  }
  return path.join(os.homedir(), ".openpipixia");
}

function resolvePythonBin(openppxRoot: string): string {
  const venvPython = path.join(openppxRoot, ".venv", "bin", "python");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return "python3";
}

function normalizeAgentProfile(payload: Record<string, unknown>): AgentProfile {
  return {
    id: String(payload.id ?? ""),
    name: String(payload.name ?? payload.id ?? ""),
    description: String(payload.description ?? "Local openppx agent"),
    enabled: payload.enabled !== false,
    status: (String(payload.status ?? "healthy") as AgentProfile["status"]) || "healthy",
    tags: Array.isArray(payload.tags) ? payload.tags.map((tag) => String(tag)) : [],
  };
}

export class OpenPpxLocalAdapter implements PpxClientApi {
  private readonly listeners = new Set<EventSink>();

  private readonly openppxRoot = detectOpenPpxRoot();

  private readonly bridgeScriptPath = path.resolve(process.cwd(), "scripts/openppx_bridge.py");

  private readonly pythonBin = resolvePythonBin(this.openppxRoot);

  private readonly configuredClientApiBaseUrl = process.env.OPENPPX_CLIENT_API_BASE_URL?.trim() || "";

  private readonly configuredClientApiAccessToken = process.env.OPENPPX_CLIENT_API_TOKEN?.trim() || "";

  private readonly managedLocalAccessToken = randomBytes(32).toString("base64url");

  private readonly developmentModes = resolveDesktopDevelopmentModes();

  private readonly clientApiHost = process.env.OPENPPX_CLIENT_API_HOST?.trim() || "127.0.0.1";

  private readonly clientApiPort = Number(process.env.OPENPPX_CLIENT_API_PORT?.trim() || "8765");

  private target: ConnectionTarget;

  private readonly connection: ClientApiConnection;

  private readonly runStream: ClientApiRunStream;

  private readonly actions: ActionClient;

  private readonly extensions: ExtensionClient;

  private readonly commands: CommandClient;

  private readonly setup: SetupClient;

  private readonly models: ModelClient;

  private readonly nodeSupervisor: LocalNodeSupervisor;

  private readonly legacyBridge: LegacyBridgeClient;

  private readonly sessionCache = new ClientApiSessionCache();

  private readonly activeRunStreams = new Map<string, AbortController>();

  private inflightHealthCheck: Promise<boolean> | null = null;

  private readonly mockUnsubscribe = subscribeMock((event) => {
    if (this.shouldUseMock()) {
      this.emit(event);
    }
  });

  public constructor(initialSettings?: ConnectionSettings) {
    this.target = this.buildTarget();
    this.connection = new ClientApiConnection({
      baseUrl: this.configuredClientApiBaseUrl || `http://${this.clientApiHost}:${this.clientApiPort}`,
      accessToken:
        this.configuredClientApiAccessToken || (this.target.type === "local" ? this.managedLocalAccessToken : ""),
    });
    this.runStream = new ClientApiRunStream({
      request: (pathname, init) => this.connection.request(pathname, init),
    });
    this.actions = new ActionClient(this.connection);
    this.extensions = new ExtensionClient(this.actions);
    this.commands = new CommandClient(this.actions);
    this.setup = new SetupClient(this.actions);
    this.models = new ModelClient(this.actions);
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
    this.legacyBridge = new LegacyBridgeClient({
      openppxRoot: this.openppxRoot,
      pythonBin: this.pythonBin,
      scriptPath: this.bridgeScriptPath,
      spawnProcess: spawn,
      log: clientDebugLog,
    });
    if (initialSettings) {
      this.applyConnectionSettings(initialSettings);
    }
  }

  private buildTarget(): ConnectionTarget {
    const rawType = process.env.OPENPPX_TARGET_TYPE?.trim().toLowerCase() || "local";
    if (rawType === "remote" || rawType === "lan") {
      return {
        id: process.env.OPENPPX_TARGET_ID?.trim() || "remote-default",
        type: "remote",
        name: process.env.OPENPPX_TARGET_NAME?.trim() || "Remote Gateway",
      };
    }
    return {
      id: process.env.OPENPPX_TARGET_ID?.trim() || "local-default",
      type: "local",
      name: process.env.OPENPPX_TARGET_NAME?.trim() || "This Mac",
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
      name: settings.targetName.trim() || (isLan ? "LAN OpenPPX Node" : "This Mac"),
    };
    this.connection.configure({
      baseUrl: nextBaseUrl,
      accessToken: isLan
        ? settings.accessToken?.trim() || this.configuredClientApiAccessToken
        : this.configuredClientApiAccessToken || this.managedLocalAccessToken,
    });
    this.sessionCache.clear();
    if (shouldStopManagedProcess) {
      this.stopManagedClientApiImmediately();
    }
    this.abortActiveRunStreams();
  }

  private canUseLegacyLocalFallback(): boolean {
    return canUseLegacyBridge(this.target.type, this.developmentModes);
  }

  private emit(event: RunEvent): void {
    this.sessionCache.applyEvent(event);
    this.listeners.forEach((listener) => listener(event));
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
      !shouldStartManagedClientApi({
        targetType: this.target.type,
        endpointReachable: connection.reachable,
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

  private formatSessionSummary(agentId: string, payload: Record<string, unknown>): SessionSummary {
    const updatedAt =
      typeof payload.last_update_time === "number" ? new Date(payload.last_update_time * 1000).toISOString() : now();
    return {
      id: String(payload.id ?? ""),
      agentId,
      title: `Session ${String(payload.id ?? "").slice(0, 8)}`,
      updatedAt,
      lastMessagePreview: typeof payload.last_preview === "string" ? payload.last_preview : "",
    };
  }

  private shouldUseMock(): boolean {
    return this.developmentModes.mockEnabled;
  }

  private getFallbackRuntimeStatus(): RuntimeStatus {
    const connection = this.connection.getSnapshot();
    if (this.isRemoteTarget()) {
      return {
        target: this.target,
        state: "error",
        summary: "Remote client-api gateway is unavailable.",
        detail: connection.lastError || `Check the remote gateway at ${connection.baseUrl}.`,
        lastError: "REMOTE_GATEWAY_UNAVAILABLE",
      };
    }
    if (!fs.existsSync(this.openppxRoot)) {
      return {
        target: this.target,
        state: "error",
        summary: "openppx root was not found.",
        detail: "Set OPENPPX_ROOT or run Desktop from apps/desktop in the OpenPPX repository.",
        lastError: "OPENPPX_ROOT_NOT_FOUND",
      };
    }
    if (this.canUseLegacyLocalFallback()) {
      return {
        target: this.target,
        state: "healthy",
        summary: "Legacy local bridge is enabled for development.",
        detail: "Client API is unavailable; OPENPPX_DESKTOP_LEGACY_BRIDGE explicitly permits the bridge.",
      };
    }
    return {
      target: this.target,
      state: "error",
      summary: "OpenPPX Client API is unavailable.",
      detail: connection.lastError || `Start a compatible protocol v${CLIENT_API_PROTOCOL_VERSION} gateway.`,
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
    if (this.shouldUseMock()) {
      return mockBootstrap();
    }

    const runtime = await this.fetchRuntimeStatus();
    if (runtime.state !== "healthy" && !this.canUseLegacyLocalFallback()) {
      return emptyBootstrap(runtime);
    }
    const clientApiHealthy = await this.isClientApiHealthy();
    if (!clientApiHealthy && !this.canUseLegacyLocalFallback()) {
      return emptyBootstrap(runtime);
    }

    let agents: AgentProfile[] = [];
    if (clientApiHealthy) {
      try {
        const payload = await this.fetchClientApiJson("/api/v1/agents");
        const items = Array.isArray((payload.data as Record<string, unknown> | undefined)?.items)
          ? ((payload.data as Record<string, unknown>).items as Array<Record<string, unknown>>)
          : [];
        agents = items.map((item) => normalizeAgentProfile(item));
      } catch (error) {
        this.rememberClientApiError(error);
        if (!this.canUseLegacyLocalFallback()) {
          throw this.clientApiUnavailableError("Loading agents");
        }
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

  public async getDiagnostics(): Promise<ClientDiagnostics> {
    const clientApiHealthy = this.shouldUseMock() ? false : await this.isClientApiHealthy();
    const connection = this.connection.getSnapshot();
    const cacheEntries = this.sessionCache.getEntryCounts();
    return {
      mode: this.shouldUseMock() ? "mock" : this.isRemoteTarget() ? "lan" : "local",
      target: this.target,
      openppxRoot: this.openppxRoot,
      openppxRootExists: fs.existsSync(this.openppxRoot),
      pythonBin: this.pythonBin,
      clientApiBaseUrl: connection.baseUrl,
      clientApiManagedByClient: !this.shouldUseMock() && Boolean(this.managedClientApiBindAddress()),
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
      bridgeScriptPath: this.bridgeScriptPath,
      bridgeScriptExists: fs.existsSync(this.bridgeScriptPath),
      agentCount: connection.nodeInfo?.agents ?? 0,
      sessionCacheEntries: cacheEntries.sessions,
      messageCacheEntries: cacheEntries.messages,
      debugEnabled: clientDebugEnabled(),
      mockEnabled: this.developmentModes.mockEnabled,
      legacyBridgeEnabled: this.developmentModes.legacyBridgeEnabled,
    };
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
    if (this.shouldUseMock()) {
      return mockRunRuntimeCommand(command);
    }
    if (this.isRemoteTarget()) {
      return {
        ...this.getFallbackRuntimeStatus(),
        summary: "Remote gateway control is not supported from the desktop client yet.",
        detail: `This target is configured as remote. Manage the gateway directly at ${this.connection.baseUrl}.`,
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
    if (this.shouldUseMock()) {
      return {
        state: "ready",
        steps: { node: "complete", agent: "complete", model: "complete", credential: "not_required", hello: "verified" },
        revisions: { node: "mock", agent: "mock", profile: "mock" },
        recommendedWorkspace: "",
        current: { node: null, agent: null, profile: null },
        providers: [],
      };
    }
    await this.ensureClientApiAvailable();
    return (await this.setup.status()).result;
  }

  public async applySetup(request: SetupApplyRequest): Promise<SetupApplyResult> {
    if (this.shouldUseMock()) {
      throw new Error("Setup is unavailable in mock mode.");
    }
    await this.ensureClientApiAvailable();
    return (await this.setup.apply(request)).result;
  }

  public async runSetupHello(agentId: string, userId: string, text: string): Promise<SetupHelloResult> {
    if (this.shouldUseMock()) {
      throw new Error("Setup Hello is unavailable in mock mode.");
    }
    await this.ensureClientApiAvailable();
    const result = (await this.setup.hello(agentId, userId, text)).result;
    this.sessionCache.clear();
    return result;
  }

  public async listModelProfiles(): Promise<{ profiles: ModelProfileSummary[] }> {
    if (this.shouldUseMock()) {
      return { profiles: [] };
    }
    await this.ensureClientApiAvailable();
    const envelope = await this.models.list();
    const profiles = envelope.result.items.map((item) => ({
      id: String(item.id ?? ""),
      revision: String(item.revision ?? ""),
      provider: String(item.provider ?? ""),
      model: String(item.model ?? ""),
      enabled: item.enabled !== false,
      credentialState: String(item.credentialState ?? "unknown"),
    }));
    return { profiles };
  }

  public async listExtensions(): Promise<{ extensions: ExtensionSummary[] }> {
    if (this.shouldUseMock()) {
      return { extensions: [] };
    }
    await this.ensureClientApiAvailable();
    const envelope = await this.extensions.list();
    return { extensions: envelope.result.items };
  }

  public async getExtension(
    kind: ExtensionSummary["kind"],
    extensionId: string,
  ): Promise<{ extension: ExtensionDetail }> {
    await this.ensureClientApiAvailable();
    const envelope = await this.extensions.get(kind, extensionId);
    return { extension: envelope.result };
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

  public async listSessions(agentId: string): Promise<{ sessions: SessionSummary[] }> {
    if (this.shouldUseMock()) {
      return mockListSessions(agentId);
    }
    const cached = this.sessionCache.readSessions(agentId);
    if (cached) {
      clientDebugLog("sessions.cache.hit", {
        agentId,
        count: cached.length,
      });
      return { sessions: cached };
    }
    if (await this.ensureClientApiAvailable()) {
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
      }
    }
    if (!this.canUseLegacyLocalFallback()) {
      throw this.clientApiUnavailableError("Listing sessions");
    }
    const sessions = await this.listSessionsForAgent(agentId);
    this.sessionCache.writeSessions(agentId, sessions);
    return { sessions };
  }

  public async createSession(agentId: string): Promise<{ session: SessionSummary }> {
    if (this.shouldUseMock()) {
      return mockCreateSession(agentId);
    }
    if (await this.ensureClientApiAvailable()) {
      try {
        const outcome = await this.actions.invoke<
          { agentId: string; userId: string },
          { session: Record<string, unknown> }
        >("session.new", { agentId, userId: "ppx-client-user" });
        const session = normalizeClientApiSession(outcome.result.session);
        if (session) {
          this.sessionCache.invalidate(agentId);
          return { session };
        }
      } catch (error) {
        this.rememberClientApiError(error);
      }
    }
    if (!this.canUseLegacyLocalFallback()) {
      throw this.clientApiUnavailableError("Creating a session");
    }
    const response = await this.legacyBridge.request<{ session?: Record<string, unknown> }>("create_session", agentId, [
      "--session-id",
      `${agentId}-${crypto.randomUUID()}`,
    ]);
    const session = this.formatSessionSummary(agentId, response?.session ?? {});
    this.sessionCache.invalidate(agentId);
    return { session };
  }

  public async loadSession(sessionId: string): Promise<{ messages: ChatMessage[] }> {
    if (this.shouldUseMock()) {
      return mockLoadSession(sessionId);
    }
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

  public async sendMessage(input: SendMessageInput): Promise<{ runId: string }> {
    clientDebugLog("send.start", {
      agentId: input.agentId,
      sessionId: input.sessionId,
      textPreview: input.text.slice(0, 240),
      mode: this.shouldUseMock() ? "mock" : this.isRemoteTarget() ? "remote" : "local",
    });
    if (this.shouldUseMock()) {
      return mockSendMessage(input);
    }
    this.sessionCache.invalidate(input.agentId, input.sessionId);
    if (await this.ensureClientApiAvailable()) {
      try {
        return await this.sendMessageViaClientApi(input);
      } catch (error) {
        this.rememberClientApiError(error);
        clientDebugLog("send.client-api.failed", {
          agentId: input.agentId,
          sessionId: input.sessionId,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
    if (!this.canUseLegacyLocalFallback()) {
      throw this.clientApiUnavailableError("Sending a message");
    }
    clientDebugLog("send.bridge.fallback", {
      agentId: input.agentId,
      sessionId: input.sessionId,
    });
    return this.sendMessageViaBridge(input);
  }

  private async sendMessageViaClientApi(input: SendMessageInput): Promise<{ runId: string }> {
    const payload = await this.fetchClientApiJson(`/api/v1/agents/${input.agentId}/sessions/${input.sessionId}/runs`, {
      method: "POST",
      body: JSON.stringify({ text: input.text }),
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
    let stepParts: StepPart[] = [];
    let terminal = false;
    const syncAssistant = (status: ChatMessage["status"]): void => {
      if (!assistantMessage) {
        return;
      }
      assistantMessage.status = status;
      assistantMessage.parts = mergeAssistantParts(stepParts, finalText);
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
        stepParts = message.parts.filter((part): part is StepPart => part.type === "step_ref");
        this.emit({ type: "message.created", runId, sessionId: message.sessionId, message });
      } else if (eventName === "step.updated" && assistantMessage) {
        const stepPart = normalizeClientApiPart(data.step);
        if (stepPart?.type === "step_ref") {
          stepParts = [...stepParts.filter((part) => part.stepId !== stepPart.stepId), stepPart];
          syncAssistant("streaming");
        }
      } else if (eventName === "message.delta" && assistantMessage) {
        const part = normalizeClientApiPart(data.part);
        if (part?.type === "markdown") {
          finalText = part.text;
          syncAssistant("streaming");
        }
      } else if (eventName === "message.completed" && assistantMessage) {
        const message = normalizeClientApiMessage(data.message);
        finalText = message?.parts.find((part) => part.type === "markdown")?.text ?? finalText;
        stepParts = stepParts.map((part) => (part.status === "running" ? { ...part, status: "completed" } : part));
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
          replaceParts: mergeAssistantParts(
            stepParts.map((part) => (part.status === "running" ? { ...part, status: "failed" } : part)),
            finalText,
          ),
          status: "cancelled",
        });
      } else if (eventName === "run.finished") {
        terminal = true;
        session.updatedAt = now();
        session.lastMessagePreview = finalText || input.text;
        this.emit({ type: "session.updated", runId, session });
        this.emit({ type: "run.finished", runId, sessionId: input.sessionId });
        clientDebugLog("send.client-api.finished", {
          runId,
          status: "completed",
          finalTextLength: finalText.length,
        });
      } else if (eventName === "run.cancelled") {
        terminal = true;
        this.emit({ type: "run.finished", runId, sessionId: input.sessionId });
      }
    };
    const streamController = new AbortController();
    this.activeRunStreams.set(runId, streamController);
    void this.runStream
      .consume(runId, ({ event, data }) => handleClientApiEvent(event, data), { signal: streamController.signal })
      .then(() => clientDebugLog("send.client-api.stream-closed", { runId }))
      .catch((error: unknown) => {
        this.rememberClientApiError(error);
        clientDebugLog("send.client-api.stream-error", {
          runId,
          error: error instanceof Error ? error.message : String(error),
        });
        if (terminal) {
          return;
        }
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
            role: "assistant",
            status: "failed",
            createdAt: now(),
            parts: [errorPart],
          };
          this.emit({ type: "message.created", runId, sessionId: input.sessionId, message: failedMessage });
        }
        this.emit({ type: "run.finished", runId, sessionId: input.sessionId });
      })
      .finally(() => {
        if (this.activeRunStreams.get(runId) === streamController) {
          this.activeRunStreams.delete(runId);
        }
      });

    return { runId };
  }

  public async cancelRun(runId: string): Promise<{ runId: string; status: "cancelled" }> {
    if (this.shouldUseMock()) {
      return mockCancelRun(runId);
    }
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

  public async listSlashCommands(): Promise<{ commands: ProjectedSlashCommand[] }> {
    if (this.shouldUseMock()) {
      return { commands: [] };
    }
    await this.ensureClientApiAvailable();
    return { commands: await this.commands.list() };
  }

  public async invokeSlashCommand(input: SlashCommandRequest): Promise<SlashCommandResult> {
    if (this.shouldUseMock()) {
      throw new Error("Slash commands require a running OpenPPX Node.");
    }
    await this.ensureClientApiAvailable();
    const envelope = await this.commands.invoke(input.rawCommand, {
      userId: "ppx-client-user",
      agentId: input.agentId,
      sessionId: input.sessionId,
      runId: input.runId,
    });
    if (input.agentId) {
      this.sessionCache.invalidate(input.agentId, input.sessionId ?? undefined);
    }
    return envelope.result;
  }

  private async sendMessageViaBridge(input: SendMessageInput): Promise<{ runId: string }> {
    const runId = `run-${crypto.randomUUID()}`;
    clientDebugLog("send.bridge.start", {
      runId,
      agentId: input.agentId,
      sessionId: input.sessionId,
    });
    const assistantMessage: ChatMessage = {
      id: `assistant-${crypto.randomUUID()}`,
      sessionId: input.sessionId,
      role: "assistant",
      status: "streaming",
      createdAt: now(),
      parts: [
        {
          type: "step_ref",
          stepId: `step-${crypto.randomUUID()}`,
          title: "Connecting to openppx",
          status: "running",
          detail: "Launching the local Python bridge for this agent session.",
        },
      ],
    };
    this.emit({
      type: "message.created",
      runId,
      sessionId: input.sessionId,
      message: assistantMessage,
    });

    const sessions = await this.listSessionsForAgent(input.agentId);
    const session = sessions.find((item) => item.id === input.sessionId) ?? {
      id: input.sessionId,
      agentId: input.agentId,
      title: "Local session",
      updatedAt: now(),
      lastMessagePreview: input.text,
    };

    let finalText = "";
    let stepParts: StepPart[] = assistantMessage.parts.filter((part): part is StepPart => part.type === "step_ref");
    let hasStructuredEvent = false;
    const applyAssistantParts = (parts: MessagePart[], status: ChatMessage["status"]): void => {
      assistantMessage.status = status;
      assistantMessage.parts = parts;
      this.emit({
        type: "message.updated",
        runId,
        sessionId: assistantMessage.sessionId,
        messageId: assistantMessage.id,
        replaceParts: parts,
        status,
      });
    };
    const syncAssistant = (status: ChatMessage["status"]): void => {
      applyAssistantParts(mergeAssistantParts(stepParts, finalText), status);
    };
    const handleBridgeEvent = (payload: LegacyBridgeStreamEvent): void => {
      clientDebugLog("send.bridge.payload", { runId, type: payload.type });
      if (payload.type === "raw") {
        finalText = typeof payload.text === "string" ? payload.text : "";
        applyAssistantParts([{ type: "markdown", text: finalText }], "streaming");
        return;
      }
      const text = typeof payload.text === "string" ? payload.text : undefined;
      const message = typeof payload.message === "string" ? payload.message : undefined;
      const bridgeEvent =
        payload.event && typeof payload.event === "object" && !Array.isArray(payload.event)
          ? (payload.event as Record<string, unknown>)
          : null;
      if (payload.type === "event" && bridgeEvent) {
        hasStructuredEvent = true;
        stepParts = projectBridgeEventToStepParts(bridgeEvent, stepParts).filter(
          (part) => !part.title.startsWith("Connecting to openppx"),
        );
        syncAssistant("streaming");
      } else if (payload.type === "delta") {
        finalText = text ?? finalText;
        if (hasStructuredEvent) {
          syncAssistant("streaming");
        } else {
          applyAssistantParts([{ type: "markdown", text: finalText }], "streaming");
        }
      } else if (payload.type === "final") {
        finalText = text ?? finalText;
        if (hasStructuredEvent) {
          stepParts = stepParts.map((part) =>
            part.status === "running"
              ? { ...part, status: "completed", detail: `${part.detail}\n\nFinished without an explicit tool response event.` }
              : part,
          );
          syncAssistant("completed");
        } else {
          applyAssistantParts([{ type: "markdown", text: finalText }], "completed");
        }
      } else if (payload.type === "error") {
        if (hasStructuredEvent && stepParts.length) {
          stepParts = stepParts.map((part) =>
            part.status === "running" ? { ...part, status: "failed", detail: message ?? part.detail } : part,
          );
          syncAssistant("failed");
        } else {
          applyAssistantParts(
            [{ type: "error", text: message ?? "Unknown bridge error", errorCode: "OPENPPX_BRIDGE_ERROR" }],
            "failed",
          );
        }
      }
    };
    const result = await this.legacyBridge.run(input, handleBridgeEvent);
    clientDebugLog("send.bridge.close", {
      runId,
      code: result.code,
      assistantStatus: assistantMessage.status,
    });
    if (result.code !== 0 && assistantMessage.status !== "failed") {
      applyAssistantParts(
        [
          {
            type: "error",
            text: result.stderr || `Bridge exited with code ${result.code}`,
            errorCode: "OPENPPX_BRIDGE_EXIT",
          },
        ],
        "failed",
      );
    }
    session.updatedAt = now();
    session.lastMessagePreview = finalText || input.text;
    this.emit({ type: "session.updated", runId, session });
    this.emit({ type: "run.finished", runId, sessionId: input.sessionId });

    return { runId };
  }

  public onRunEvent(listener: (event: RunEvent) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  public dispose(): void {
    this.mockUnsubscribe();
    this.abortActiveRunStreams();
    this.sessionCache.clear();
    this.stopManagedClientApiImmediately();
  }

  private abortActiveRunStreams(): void {
    this.activeRunStreams.forEach((controller) => controller.abort());
    this.activeRunStreams.clear();
  }

  private async listSessionsForAgent(agentId: string): Promise<SessionSummary[]> {
    const response = await this.legacyBridge.request<{ sessions?: Array<Record<string, unknown>> }>(
      "list_sessions",
      agentId,
    );
    const sessions = Array.isArray(response?.sessions) ? response.sessions : [];
    return sessions
      .map((payload) => this.formatSessionSummary(agentId, payload))
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  }
}
