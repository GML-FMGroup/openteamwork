import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
import { App } from "../app/src/App";
import type {
  BootstrapPayload,
  AgentResourceSummary,
  ClientDiagnostics,
  ExtensionSummary,
  GoalDetail,
  PpxClientApi,
  ProjectedSlashCommand,
  RunEvent,
  RuntimeStatus,
  SessionSummary,
} from "../app/src/types";

function buildBootstrapPayload(): BootstrapPayload {
  const runtime: RuntimeStatus = {
    target: { id: "local-default", type: "local", name: "This Mac" },
    state: "healthy",
    summary: "ready",
    detail: "detail",
  };
  const sessions: SessionSummary[] = [
    {
      id: "session-a",
      agentId: "agent-1",
      title: "Session A",
      updatedAt: "2026-04-02T10:00:00.000Z",
      lastMessagePreview: "Preview A should stay hidden",
    },
    {
      id: "session-b",
      agentId: "agent-1",
      title: "Session B",
      updatedAt: "2026-04-02T09:00:00.000Z",
      lastMessagePreview: "Preview B should stay hidden",
    },
  ];
  return {
    runtime,
    agents: [
      {
        id: "agent-1",
        name: "Agent 1",
        description: "Local test agent",
        enabled: true,
        status: "healthy",
        tags: ["local"],
      },
    ],
    sessions,
    messages: [
      {
        id: "message-a",
        sessionId: "session-a",
        role: "assistant",
        status: "completed",
        createdAt: "2026-04-02T10:00:01.000Z",
        parts: [{ type: "markdown", text: "Loaded Session A" }],
      },
    ],
    selectedAgentId: "agent-1",
    selectedSessionId: "session-a",
  };
}

function buildDiagnostics(): ClientDiagnostics {
  return {
    desktopVersion: "0.6.0",
    mode: "local",
    target: { id: "local-default", type: "local", name: "This Mac" },
    openppxRoot: "/tmp/openppx_root",
    openppxRootExists: true,
    pythonBin: "/tmp/openppx_root/.venv/bin/python",
    clientApiBaseUrl: "http://127.0.0.1:8765",
    clientApiManagedByClient: true,
    clientApiHealthy: true,
    clientApiProductVersion: "0.4",
    clientApiProtocolVersion: 1,
    clientApiCompatibility: "compatible",
    clientApiAuthState: "authenticated",
    clientApiCredentialConfigured: true,
    nodeId: "node_test",
    nodeName: "This Mac",
    clientApiProcessRunning: true,
    agentCount: 1,
    sessionCacheEntries: 1,
    messageCacheEntries: 1,
    debugEnabled: false,
  };
}

function viewportRect(top: number, height = 200): DOMRect {
  return {
    x: 0,
    y: top,
    top,
    right: 800,
    bottom: top + height,
    left: 0,
    width: 800,
    height,
    toJSON: () => ({}),
  };
}

function buildSlashCommands(): ProjectedSlashCommand[] {
  return [
    {
      command: "/help",
      title: "Show commands",
      description: "List commands available to this client.",
      icon: "circle-help",
      argHint: "",
      lifecycle: "side_channel" as const,
      acceptsArgs: false,
      arguments: [],
      noArgsBehavior: "invoke" as const,
      usage: "/help",
      order: 10,
      actionId: "system.help",
      available: true,
      availabilityReason: null,
    },
    {
      command: "/status",
      title: "Show status",
      description: "Display Node and Agent readiness.",
      icon: "activity",
      argHint: "",
      lifecycle: "side_channel" as const,
      acceptsArgs: false,
      arguments: [],
      noArgsBehavior: "invoke" as const,
      usage: "/status",
      order: 40,
      actionId: "system.status",
      available: true,
      availabilityReason: null,
    },
  ];
}

function installLocalStorage(): { storage: Storage; restore: () => void } {
  const values = new Map<string, string>();
  const previousDescriptor = Object.getOwnPropertyDescriptor(window, "localStorage");
  const storage: Storage = {
    get length() {
      return values.size;
    },
    clear: () => {
      values.clear();
    },
    getItem: (key) => values.get(key) ?? null,
    key: (index) => Array.from(values.keys())[index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, String(value));
    },
  };
  Object.defineProperty(window, "localStorage", { configurable: true, value: storage });
  return {
    storage,
    restore: () => {
      if (previousDescriptor) {
        Object.defineProperty(window, "localStorage", previousDescriptor);
      } else {
        Reflect.deleteProperty(window, "localStorage");
      }
    },
  };
}

function installClient(overrides: Partial<PpxClientApi> = {}): { client: PpxClientApi; emit: (event: RunEvent) => void } {
  let listener: ((event: RunEvent) => void) | null = null;
  const client: PpxClientApi = {
    platform: "macos",
    bootstrap: async () => buildBootstrapPayload(),
    getUserProfile: async () => ({
      id: "ppx-client-user",
      displayName: "Wenhao Jiang",
      accountKind: "local",
    }),
    login: async () => ({ id: "user-test", displayName: "user@example.com", accountKind: "product", privilegeLevel: "high" }),
    logout: async () => undefined,
    getDiagnostics: async () => buildDiagnostics(),
    setDesktopHostPreferences: async () => undefined,
    testConnectionSettings: async () => buildDiagnostics(),
    saveConnectionSettings: async () => buildDiagnostics(),
    listConnectionProfiles: async () => ({ profiles: [] }),
    activateConnectionProfile: async () => buildDiagnostics(),
    removeConnectionProfile: async () => ({ removed: true }),
    runRuntimeCommand: async () => buildBootstrapPayload().runtime,
    createAgent: async () => ({
      agent: {
        id: "research",
        name: "Research",
        description: "Workspace: /node/workspaces/research",
        enabled: true,
        status: "healthy",
        workspace: "/node/workspaces/research",
        avatar: null,
        tags: ["local", "openppx"],
        revision: "sha256:agent",
      },
      nodeRevision: "sha256:node",
      effect: "next_run",
    }),
    listManagedAgents: async () => ({ agents: [{
      id: "agent-1", name: "Agent 1", description: "Local test agent", enabled: true, status: "healthy",
      workspace: "/workspace", instruction: "", privilegeLevel: "medium", modelProfileId: "primary",
      avatar: null, tags: ["local"], revision: "sha256:agent", nodeRevision: "sha256:node", effect: "none",
    }] }),
    updateAgent: async (input) => ({
      id: input.agentId, name: input.displayName, description: `Workspace: ${input.workspace}`, enabled: true,
      status: "healthy", workspace: input.workspace, instruction: input.instruction, privilegeLevel: input.privilegeLevel,
      modelProfileId: input.modelProfileId, avatar: null, tags: ["local"], revision: "sha256:agent-next",
      nodeRevision: "sha256:node", effect: "next_run",
    }),
    setAgentEnabled: async (agentId, enabled) => ({
      id: agentId, name: "Agent 1", description: "Local test agent", enabled, status: enabled ? "healthy" : "disabled",
      workspace: "/workspace", instruction: "", privilegeLevel: "medium", modelProfileId: "primary", avatar: null,
      tags: ["local"], revision: "sha256:agent", nodeRevision: "sha256:node-next", effect: "next_run",
    }),
    removeAgent: async (agentId) => ({ agentId, workspaceRetained: true }),
    getSetupStatus: async () => ({
      state: "ready",
      steps: { node: "complete", agent: "complete", model: "complete", credential: "available", hello: "verified" },
      revisions: { node: "node-revision", agent: "agent-revision", profile: "profile-revision" },
      recommendedWorkspace: "/workspace",
      diagnostic: null,
      current: { node: null, agent: null, profile: null },
      providers: [],
    }),
    getSetupReadiness: async () => ({
      state: "ready",
      workspaceReady: true,
      steps: { node: "complete", agent: "complete", model: "complete", credential: "available" },
    }),
    applySetup: async () => ({
      state: "configured",
      revisions: { node: "node-revision", agent: "agent-revision", profile: "profile-revision" },
      secretState: "available",
      restartRequired: false,
    }),
    runSetupHello: async () => ({ sessionId: "session-fixture", reply: "Hello", state: "ready" }),
    getProviderModels: async (providerId) => ({
      providerId,
      source: "provider_default",
      authoritative: false,
      defaultModel: "gemini-2.5-flash",
      items: [
        {
          id: "gemini-2.5-flash",
          displayName: "gemini-2.5-flash",
          description: "Default model",
          defaultReasoningEffort: null,
          reasoningEfforts: [],
        },
      ],
    }),
    getProviderAuthStatus: async (providerId) => ({
      providerId,
      state: "authenticated",
      source: "codex_cli",
      expiresAt: null,
      loginMode: "device_code",
      session: null,
    }),
    beginProviderAuth: async (providerId) => ({
      providerId,
      state: "pending",
      source: null,
      expiresAt: null,
      loginMode: "device_code",
      session: {
        id: "login-fixture",
        state: "pending",
        verificationUrl: "https://auth.openai.com/codex/device",
        userCode: "ABCD-EFGH",
        expiresAt: null,
        error: null,
      },
    }),
    refreshProviderAuth: async (providerId) => ({
      providerId,
      state: "authenticated",
      source: "codex_cli",
      expiresAt: null,
      loginMode: "device_code",
      session: null,
    }),
    openExternalUrl: async () => undefined,
    listModelProfiles: async () => ({ profiles: [] }),
    readModelProfile: async (profileId) => ({
      resourceId: `model-profiles/${profileId}`,
      revision: "sha256:profile",
      document: {
        apiVersion: "openppx.io/v1alpha1",
        kind: "ModelProfile",
        metadata: { name: profileId },
        spec: {
          displayName: "Primary",
          provider: "google",
          model: "gemini-2.5-flash",
          credential: { store: "system", name: "fixture-secret" },
          executionLocation: "remote",
          apiBase: null,
          capabilities: ["text", "tool_calling"],
          contextWindowTokens: null,
          inputCostPerMillionUsd: null,
          outputCostPerMillionUsd: null,
          fallbackProfiles: [],
          enabled: true,
        },
      },
    }),
    createModelProfile: async (input) => ({
      resourceId: "model-profiles/model-created",
      revision: "sha256:saved-profile",
      document: {
        apiVersion: "openppx.io/v1alpha1",
        kind: "ModelProfile",
        metadata: { name: "model-created" },
        spec: {
          displayName: input.displayName,
          provider: input.providerId,
          model: input.model,
          credential: null,
          executionLocation: input.executionLocation,
          apiBase: input.apiBase,
          capabilities: input.capabilities,
          contextWindowTokens: input.contextWindowTokens,
          inputCostPerMillionUsd: input.inputCostPerMillionUsd,
          outputCostPerMillionUsd: input.outputCostPerMillionUsd,
          fallbackProfiles: input.fallbackProfileIds,
          enabled: input.enabled,
        },
      },
      credentialState: "not_required",
      effect: "next_run",
    }),
    updateModelProfile: async (input) => ({
      resourceId: `model-profiles/${input.profileId}`,
      revision: "sha256:updated-profile",
      document: {
        apiVersion: "openppx.io/v1alpha1",
        kind: "ModelProfile",
        metadata: { name: input.profileId },
        spec: {
          displayName: input.displayName,
          provider: input.providerId,
          model: input.model,
          credential: null,
          executionLocation: input.executionLocation,
          apiBase: input.apiBase,
          capabilities: input.capabilities,
          contextWindowTokens: input.contextWindowTokens,
          inputCostPerMillionUsd: input.inputCostPerMillionUsd,
          outputCostPerMillionUsd: input.outputCostPerMillionUsd,
          fallbackProfiles: input.fallbackProfileIds,
          enabled: input.enabled,
        },
      },
      credentialState: "not_required",
      effect: "next_run",
    }),
    getOperationsOverview: async () => ({
      state: "healthy",
      components: [
        { component: "runtime", state: "healthy", code: "runtime_ready", reason: "Runtime Supervisor is ready.", remediation: null },
      ],
      tasks: { total: 0, byStatus: {} },
      automation: { cronJobs: 0, heartbeatEnabled: false },
    }),
    listOperationsAudit: async () => ({ items: [] }),
    getOperationsDashboard: async () => ({
      overview: {
        state: "healthy",
        components: [],
        tasks: { total: 0, byStatus: {} },
        automation: { cronJobs: 0, heartbeatEnabled: false },
      },
      tasks: { ok: true, items: [] },
      cron: { status: {}, items: [], history: [] },
      heartbeat: { running: true, enabled: false, intervalMs: null, wakePending: false, lastRunAtMs: null, lastStatus: null, lastReason: null, lastDurationMs: null, configuration: { enabled: false, everySeconds: 1800, prompt: "Review tasks", activeHours: { start: null, end: null, timezone: "user" } } },
      usage: { requests: 0, requestTokens: 0, responseTokens: 0, totalTokens: 0, recent: [] },
      audit: [],
    }),
    getOperationsTask: async () => ({ ok: true, items: [], task: {}, events: [], checkpoints: [], deliveries: [] }),
    getOperationsTaskOutput: async () => ({}),
    controlOperationsTask: async () => ({}),
    createOperationsCron: async () => ({}),
    updateOperationsCron: async () => ({}),
    setOperationsCronEnabled: async () => ({}),
    runOperationsCron: async () => ({}),
    removeOperationsCron: async () => ({}),
    runOperationsHeartbeat: async () => ({}),
    configureOperationsHeartbeat: async () => ({}),
    listSessions: async () => ({ sessions: buildBootstrapPayload().sessions }),
    createSession: async () => ({ session: buildBootstrapPayload().sessions[0] }),
    renameSession: async ({ sessionId, title }) => ({ sessionId, title }),
    archiveSession: async ({ sessionId, archived }) => ({ sessionId, archived }),
    forkSession: async () => ({ session: buildBootstrapPayload().sessions[0] }),
    exportSession: async ({ sessionId }) => ({ sessionId, items: [] }),
    deleteSession: async ({ sessionId }) => ({ sessionId, deleted: true }),
    loadSession: async () => ({ messages: [] }),
    getCurrentGoal: async () => ({ goal: null }),
    updateGoal: async () => {
      throw new Error("No fixture Goal configured.");
    },
    transitionGoal: async () => {
      throw new Error("No fixture Goal configured.");
    },
    retryGoalStep: async () => {
      throw new Error("No fixture Goal configured.");
    },
    listAutomations: async () => ({ automations: [] }),
    getAutomation: async () => {
      throw new Error("No fixture Automation configured.");
    },
    createAutomation: async (input) => ({
      automationId: "auto_fixture",
      name: input.name,
      description: input.description ?? "",
      instructions: input.instructions,
      outputRequirements: input.outputRequirements ?? [],
      status: "active",
      agentId: input.agentId,
      userId: input.userId,
      revision: 1,
      trigger: null,
      latestRun: null,
      workspaceRef: "",
      contextMode: "isolated",
      modelProfileRef: "",
      extensionPolicy: {},
      permissionPolicy: {},
      deliveryPolicy: {},
      concurrencyPolicy: {},
      missedRunPolicy: {},
      retryPolicy: {},
      budgetPolicy: {},
      monitorPolicy: {},
      readiness: { ready: true, reasons: [] },
      createdAtMs: Date.now(),
      updatedAtMs: Date.now(),
    }),
    updateAutomation: async () => {
      throw new Error("No fixture Automation configured.");
    },
    transitionAutomation: async () => ({}),
    runAutomation: async () => {
      throw new Error("No fixture Automation configured.");
    },
    getAutomationHistory: async () => ({ runs: [], events: [] }),
    listAutomationTemplates: async () => ({ templates: [{
      templateId: "morning-brief",
      name: "Morning brief",
      description: "Summarize the day before it starts.",
      instructions: "Summarize today's calendar and important unread email.",
      outputRequirements: ["Concise daily brief"],
      recommendedSchedule: { kind: "cron", cronExpr: "0 8 * * 1-5", timezone: "" },
      requiredExtensions: ["Calendar", "Email"],
      deliveryHint: "session",
      behavior: "task",
      provenance: "openppx",
      version: 1,
    }] }),
    uploadArtifact: async (input) => ({
      id: "artifact-test",
      key: `uploads/artifact-test/${input.fileName}`,
      fileName: input.fileName,
      mimeType: input.mimeType,
      sizeBytes: 1,
      version: 0,
      source: "user_upload",
      createdAt: "2026-08-04T00:00:00Z",
    }),
    listArtifacts: async () => ({ artifacts: [] }),
    downloadArtifact: async (_agentId, _sessionId, artifact) => ({ dataBase64: "", mimeType: artifact.mimeType }),
    sendMessage: async () => new Promise<{ runId: string }>(() => undefined),
    cancelRun: async (runId) => ({ runId, status: "cancelled" }),
    listSlashCommands: async () => ({ commands: [] }),
    invokeSlashCommand: async () => {
      throw new Error("No fixture slash command configured.");
    },
    listExtensions: async () => ({ extensions: [] }),
    listExtensionStarters: async () => ({ starters: [], counts: { plugin: 0, app: 0, mcp: 0, skill: 0 } }),
    installAppStarter: async () => ({ id: "fixture-app", revision: "sha256:app", status: "installed" }),
    getExtension: async () => {
      throw new Error("No fixture Extension configured.");
    },
    getExtensionReadiness: async (kind, extensionId) => ({ kind, id: extensionId, ready: true, issues: [], status: "installed", revision: "sha256:fixture" }),
    getExtensionHealthHistory: async () => ({
      summary: { latest: null, lastSuccessAtMs: null, lastFailureAtMs: null, consecutiveFailures: 0 },
      items: [],
    }),
    previewExtension: async () => {
      throw new Error("No fixture Extension preview configured.");
    },
    installExtension: async () => ({ revision: "sha256:installed", status: "installed" }),
    setExtensionAgentEnabled: async () => ({ revision: "sha256:fixture", status: "enabled" }),
    removeExtension: async () => ({ removed: true }),
    getPluginHookStatus: async (pluginId, expectedRevision) => ({
      pluginId,
      pluginRevision: expectedRevision,
      pluginDigest: "sha256:plugin",
      hookDigest: "sha256:hooks",
      trusted: false,
      declaredEvents: [],
      supportedEvents: [],
      handlerCount: 0,
      executableCount: 0,
      unsupportedHandlers: 0,
      handlers: [],
    }),
    setPluginHookTrust: async (pluginId, expectedRevision, trusted) => ({
      pluginId,
      pluginRevision: expectedRevision,
      pluginDigest: "sha256:plugin",
      hookDigest: "sha256:hooks",
      trusted,
      declaredEvents: [],
      supportedEvents: [],
      handlerCount: 0,
      executableCount: 0,
      unsupportedHandlers: 0,
      handlers: [],
    }),
    listPluginMarketplaces: async () => ({ marketplaces: [] }),
    listPluginMarketplaceEntries: async () => ({ entries: [] }),
    savePluginMarketplace: async (input) => ({
      id: input.marketplaceId,
      revision: "sha256:marketplace",
      resolvedRevision: null,
      catalogDigest: null,
      entryCount: 0,
      refreshedAt: null,
      ready: false,
      ...input.spec,
    }),
    refreshPluginMarketplace: async (marketplaceId) => ({
      id: marketplaceId,
      displayName: "Fixture marketplace",
      type: "local",
      locator: "/fixture",
      ref: "HEAD",
      revision: "sha256:marketplace",
      resolvedRevision: "local",
      catalogDigest: "sha256:catalog",
      entryCount: 0,
      refreshedAt: "2026-08-05T00:00:00Z",
      ready: true,
    }),
    removePluginMarketplace: async () => undefined,
    createMcpServer: async () => ({ revision: "sha256:mcp-created", status: "installed" }),
    updateMcpServer: async () => ({ revision: "sha256:mcp-updated", status: "installed" }),
    beginMcpOAuth: async (serverId) => ({ serverId, status: "authorizing", authorizeUrl: "https://example.com/authorize", error: "" }),
    getMcpOAuthStatus: async (serverId) => ({ serverId, status: "needs_auth", authorizeUrl: "", error: "" }),
    signOutMcpOAuth: async (serverId) => ({ serverId, status: "needs_auth", authorizeUrl: "", error: "" }),
    testMcpServer: async (serverId) => ({ kind: "mcp", id: serverId, revision: "sha256:mcp", checkedAt: "2026-08-04T12:00:00Z", ready: true, status: "ok", transport: "stdio", elapsedMs: 1, attempts: 1, toolCount: 0, toolNames: [], issues: [], errorKind: null, message: "" }),
    saveAppConnection: async () => ({ revision: "sha256:connection", status: "ready" }),
    testAppConnection: async (connectionId) => ({ kind: "app_connection", id: connectionId, revision: "sha256:connection", checkedAt: "2026-08-04T12:00:00Z", ready: true, status: "ok", transport: "stdio", elapsedMs: 1, attempts: 1, toolCount: 0, toolNames: [], issues: [], errorKind: null, message: "" }),
    setAppConnectionAgentEnabled: async () => ({ revision: "sha256:connection-enabled", status: "enabled" }),
    removeAppConnection: async () => ({ removed: true }),
    onRunEvent: (next) => {
      listener = next;
      return () => {
        if (listener === next) {
          listener = null;
        }
      };
    },
    ...overrides,
  };
  window.ppxClient = client;
  return {
    client,
    emit: (event) => listener?.(event),
  };
}

async function openSettings(): Promise<void> {
  fireEvent.click(await screen.findByRole("button", { name: "User profile" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Settings" }));
}

describe("Model Profile settings", () => {
  it("shows provider-aware access states instead of raw credential storage states", async () => {
    const getProviderAuthStatus = vi.fn()
      .mockResolvedValueOnce({
        providerId: "openai_codex",
        state: "authenticated",
        source: "codex_cli",
        expiresAt: null,
        loginMode: "device_code",
        session: null,
      })
      .mockResolvedValue({
        providerId: "openai_codex",
        state: "not_authenticated",
        source: null,
        expiresAt: null,
        loginMode: "device_code",
        session: null,
      });
    installClient({
      getSetupStatus: async () => ({
        state: "ready",
        steps: { node: "complete", agent: "complete", model: "complete", credential: "available", hello: "verified" },
        revisions: { node: "node-revision", agent: "agent-revision", profile: "profile-revision" },
        recommendedWorkspace: "/workspace",
        diagnostic: null,
        current: { node: null, agent: null, profile: null },
        providers: [
          { id: "openai_codex", displayName: "OpenAI Codex", runtime: "codex", credentialMode: "oauth", credentialRequired: false, defaultModel: "openai-codex/gpt-5.5" },
          { id: "google", displayName: "Google Gemini", runtime: "google", credentialMode: "api_key", credentialRequired: true, defaultModel: "gemini-3-flash-preview" },
          { id: "local", displayName: "Local model", runtime: "local", credentialMode: "none", credentialRequired: false, defaultModel: "local/test" },
        ],
      }),
      getProviderAuthStatus,
      listModelProfiles: async () => ({
        profiles: [
          { id: "primary", displayName: "Primary", revision: "sha256:primary", provider: "openai_codex", model: "openai-codex/gpt-5.5", enabled: true, credentialState: "not_required" },
          { id: "gemini", displayName: "Gemini", revision: "sha256:gemini", provider: "google", model: "gemini-3-flash-preview", enabled: true, credentialState: "available" },
          { id: "local", displayName: "Local", revision: "sha256:local", provider: "local", model: "local/test", enabled: false, credentialState: "not_required" },
        ],
      }),
    });

    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    await openSettings();
    fireEvent.click(screen.getByRole("button", { name: "Models" }));

    expect(await screen.findByText("Signed in on Node")).toBeInTheDocument();
    expect(screen.getByText("API key saved")).toBeInTheDocument();
    expect(screen.getByText("No credentials needed")).toBeInTheDocument();
    expect(screen.getByText("disabled")).toBeInTheDocument();
    expect(screen.queryByText("not required")).not.toBeInTheDocument();
    expect(getProviderAuthStatus).toHaveBeenCalledTimes(1);
    expect(getProviderAuthStatus).toHaveBeenCalledWith("openai_codex");

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("Sign-in required")).toBeInTheDocument();
    expect(getProviderAuthStatus).toHaveBeenCalledTimes(2);
  });
});

function configuredSetupStatus() {
  return {
    state: "configured" as const,
    steps: { node: "complete", agent: "complete", model: "complete", credential: "not_required", hello: "stale" },
    revisions: { node: "node-revision", agent: "agent-revision", profile: "profile-revision" },
    recommendedWorkspace: "/workspace/openppx",
    diagnostic: null,
    current: {
      node: {
        metadata: { name: "local-node" },
        spec: { displayName: "This Mac" },
      },
      agent: {
        metadata: { name: "main" },
        spec: {
          displayName: "Monica",
          workspace: "/workspace/openppx",
          ownerPrincipalId: "ppx-client-user",
          privilegeLevel: "medium",
          modelPolicy: { defaultProfile: "primary" },
        },
      },
      profile: {
        metadata: { name: "primary" },
        spec: {
          displayName: "Primary",
          provider: "openai_codex",
          model: "openai-codex/gpt-5.5",
          executionLocation: "remote",
        },
      },
    },
    providers: [{
      id: "openai_codex",
      displayName: "OpenAI Codex",
      runtime: "codex",
      credentialMode: "oauth" as const,
      credentialRequired: false,
      defaultModel: "openai-codex/gpt-5.5",
    }],
  };
}

describe("App sending state", () => {
  it("signs a remote product user in with Node URL, email, and transient secret", async () => {
    const login = vi.fn(async () => ({
      id: "user-jiang",
      displayName: "jiang@example.com",
      accountKind: "product" as const,
      email: "jiang@example.com",
      privilegeLevel: "high" as const,
    }));
    const getSetupReadiness = vi.fn(async () => ({
      state: "configured" as const,
      workspaceReady: true,
      steps: { node: "complete", agent: "complete", model: "complete", credential: "available" },
    }));
    const getSetupStatus = vi.fn(async () => {
      throw new Error("ordinary users must not request rich setup status");
    });
    installClient({
      getUserProfile: async () => { throw new Error("A valid user session token is required."); },
      login,
      getSetupReadiness,
      getSetupStatus,
    });
    render(<App />);

    await screen.findByRole("heading", { name: "Connect to a Node" });
    fireEvent.click(screen.getByRole("button", { name: "Remote Node" }));
    fireEvent.change(screen.getByLabelText("Node URL"), { target: { value: "https://team.example.com" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "jiang@example.com" } });
    fireEvent.change(screen.getByLabelText("Secret"), { target: { value: "private secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in to OpenTeamwork" }));

    await screen.findByRole("button", { name: "Send" });
    expect(login).toHaveBeenCalledWith({
      connection: expect.objectContaining({
        targetType: "lan",
        clientApiBaseUrl: "https://team.example.com",
        accessToken: "",
      }),
      email: "jiang@example.com",
      secret: "private secret",
    });
    expect(screen.queryByDisplayValue("private secret")).not.toBeInTheDocument();
    expect(getSetupReadiness).toHaveBeenCalledTimes(1);
    expect(getSetupStatus).not.toHaveBeenCalled();
  });

  it("refuses a plaintext remote login before sending credentials", async () => {
    const login = vi.fn();
    installClient({
      getUserProfile: async () => { throw new Error("A valid user session token is required."); },
      login,
    });
    render(<App />);

    await screen.findByRole("heading", { name: "Connect to a Node" });
    fireEvent.click(screen.getByRole("button", { name: "Remote Node" }));
    fireEvent.change(screen.getByLabelText("Node URL"), { target: { value: "http://team.example.com:18765" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "jiang@example.com" } });
    fireEvent.change(screen.getByLabelText("Secret"), { target: { value: "private secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in to OpenTeamwork" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("requires an HTTPS Node URL");
    expect(login).not.toHaveBeenCalled();
  });

  it("describes a post-authentication workspace failure without blaming credentials", async () => {
    installClient({
      getUserProfile: async () => { throw new Error("A valid user session token is required."); },
      login: async () => ({
        id: "user-jiang",
        displayName: "jiang@example.com",
        accountKind: "product",
        email: "jiang@example.com",
        privilegeLevel: "high",
      }),
      getSetupReadiness: async () => { throw new Error("Node readiness is temporarily unavailable."); },
      getSetupStatus: async () => { throw new Error("ordinary users must not request rich setup status"); },
    });
    render(<App />);

    await screen.findByRole("heading", { name: "Connect to a Node" });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "jiang@example.com" } });
    fireEvent.change(screen.getByLabelText("Secret"), { target: { value: "private secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in to OpenTeamwork" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Sign-in succeeded, but the Node workspace could not be loaded: Node readiness is temporarily unavailable.",
    );
  });

  it("shows ordinary users administrator guidance when sanitized readiness is incomplete", async () => {
    const getSetupStatus = vi.fn(async () => {
      throw new Error("ordinary users must not request rich setup status");
    });
    installClient({
      getUserProfile: async () => ({
        id: "user-high",
        displayName: "jiang@example.com",
        accountKind: "product",
        email: "jiang@example.com",
        privilegeLevel: "high",
      }),
      getSetupReadiness: async () => ({
        state: "needs_configuration",
        workspaceReady: false,
        steps: { node: "complete", agent: "missing", model: "missing", credential: "not_required" },
      }),
      getSetupStatus,
    });
    render(<App />);

    expect(await screen.findByText("This Node needs administrator setup")).toBeInTheDocument();
    expect(getSetupStatus).not.toHaveBeenCalled();
  });

  it("completes first-run setup only after a real Hello is verified", async () => {
    const needsConfiguration = {
      state: "needs_configuration" as const,
      steps: { node: "missing", agent: "missing", model: "missing", credential: "missing", hello: "pending" },
      revisions: { node: null, agent: null, profile: null },
      recommendedWorkspace: "/workspace/openppx",
      diagnostic: null,
      current: { node: null, agent: null, profile: null },
      providers: [
        {
          id: "google",
          displayName: "Google Gemini",
          runtime: "google_adk",
          credentialMode: "api_key" as const,
          credentialRequired: true,
          defaultModel: "gemini-2.5-flash",
        },
      ],
    };
    const ready = {
      ...needsConfiguration,
      state: "ready" as const,
      steps: { node: "complete", agent: "complete", model: "complete", credential: "available", hello: "verified" },
      revisions: { node: "node-revision", agent: "agent-revision", profile: "profile-revision" },
    };
    const getSetupStatus = vi.fn().mockResolvedValueOnce(needsConfiguration).mockResolvedValue(ready);
    const applySetup = vi.fn(async () => ({
      state: "configured" as const,
      revisions: ready.revisions,
      secretState: "available",
      restartRequired: false,
    }));
    const runSetupHello = vi.fn(async () => ({ sessionId: "session-first", reply: "Hello", state: "ready" as const }));
    installClient({ getSetupStatus, applySetup, runSetupHello });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Set up your first agent." })).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveClass("platform-macos");
    expect(screen.getByText("Not tested")).toBeInTheDocument();
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
    expect(screen.queryByText("Setup required")).not.toBeInTheDocument();
    const nodeStep = screen.getByRole("button", { name: "Node" });
    const agentStep = screen.getByRole("button", { name: "Agent" });
    const helloStep = screen.getByRole("button", { name: "First Hello" });
    expect(nodeStep.closest("li")).toHaveClass("pending", "active");
    expect(nodeStep).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("Connection & identity")).toBeInTheDocument();
    expect(screen.getByText("Workspace & model")).toBeInTheDocument();
    expect(screen.getByText("Real model check")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Node" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Agent" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "First Hello" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Model" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Connection successful: This Mac");

    const shell = screen.getByRole("main");
    const nodeSection = screen.getByRole("heading", { name: "Node" }).closest("section") as HTMLElement;
    const agentSection = screen.getByRole("heading", { name: "Agent" }).closest("section") as HTMLElement;
    const helloSection = screen.getByRole("heading", { name: "First Hello" }).closest("section") as HTMLElement;
    vi.spyOn(shell, "getBoundingClientRect").mockReturnValue(viewportRect(0, 1_000));
    vi.spyOn(nodeSection, "getBoundingClientRect").mockReturnValue(viewportRect(-420));
    vi.spyOn(agentSection, "getBoundingClientRect").mockReturnValue(viewportRect(80, 500));
    vi.spyOn(helloSection, "getBoundingClientRect").mockReturnValue(viewportRect(900));
    shell.scrollTop = 420;
    fireEvent.scroll(shell);
    expect(agentStep).toHaveAttribute("aria-current", "step");
    expect(nodeStep).not.toHaveAttribute("aria-current");

    const scrollIntoView = vi.fn();
    Object.defineProperty(helloSection, "scrollIntoView", { configurable: true, value: scrollIntoView });
    const originalMatchMedia = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({ matches: true })),
    });
    fireEvent.click(helloStep);
    expect(helloStep).toHaveAttribute("aria-current", "step");
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "start" });
    Object.defineProperty(window, "matchMedia", { configurable: true, value: originalMatchMedia });

    expect(screen.getByLabelText("Workspace folder")).toHaveValue("/workspace/openppx");
    expect(screen.getByLabelText("Model")).toHaveValue("gemini-2.5-flash");
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "test-api-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Set up & say Hello" }));

    await waitFor(() => expect(applySetup).toHaveBeenCalledTimes(1));
    expect(applySetup).toHaveBeenCalledWith(expect.objectContaining({
      secret: { ref: { store: "system", name: "primary-model-api-key" }, value: "test-api-key" },
    }));
    expect(runSetupHello).toHaveBeenCalledWith("main", "ppx-client-user", "Hello OpenTeamwork");
    expect(await screen.findByRole("button", { name: "Send" })).toBeInTheDocument();
  });

  it("adds the first Agent without replacing the initialized Node listener", async () => {
    const nodeOnly = {
      state: "needs_configuration" as const,
      steps: { node: "complete", agent: "missing", model: "missing", credential: "not_required", hello: "not_started" },
      revisions: { node: "node-revision", agent: null, profile: null },
      recommendedWorkspace: "/node/users/root/agents/main/workspace",
      diagnostic: null,
      current: {
        node: {
          apiVersion: "openppx.io/v1alpha1",
          kind: "NodeConfig",
          metadata: { name: "team-node" },
          spec: {
            displayName: "Team Node",
            enabledAgents: [],
            clientApi: { listenHost: "127.0.0.1", port: 18765, authentication: "required" },
          },
        },
        agent: null,
        profile: null,
      },
      providers: [{
        id: "google",
        displayName: "Google Gemini",
        runtime: "google_adk",
        credentialMode: "api_key" as const,
        credentialRequired: true,
        defaultModel: "gemini-2.5-flash",
      }],
    };
    const ready = {
      ...nodeOnly,
      state: "ready" as const,
      steps: { node: "complete", agent: "complete", model: "complete", credential: "available", hello: "verified" },
      revisions: { node: "node-next", agent: "agent-revision", profile: "profile-revision" },
    };
    const applySetup = vi.fn(async () => ({
      state: "configured" as const,
      revisions: ready.revisions,
      secretState: "available",
      restartRequired: false,
    }));
    installClient({
      getUserProfile: async () => ({
        id: "user-root",
        displayName: "admin@example.com",
        accountKind: "product",
        email: "admin@example.com",
        privilegeLevel: "root",
      }),
      getDiagnostics: async () => ({
        ...buildDiagnostics(),
        mode: "lan",
        target: { id: "lan-team-node", type: "remote", name: "Team Node" },
        clientApiBaseUrl: "https://node.example.com",
        clientApiManagedByClient: false,
      }),
      getSetupStatus: vi.fn().mockResolvedValueOnce(nodeOnly).mockResolvedValue(ready),
      applySetup,
      runSetupHello: async () => ({ sessionId: "session-first", reply: "Hello", state: "ready" }),
    });

    render(<App />);

    await screen.findByRole("heading", { name: "Set up your first agent." });
    expect(screen.getByRole("button", { name: "Agent" })).toHaveAttribute("aria-current", "step");
    expect(screen.getByText("Your Node is ready. Configure its first Agent and model, then verify one real conversation.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "test-api-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Set up & say Hello" }));

    await waitFor(() => expect(applySetup).toHaveBeenCalledTimes(1));
    expect(applySetup).toHaveBeenCalledWith(expect.objectContaining({
      node: expect.objectContaining({
        metadata: { name: "team-node" },
        spec: expect.objectContaining({
          enabledAgents: ["main"],
          clientApi: { listenHost: "127.0.0.1", port: 18765, authentication: "required" },
        }),
      }),
    }));
  });

  it("updates the candidate connection state and explains an unreachable local Node", async () => {
    const needsConfiguration = {
      ...configuredSetupStatus(),
      state: "needs_configuration" as const,
      steps: { node: "missing", agent: "missing", model: "missing", credential: "missing", hello: "pending" },
    };
    let rejectTest!: (reason: unknown) => void;
    const testConnectionSettings = vi.fn(() => new Promise<ClientDiagnostics>((_resolve, reject) => {
      rejectTest = reject;
    }));
    installClient({
      getSetupStatus: async () => needsConfiguration,
      testConnectionSettings,
    });

    render(<App />);

    await screen.findByRole("heading", { name: "Set up your first agent." });
    expect(screen.getByText("Not tested")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Node URL"), { target: { value: "http://127.0.0.1:18764" } });
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(screen.getByText("Testing…", { selector: ".onboarding-state-badge" })).toBeInTheDocument();

    await act(async () => {
      rejectTest(new Error(
        "Error invoking remote method 'ppx-client:test-connection-settings': Error: Testing the local connection requires the OpenPPX Client API. fetch failed",
      ));
    });

    expect(await screen.findByText("Connection failed")).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "Couldn’t reach an OpenTeamwork Node at http://127.0.0.1:18764. Check the URL and make sure the Node is running, then try again.",
    );
    expect(alert).not.toHaveTextContent(/Error invoking remote method|test-connection-settings|fetch failed/);

    fireEvent.change(screen.getByLabelText("Node URL"), { target: { value: "http://127.0.0.1:18765" } });
    await waitFor(() => expect(screen.getByText("Not tested")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("opens the workspace when core configuration is complete but Hello is stale", async () => {
    const configured = configuredSetupStatus();
    const getSetupStatus = vi.fn(async () => configured);
    const applySetup = vi.fn();
    const runSetupHello = vi.fn();
    const listSlashCommands = vi.fn(async () => ({ commands: [] }));
    const listModelProfiles = vi.fn(async () => ({ profiles: [] }));
    installClient({ getSetupStatus, applySetup, runSetupHello, listSlashCommands, listModelProfiles });

    render(<App />);

    expect(await screen.findByRole("button", { name: "Send" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Verify your saved agent." })).not.toBeInTheDocument();
    expect(applySetup).not.toHaveBeenCalled();
    expect(runSetupHello).not.toHaveBeenCalled();
    await waitFor(() => expect(listSlashCommands).toHaveBeenCalledTimes(1));
    expect(listModelProfiles).toHaveBeenCalledTimes(1);
  });

  it("continues setup for an existing Agent with an incomplete model", async () => {
    const configured = {
      ...configuredSetupStatus(),
      state: "needs_configuration" as const,
      steps: { node: "complete", agent: "complete", model: "missing", credential: "not_required", hello: "pending" },
    };
    const ready = {
      ...configured,
      state: "ready" as const,
      steps: { ...configured.steps, hello: "verified" },
    };
    const getSetupStatus = vi.fn().mockResolvedValueOnce(configured).mockResolvedValue(ready);
    const applySetup = vi.fn(async () => ({
      state: "configured" as const,
      revisions: ready.revisions,
      secretState: "not_required",
      restartRequired: false,
    }));
    const runSetupHello = vi.fn(async () => ({ sessionId: "session-edited", reply: "Hello", state: "ready" as const }));
    installClient({ getSetupStatus, applySetup, runSetupHello });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Set up your first agent." })).toBeInTheDocument();
    expect(screen.getByLabelText("Node name")).toHaveValue("This Mac");
    expect(screen.queryByRole("button", { name: "Model" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Workspace folder")).toBeInTheDocument();
    expect(screen.getByLabelText("First message")).toBeInTheDocument();
    expect(screen.getByLabelText("Provider")).toBeEnabled();
    expect(screen.getByLabelText("Agent ID")).toHaveValue("main");
    expect(screen.getByLabelText("Agent ID")).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "Monica Prime" } });
    const saveAndVerify = screen.getByRole("button", { name: "Set up & say Hello" });
    await waitFor(() => expect(saveAndVerify).toBeEnabled());
    fireEvent.click(saveAndVerify);

    await waitFor(() => expect(runSetupHello).toHaveBeenCalledTimes(1));
    expect(applySetup).toHaveBeenCalledWith(expect.objectContaining({
      agent: expect.objectContaining({
        metadata: { name: "main" },
        spec: expect.objectContaining({ displayName: "Monica Prime" }),
      }),
    }));
    expect(applySetup.mock.invocationCallOrder[0]).toBeLessThan(runSetupHello.mock.invocationCallOrder[0]!);
  });

  it("restarts the Node before first Hello when setup requires it", async () => {
    const needsConfiguration = {
      state: "needs_configuration" as const,
      steps: { node: "missing", agent: "missing", model: "missing", credential: "missing", hello: "pending" },
      revisions: { node: null, agent: null, profile: null },
      recommendedWorkspace: "/workspace/openppx",
      diagnostic: null,
      current: { node: null, agent: null, profile: null },
      providers: [{
        id: "google",
        displayName: "Google Gemini",
        runtime: "google_adk",
        credentialMode: "api_key" as const,
        credentialRequired: true,
        defaultModel: "gemini-2.5-flash",
      }],
    };
    const ready = {
      ...needsConfiguration,
      state: "ready" as const,
      steps: { node: "complete", agent: "complete", model: "complete", credential: "available", hello: "verified" },
      revisions: { node: "node-revision", agent: "agent-revision", profile: "profile-revision" },
    };
    const getSetupStatus = vi.fn().mockResolvedValueOnce(needsConfiguration).mockResolvedValue(ready);
    const applySetup = vi.fn(async () => ({
      state: "configured" as const,
      revisions: ready.revisions,
      secretState: "available",
      restartRequired: true,
    }));
    const runRuntimeCommand = vi.fn(async () => buildBootstrapPayload().runtime);
    const runSetupHello = vi.fn(async () => ({ sessionId: "session-first", reply: "Hello", state: "ready" as const }));
    installClient({ getSetupStatus, applySetup, runRuntimeCommand, runSetupHello });

    render(<App />);

    await screen.findByRole("heading", { name: "Set up your first agent." });
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "test-api-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Set up & say Hello" }));

    await waitFor(() => expect(runSetupHello).toHaveBeenCalledTimes(1));
    expect(runRuntimeCommand).toHaveBeenCalledWith("restart");
    expect(applySetup.mock.invocationCallOrder[0]).toBeLessThan(runRuntimeCommand.mock.invocationCallOrder[0]!);
    expect(runRuntimeCommand.mock.invocationCallOrder[0]).toBeLessThan(runSetupHello.mock.invocationCallOrder[0]!);
  });

  it("renders an invalid setup resource without failing Desktop initialization", async () => {
    installClient({
      bootstrap: async () => {
        throw new Error("Loading agents failed because configuration is invalid.");
      },
      getSetupStatus: async () => ({
        state: "needs_configuration",
        steps: { node: "complete", agent: "complete", model: "invalid", credential: "not_required", hello: "not_started" },
        revisions: { node: "node-revision", agent: "agent-revision", profile: null },
        recommendedWorkspace: "/workspace/openppx",
        diagnostic: {
          component: "model",
          errorKind: "invalid_schema",
          issues: [{
            code: "invalid_value",
            path: ["spec", "displayName"],
            message: "Setting has an invalid value.",
            source: "model-profile:primary",
          }],
        },
        current: { node: null, agent: null, profile: null },
        providers: [],
      }),
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Set up your first agent." })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Model profile configuration is invalid (spec.displayName). Setting has an invalid value. Repair the Node configuration and retry.",
    );
    expect(screen.getByRole("button", { name: "Set up & say Hello" })).toBeDisabled();
    expect(screen.queryByText(/failed to initialize/i)).not.toBeInTheDocument();
  });

  it("uses Node-owned Codex authentication and an authoritative model selector", async () => {
    const status = {
      state: "needs_configuration" as const,
      steps: { node: "missing", agent: "missing", model: "missing", credential: "not_required", hello: "pending" },
      revisions: { node: null, agent: null, profile: null },
      recommendedWorkspace: "/workspace/openppx",
      diagnostic: null,
      current: { node: null, agent: null, profile: null },
      providers: [
        {
          id: "openai_codex",
          displayName: "OpenAI Codex",
          runtime: "codex",
          credentialMode: "oauth" as const,
          credentialRequired: false,
          defaultModel: "openai-codex/gpt-5.5",
        },
      ],
    };
    installClient({
      getSetupStatus: async () => status,
      getProviderModels: async () => ({
        providerId: "openai_codex",
        source: "codex_cli",
        authoritative: true,
        defaultModel: "openai-codex/gpt-5.5",
        items: [
          {
            id: "openai-codex/gpt-5.5",
            displayName: "GPT-5.5",
            description: "Current model",
            defaultReasoningEffort: "medium",
            reasoningEfforts: ["low", "medium", "high"],
          },
        ],
      }),
    });

    render(<App />);

    expect(await screen.findByText("Authenticated on this Node")).toBeInTheDocument();
    expect(screen.queryByLabelText("Owner")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Model")).toHaveValue("openai-codex/gpt-5.5");
    expect(screen.getByRole("button", { name: "Set up & say Hello" })).toBeEnabled();
  });

  it("creates a new Agent without manufacturing an empty Session", async () => {
    let managedAgents: AgentResourceSummary[] = [{
      id: "agent-1",
      name: "Agent 1",
      description: "Local test agent",
      enabled: true,
      status: "healthy",
      workspace: "/workspace",
      instruction: "",
      privilegeLevel: "medium",
      modelProfileId: "primary",
      avatar: null,
      tags: ["local"],
      revision: "sha256:agent",
      nodeRevision: "sha256:node",
      effect: "none",
    }];
    const createAgent = vi.fn(async () => {
      managedAgents = [...managedAgents, {
        id: "research",
        name: "Research",
        description: "Workspace: /node/workspaces/research",
        enabled: true,
        status: "healthy",
        workspace: "/node/workspaces/research",
        instruction: "",
        privilegeLevel: "medium",
        modelProfileId: "primary",
        avatar: null,
        tags: ["local", "openppx"],
        revision: "sha256:research",
        nodeRevision: "sha256:node-next",
        effect: "next_run",
      }];
      return {
        agent: {
          id: "research",
          name: "Research",
          description: "Workspace: /node/workspaces/research",
          enabled: true,
          status: "healthy" as const,
          workspace: "/node/workspaces/research",
          avatar: null,
          tags: ["local", "openppx"],
          revision: "sha256:research",
        },
        nodeRevision: "sha256:node-next",
        effect: "next_run" as const,
      };
    });
    const createSession = vi.fn(async () => {
      throw new Error("Agent creation must not create an empty Session");
    });
    installClient({
      createAgent,
      createSession,
      listManagedAgents: async () => ({ agents: managedAgents }),
      listModelProfiles: async () => ({
        profiles: [{
          id: "primary",
          displayName: "Primary",
          revision: "sha256:primary",
          provider: "openai_codex",
          model: "openai-codex/gpt-5.5",
          enabled: true,
          credentialState: "available",
        }],
      }),
    });

    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    await openSettings();
    await screen.findByRole("heading", { name: "Connection" });
    fireEvent.click(screen.getByRole("button", { name: /^Agent$/ }));
    await screen.findByRole("heading", { name: "Agents" });
    const sidebarNewAgent = document.querySelector<HTMLButtonElement>(".agent-section .section-add");
    expect(sidebarNewAgent).not.toBeNull();
    fireEvent.click(sidebarNewAgent as HTMLButtonElement);
    expect(await screen.findByRole("heading", { name: "Create a focused workspace." })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Connection" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Owner")).not.toBeInTheDocument();
    const agentName = screen.getByLabelText("Agent name");
    expect(agentName).toHaveFocus();
    fireEvent.change(agentName, { target: { value: "Research" } });
    expect(screen.getByLabelText("Agent ID")).toHaveValue("research");
    await waitFor(() => expect(screen.getByLabelText("Model Profile")).toHaveValue("primary"));
    fireEvent.click(screen.getByRole("button", { name: "Create Agent" }));

    await waitFor(() => expect(createAgent).toHaveBeenCalledWith({
      agentId: "research",
      displayName: "Research",
      workspace: null,
      privilegeLevel: "medium",
      modelProfileId: "primary",
    }));
    expect(createSession).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Agents" })).toBeInTheDocument();
    expect(await screen.findByDisplayValue("Research")).toBeInTheDocument();
    expect(screen.getByText("Research created")).toBeInTheDocument();
  });

  it("jumps to the latest reply when loading a session", async () => {
    const scrollTo = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: scrollTo,
    });

    installClient({
      loadSession: async () => ({
        messages: [
          {
            id: "message-b",
            sessionId: "session-b",
            role: "assistant",
            status: "completed",
            createdAt: "2026-04-02T10:00:02.000Z",
            parts: [{ type: "markdown", text: "Loaded Session B" }],
          },
        ],
      }),
    });

    render(<App />);

    await screen.findByText("Loaded Session A");
    fireEvent.click(screen.getByRole("button", { name: /Session B/ }));
    await screen.findByText("Loaded Session B");

    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: "auto" }));
  });

  it("does not steal scroll position while the user is reading history", async () => {
    const { emit } = installClient();
    render(<App />);

    await screen.findByText("Loaded Session A");
    const stream = document.querySelector<HTMLElement>(".message-stream");
    expect(stream).not.toBeNull();
    Object.defineProperties(stream!, {
      scrollHeight: { configurable: true, value: 1_200 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, writable: true, value: 100 },
    });
    const scrollTo = vi.fn();
    Object.defineProperty(stream!, "scrollTo", { configurable: true, value: scrollTo });

    fireEvent.scroll(stream!);
    await screen.findByRole("button", { name: /Jump to latest/ });

    act(() => {
      emit({
        type: "message.created",
        runId: "run-history",
        sessionId: "session-a",
        message: {
          id: "message-new",
          sessionId: "session-a",
          role: "assistant",
          status: "streaming",
          createdAt: "2026-04-02T10:00:03.000Z",
          parts: [{ type: "markdown", text: "New reply while reading" }],
        },
      });
    });

    await screen.findByText("New reply while reading");
    expect(scrollTo).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Jump to latest/ }));
    expect(scrollTo).toHaveBeenCalledWith({ top: 1_200, behavior: "smooth" });
  });

  it("supports workspace collapse and search shortcuts", async () => {
    installClient();
    render(<App />);

    const search = await screen.findByPlaceholderText("Search sessions");
    const collapseSidebar = screen.getByRole("button", { name: "Collapse sidebar" });
    const closeTaskPanel = screen.getByRole("button", { name: "Close task panel" });
    const sidebarHeader = screen.getByLabelText("OpenTeamwork navigation").querySelector(".sidebar-brand-row");
    expect(sidebarHeader).not.toBeNull();
    expect(within(sidebarHeader as HTMLElement).getAllByRole("button")[0]).toHaveAccessibleName("Collapse sidebar");
    expect(sidebarHeader?.querySelector(".brand-lockup")).not.toBeInTheDocument();
    expect(collapseSidebar.querySelector('[data-icon="sidebar"]')).toBeInTheDocument();
    expect(closeTaskPanel.querySelector('[data-icon="sidebar-right"]')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(search).toHaveFocus();

    fireEvent.keyDown(window, { key: "b", metaKey: true });
    expect(screen.queryByLabelText("OpenTeamwork navigation")).not.toBeInTheDocument();
    const openSidebar = await screen.findByRole("button", { name: "Open sidebar" });
    expect(openSidebar.querySelector('[data-icon="sidebar"]')).toBeInTheDocument();
    await screen.findByRole("button", { name: "New session" });
    const searchSessions = await screen.findByRole("button", { name: "Search sessions" });

    fireEvent.click(searchSessions);
    await screen.findByLabelText("OpenTeamwork navigation");
    expect(screen.getByPlaceholderText("Search sessions")).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Open sidebar" })).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "b", metaKey: true, shiftKey: true });
    const openTaskPanel = await screen.findByRole("button", { name: "Open task panel" });
    expect(openTaskPanel.querySelector('[data-icon="sidebar-right"]')).toBeInTheDocument();
  });

  it("resizes both workspace side columns and preserves their widths across collapse", async () => {
    const { storage, restore } = installLocalStorage();
    const previousInnerWidth = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1_440 });
    try {
      installClient();
      const { container, unmount } = render(<App />);

      const shell = await waitFor(() => {
        const element = container.querySelector<HTMLElement>(".app-shell");
        expect(element).not.toBeNull();
        return element!;
      });
      Object.defineProperty(shell, "clientWidth", { configurable: true, value: 1_440 });

      const leftSeparator = await screen.findByRole("separator", { name: "Resize navigation sidebar" });
      fireEvent(leftSeparator, new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 252 }));
      fireEvent(window, new MouseEvent("pointermove", { bubbles: true, clientX: 332 }));
      fireEvent(window, new MouseEvent("pointerup", { bubbles: true, clientX: 332 }));
      expect(shell.style.getPropertyValue("--left-column-custom")).toBe("332px");

      const rightSeparator = screen.getByRole("separator", { name: "Resize task panel" });
      fireEvent(rightSeparator, new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 1_124 }));
      fireEvent(window, new MouseEvent("pointermove", { bubbles: true, clientX: 1_044 }));
      fireEvent(window, new MouseEvent("pointerup", { bubbles: true, clientX: 1_044 }));
      expect(shell.style.getPropertyValue("--right-column-custom")).toBe("396px");
      expect(storage.getItem("openteamwork.desktop.column-widths.v1")).toContain('"left":332');
      expect(storage.getItem("openteamwork.desktop.column-widths.v1")).toContain('"right":396');

      fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      expect(screen.queryByRole("separator", { name: "Resize navigation sidebar" })).not.toBeInTheDocument();
      fireEvent.click(await screen.findByRole("button", { name: "Open sidebar" }));
      expect(await screen.findByRole("separator", { name: "Resize navigation sidebar" })).toBeInTheDocument();
      expect(shell.style.getPropertyValue("--left-column-custom")).toBe("332px");

      unmount();
      installClient();
      const restored = render(<App />);
      await screen.findByRole("separator", { name: "Resize navigation sidebar" });
      const restoredShell = restored.container.querySelector<HTMLElement>(".app-shell");
      expect(restoredShell?.style.getPropertyValue("--left-column-custom")).toBe("332px");
      expect(restoredShell?.style.getPropertyValue("--right-column-custom")).toBe("396px");
      restored.unmount();
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: previousInnerWidth });
      restore();
    }
  });

  it("supports keyboard resizing and double-click reset", async () => {
    const { restore } = installLocalStorage();
    try {
      installClient();
      const { container } = render(<App />);
      const leftSeparator = await screen.findByRole("separator", { name: "Resize navigation sidebar" });
      const shell = container.querySelector<HTMLElement>(".app-shell");
      expect(shell).not.toBeNull();
      Object.defineProperty(shell!, "clientWidth", { configurable: true, value: 1_440 });

      fireEvent.keyDown(leftSeparator, { key: "ArrowRight" });
      expect(shell!.style.getPropertyValue("--left-column-custom")).toBe("268px");
      fireEvent.doubleClick(leftSeparator);
      expect(shell!.style.getPropertyValue("--left-column-custom")).toBe("");
    } finally {
      restore();
    }
  });

  it("keeps a top-bar sidebar opener in Settings", async () => {
    installClient();
    render(<App />);

    await openSettings();
    fireEvent.keyDown(window, { key: "b", metaKey: true });

    expect(screen.queryByLabelText("OpenTeamwork navigation")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Open sidebar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New session" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search sessions" })).toBeInTheDocument();
  });

  it("creates a new session from the collapsed top-bar tools", async () => {
    const createdSession: SessionSummary = {
      id: "session-toolbar",
      agentId: "agent-1",
      title: "Toolbar session",
      updatedAt: "2026-04-02T10:02:00.000Z",
      lastMessagePreview: "",
    };
    const createSession = vi.fn(async () => ({ session: createdSession }));
    installClient({ createSession });
    render(<App />);

    await screen.findByText("Loaded Session A");
    fireEvent.keyDown(window, { key: "b", metaKey: true });
    fireEvent.click(await screen.findByRole("button", { name: "New session" }));

    await waitFor(() => expect(createSession).toHaveBeenCalledWith("agent-1"));
    expect(await screen.findByText("Toolbar session")).toBeInTheDocument();
  });

  it("starts with both side columns collapsed in a narrow window", async () => {
    const originalMatchMedia = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({
        matches: true,
        media: "(max-width: 1080px)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    });
    try {
      installClient();
      render(<App />);

      expect(screen.queryByLabelText("OpenTeamwork navigation")).not.toBeInTheDocument();
      await screen.findByRole("button", { name: "Open sidebar" });
      await screen.findByRole("button", { name: "Open task panel" });
      expect(screen.queryByRole("separator", { name: "Resize navigation sidebar" })).not.toBeInTheDocument();
      expect(screen.queryByRole("separator", { name: "Resize task panel" })).not.toBeInTheDocument();
    } finally {
      Object.defineProperty(window, "matchMedia", {
        configurable: true,
        value: originalMatchMedia,
      });
    }
  });

  it("closes the sidebar overlay after navigation in a narrow window", async () => {
    const originalMatchMedia = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({
        matches: true,
        media: "(max-width: 1080px)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    });
    try {
      installClient();
      render(<App />);

      fireEvent.click(await screen.findByRole("button", { name: "Open sidebar" }));
      await openSettings();

      expect(screen.queryByLabelText("OpenTeamwork navigation")).not.toBeInTheDocument();
      expect(await screen.findByRole("button", { name: "Open sidebar" })).toBeInTheDocument();
      expect(await screen.findByRole("heading", { name: "Connection" })).toBeInTheDocument();
    } finally {
      Object.defineProperty(window, "matchMedia", {
        configurable: true,
        value: originalMatchMedia,
      });
    }
  });

  it("keeps send disabled while the current agent still has a running reply", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "hello world" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByRole("button", { name: "Running" });
    expect(screen.queryByText("The current Agent is running. Progress appears in the right panel.")).not.toBeInTheDocument();
    expect(screen.getByText("Enter to send · Shift+Enter for a new line")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Session B/ }));
    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "follow up" },
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Running" })).toBeDisabled();
    });
  });

  it("keeps the current session running until the outer Run finishes", async () => {
    const { emit } = installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "hello world" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByRole("button", { name: "Running" });

    await act(async () => {
      emit({
        type: "message.updated",
        runId: "run-1",
        sessionId: "session-a",
        messageId: "assistant-1",
        status: "completed",
        replaceParts: [{ type: "markdown", text: "done" }],
      });
    });

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "second try" },
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "Running" })).toBeDisabled());

    act(() => emit({ type: "run.finished", runId: "run-1", sessionId: "session-a", status: "completed" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeEnabled());
  });

  it("refreshes Session artifacts when a Run finishes", async () => {
    const listArtifacts = vi
      .fn()
      .mockResolvedValueOnce({ artifacts: [] })
      .mockResolvedValue({
        artifacts: [{
          id: "artifact-output",
          key: "outputs/report.docx",
          fileName: "report.docx",
          mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          sizeBytes: 128,
          version: 0,
          source: "agent_output",
          createdAt: "2026-08-07T10:00:00Z",
        }],
      });
    const { emit } = installClient({ listArtifacts });

    render(<App />);

    await waitFor(() => expect(listArtifacts).toHaveBeenCalledWith("agent-1", "session-a"));
    expect(screen.getByText("No artifacts yet.")).toBeInTheDocument();

    act(() => emit({
      type: "run.finished",
      runId: "run-artifact",
      sessionId: "session-a",
      status: "completed",
    }));

    expect(await screen.findByText("report.docx")).toBeInTheDocument();
    await waitFor(() => expect(listArtifacts).toHaveBeenCalledTimes(2));
  });

  it("cancels the active Run from the composer", async () => {
    const cancelRun = vi.fn(async (runId: string) => ({ runId, status: "cancelled" as const }));
    const { emit } = installClient({
      sendMessage: async () => ({ runId: "run-stop" }),
      cancelRun,
    });
    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "long task" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const stop = await screen.findByRole("button", { name: "Stop" });
    expect(stop).toBeEnabled();
    expect(stop.querySelector("rect")).not.toBeNull();
    expect(stop.querySelector("span")).toBeNull();
    fireEvent.click(stop);
    await waitFor(() => expect(cancelRun).toHaveBeenCalledWith("run-stop"));
    expect(screen.getByRole("button", { name: "Stopping" })).toBeDisabled();

    act(() => emit({
      type: "run.finished",
      runId: "run-stop",
      sessionId: "session-a",
      status: "cancelled",
    }));
    await screen.findByRole("button", { name: "Send" });
  });

  it("shows and controls the current Goal from the composer", async () => {
    let goalState: GoalDetail = {
      goalId: "goal-1",
      sessionId: "session-a",
      agentId: "agent-1",
      userId: "ppx-client-user",
      objective: "Research Goal controls",
      status: "active",
      revision: 2,
      activeFlowId: "",
      completionCriteria: [],
      budgetState: {},
      createdAtMs: 1,
      updatedAtMs: 2,
      completedAtMs: null,
      cancelledAtMs: null,
      workspaceRef: "",
      constraints: [],
      budgetPolicy: {},
      permissionRevision: "",
      modelProfileRevision: "",
      extensionSnapshotDigest: "",
      completionEvidence: [],
      correlationId: "correlation-1",
      createdBy: "ppx-client-user",
      flow: null,
    };
    const updateGoal = vi.fn(async (input: Parameters<PpxClientApi["updateGoal"]>[0]) => {
      goalState = { ...goalState, objective: input.objective, revision: goalState.revision + 1 };
      return goalState;
    });
    const transitionGoal = vi.fn(async (operation: Parameters<PpxClientApi["transitionGoal"]>[0]) => {
      goalState = { ...goalState, status: operation === "pause" ? "paused" : "active", revision: goalState.revision + 1 };
      return goalState;
    });
    installClient({
      getCurrentGoal: async () => ({ goal: goalState }),
      updateGoal,
      transitionGoal,
    });
    render(<App />);

    const status = await screen.findByRole("region", { name: "Current Goal" });
    expect(within(status).getByText("Pursuing goal")).toBeInTheDocument();
    fireEvent.click(within(status).getByRole("button", { name: "Edit" }));
    fireEvent.change(await within(status).findByRole("textbox", { name: "Goal objective" }), {
      target: { value: "Ship Goal controls" },
    });
    fireEvent.click(within(status).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateGoal).toHaveBeenCalledWith({
      goalId: "goal-1",
      expectedRevision: 2,
      objective: "Ship Goal controls",
    }));

    fireEvent.click(within(status).getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(transitionGoal).toHaveBeenCalledWith("pause", "goal-1", 3));
    expect(await within(status).findByText("Goal paused")).toBeInTheDocument();
  });

  it("sends on Enter and keeps Shift+Enter for newline", async () => {
    const sendMessage = vi.fn(async () => ({ runId: "run-1" }));
    installClient({ sendMessage });

    render(<App />);

    await screen.findByRole("button", { name: "Send" });

    const composer = screen.getByPlaceholderText("Describe the outcome you want...");

    fireEvent.change(composer, { target: { value: "first line" } });
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter", charCode: 13 });

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith({
        agentId: "agent-1",
        sessionId: "session-a",
        text: "first line",
      });
    });

    fireEvent.change(composer, { target: { value: "hello" } });
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter", charCode: 13, shiftKey: true });

    expect(sendMessage).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["standard composition state", { isComposing: true }],
    ["legacy IME key code", { keyCode: 229 }],
  ])("does not send when Enter confirms IME input via %s", async (_caseName, eventState) => {
    const sendMessage = vi.fn(async () => ({ runId: "run-ime" }));
    installClient({ sendMessage });

    render(<App />);

    const composer = await screen.findByPlaceholderText("Describe the outcome you want...");
    fireEvent.change(composer, { target: { value: "openppx" } });
    fireEvent.keyDown(composer, {
      key: "Enter",
      code: "Enter",
      charCode: 13,
      ...eventState,
    });

    expect(sendMessage).not.toHaveBeenCalled();
    expect(composer).toHaveValue("openppx");

    fireEvent.keyDown(composer, { key: "Enter", code: "Enter", charCode: 13 });
    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
  });

  it("sorts mixed timezone timestamps and moves the active Session to the top on send", async () => {
    const payload = buildBootstrapPayload();
    payload.sessions = [
      { ...payload.sessions[0], updatedAt: "2026-08-07T16:00:00+08:00" },
      { ...payload.sessions[1], updatedAt: "2026-08-07T09:00:00Z" },
    ];
    const sendMessage = vi.fn(async () => ({ runId: "run-recency" }));
    installClient({
      bootstrap: async () => payload,
      sendMessage,
    });

    render(<App />);

    const sessionA = await screen.findByRole("button", { name: /Session A/ });
    const sessionB = screen.getByRole("button", { name: /Session B/ });
    expect(sessionB.compareDocumentPosition(sessionA) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const composer = screen.getByPlaceholderText("Describe the outcome you want...");
    fireEvent.change(composer, { target: { value: "Make Session A recent" } });
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter", charCode: 13 });

    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(sessionA.compareDocumentPosition(sessionB) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });
  });

  it("navigates the slash command palette and invokes structured commands", async () => {
    const invokeSlashCommand = vi.fn(async () => ({
      command: "/status",
      lifecycle: "side_channel" as const,
      targetActionId: "system.status",
      result: { state: "ready", node: { displayName: "Studio Node" } },
    }));
    installClient({
      listSlashCommands: async () => ({ commands: buildSlashCommands() }),
      invokeSlashCommand,
    });
    render(<App />);

    const composer = await screen.findByPlaceholderText("Describe the outcome you want...");
    fireEvent.change(composer, { target: { value: "/" } });
    const palette = await screen.findByRole("listbox", { name: "Slash commands" });
    expect(within(palette).getAllByRole("option")).toHaveLength(2);

    fireEvent.keyDown(composer, { key: "ArrowDown" });
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(composer).toHaveValue("/status");
    fireEvent.keyDown(composer, { key: "Enter" });

    await waitFor(() => {
      expect(invokeSlashCommand).toHaveBeenCalledWith({
        rawCommand: "/status",
        agentId: "agent-1",
        sessionId: "session-a",
        runId: null,
      });
    });
    expect(await screen.findByText("Node status: ready · Studio Node")).toBeInTheDocument();
  });

  it("closes the slash command palette when argument entry begins", async () => {
    const goalCommand: ProjectedSlashCommand = {
      command: "/goal",
      title: "Manage goal",
      description: "Create or manage the current Goal.",
      icon: "target",
      argHint: "[objective|status|pause|resume|cancel]",
      lifecycle: "side_channel",
      acceptsArgs: true,
      arguments: [],
      noArgsBehavior: "show_usage",
      usage: "/goal [objective|status|pause|resume|cancel]",
      order: 20,
      actionId: "goal.manage",
      available: true,
      availabilityReason: null,
    };
    installClient({ listSlashCommands: async () => ({ commands: [goalCommand] }) });
    render(<App />);

    const composer = await screen.findByPlaceholderText("Describe the outcome you want...");
    fireEvent.change(composer, { target: { value: "/g" } });
    expect(await screen.findByRole("listbox", { name: "Slash commands" })).toBeInTheDocument();

    fireEvent.change(composer, { target: { value: "/goal" } });
    expect(screen.getByRole("listbox", { name: "Slash commands" })).toBeInTheDocument();

    fireEvent.change(composer, { target: { value: "/goal Research current policy" } });
    expect(screen.queryByRole("listbox", { name: "Slash commands" })).not.toBeInTheDocument();

    fireEvent.change(composer, { target: { value: "/unknown" } });
    expect(screen.queryByRole("listbox", { name: "Slash commands" })).not.toBeInTheDocument();
  });

  it("renders a make-skill draft with explicit review commands", async () => {
    installClient({
      listSlashCommands: async () => ({ commands: buildSlashCommands() }),
      invokeSlashCommand: async () => ({
        command: "/make-skill",
        lifecycle: "side_channel" as const,
        targetActionId: "skill.draft.command",
        result: {
          operation: "draft",
          draft: {
            draftId: "draft-1",
            status: "ready_for_review",
            skillId: "weekly-sales-report",
            displayName: "Weekly sales report",
            description: "Prepare a weekly sales report.",
            triggers: ["A weekly sales report is requested."],
            inputs: ["Reviewed sales data"],
            outputs: ["Weekly sales report"],
            steps: [{ text: "Summarize the weekly sales metrics." }],
            limitations: ["The recipient must be supplied."],
            unresolvedQuestions: [],
            sourceMessageCount: 2,
            redactionCount: 1,
          },
        },
      }),
    });
    render(<App />);

    const composer = await screen.findByPlaceholderText("Describe the outcome you want...");
    fireEvent.change(composer, { target: { value: "/make-skill weekly report" } });
    fireEvent.keyDown(composer, { key: "Enter" });

    expect(await screen.findByText("Skill draft: Weekly sales report")).toBeInTheDocument();
    expect(screen.getByText(/Not execution-verified/)).toBeInTheDocument();
    expect(screen.getByText(/\/make-skill approve/)).toBeInTheDocument();
    expect(screen.getByText(/1 sensitive value was redacted/)).toBeInTheDocument();
  });

  it("removes Electron transport wording from slash command errors", async () => {
    installClient({
      listSlashCommands: async () => ({ commands: buildSlashCommands() }),
      invokeSlashCommand: async () => {
        throw new Error(
          "Error invoking remote method 'ppx-client:invoke-slash-command': ClientApiRequestError: This Session already has an unfinished Goal. Use /goal status or /goal cancel.",
        );
      },
    });
    render(<App />);

    const composer = await screen.findByPlaceholderText("Describe the outcome you want...");
    fireEvent.change(composer, { target: { value: "/goal Start another objective" } });
    fireEvent.keyDown(composer, { key: "Enter" });

    expect(
      await screen.findByText(
        "This Session already has an unfinished Goal. Use /goal status or /goal cancel.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Error invoking remote method/)).not.toBeInTheDocument();
  });

  it("orders recent commands first and explains unavailable commands", async () => {
    const local = installLocalStorage();
    local.storage.setItem("openteamwork.desktop.recentSlashCommands.v1", JSON.stringify(["/status"]));
    const commands = buildSlashCommands();
    commands[0] = {
      ...commands[0],
      available: false,
      availabilityReason: "A writable Session is required.",
    };
    installClient({ listSlashCommands: async () => ({ commands }) });
    render(<App />);

    const composer = await screen.findByPlaceholderText("Describe the outcome you want...");
    fireEvent.change(composer, { target: { value: "/" } });
    const options = within(await screen.findByRole("listbox", { name: "Slash commands" })).getAllByRole("option");
    expect(options[0]).toHaveTextContent("/status");
    expect(options[1]).toBeDisabled();
    expect(options[1]).toHaveTextContent("A writable Session is required.");
    local.restore();
  });

  it("switches to the Session returned by the new command", async () => {
    const created: SessionSummary = {
      id: "session-command",
      agentId: "agent-1",
      title: "New chat",
      updatedAt: "2026-08-03T00:00:00.000Z",
      lastMessagePreview: "",
    };
    installClient({
      listSlashCommands: async () => ({ commands: buildSlashCommands() }),
      invokeSlashCommand: async () => ({
        command: "/new",
        lifecycle: "finalize_active_turn",
        targetActionId: "session.new",
        result: { session: created },
      }),
    });
    render(<App />);

    const composer = await screen.findByPlaceholderText("Describe the outcome you want...");
    fireEvent.change(composer, { target: { value: "/new" } });
    fireEvent.keyDown(composer, { key: "Enter" });

    await waitFor(() => {
      expect(screen.getAllByText("New chat").length).toBeGreaterThan(0);
    });
    expect(screen.getByText("Agent 1 is ready")).toBeInTheDocument();
  });

  it("waits until first send to create a Session for an Agent with no history", async () => {
    const createdSession: SessionSummary = {
      id: "session-created",
      agentId: "agent-1",
      title: "New local session",
      updatedAt: "2026-04-02T10:01:00.000Z",
      lastMessagePreview: "Start a task",
    };
    const createSession = vi.fn(async () => ({ session: createdSession }));
    const sendMessage = vi.fn(async () => ({ runId: "run-1" }));
    installClient({
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        sessions: [],
        messages: [],
        selectedSessionId: "",
      }),
      listSessions: async () => ({ sessions: [] }),
      createSession,
      sendMessage,
    });

    render(<App />);

    expect(await screen.findByText("Agent 1 is ready")).toBeInTheDocument();
    expect(createSession).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "first task" },
    });
    const sendButton = screen.getByRole("button", { name: "Send" });
    expect(sendButton).toBeEnabled();
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(createSession).toHaveBeenCalledWith("agent-1");
      expect(sendMessage).toHaveBeenCalledWith({
        agentId: "agent-1",
        sessionId: "session-created",
        text: "first task",
      });
    });
  });

  it("creates a session before sending if the active session was not selected yet", async () => {
    const createdSession: SessionSummary = {
      id: "session-on-send",
      agentId: "agent-1",
      title: "New local session",
      updatedAt: "2026-04-02T10:01:00.000Z",
      lastMessagePreview: "Start a task",
    };
    const createSession = vi.fn(async () => ({ session: createdSession }));
    const sendMessage = vi.fn(async () => ({ runId: "run-1" }));
    installClient({
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        sessions: [],
        messages: [],
        selectedSessionId: "stale-session",
      }),
      listSessions: async () => ({ sessions: [] }),
      createSession,
      sendMessage,
    });

    render(<App />);

    await screen.findByText("Agent 1 is ready");

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "recover session" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(createSession).toHaveBeenCalledWith("agent-1");
      expect(sendMessage).toHaveBeenCalledWith({
        agentId: "agent-1",
        sessionId: "session-on-send",
        text: "recover session",
      });
    });
  });

  it("shows a send error instead of failing silently", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      installClient({
        sendMessage: async () => {
          throw new Error("gateway refused the run");
        },
      });

      render(<App />);

      await screen.findByRole("button", { name: "Send" });

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
        target: { value: "will fail" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Send" }));

      await screen.findByText("gateway refused the run");
    } finally {
      consoleError.mockRestore();
    }
  });

  it("renders an icon send button that activates when composer has text", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });

    const sendButton = screen.getByRole("button", { name: "Send" });
    expect(sendButton).toBeDisabled();
    expect(sendButton.querySelector("path")).not.toBeNull();
    expect(sendButton.querySelector("span")).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "hello world" },
    });

    await waitFor(() => {
      expect(sendButton).toBeEnabled();
      expect(sendButton.className).toContain("ready");
    });
  });

  it("renders compact session context in the session list", async () => {
    installClient();

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("Session A").length).toBeGreaterThan(0);
    });

    expect(screen.getByText("Preview A should stay hidden")).toBeInTheDocument();
    expect(screen.getByText("Preview B should stay hidden")).toBeInTheDocument();
  });

  it("separates active and archived sessions and requires restore before continuing", async () => {
    let sessions = buildBootstrapPayload().sessions.map((session) => ({ ...session, archived: false }));
    const archiveSession = vi.fn(async ({ sessionId, archived }: { sessionId: string; archived: boolean }) => {
      sessions = sessions.map((session) => session.id === sessionId ? { ...session, archived } : session);
      return { sessionId, archived };
    });
    installClient({
      bootstrap: async () => ({ ...buildBootstrapPayload(), sessions }),
      listSessions: async () => ({ sessions }),
      archiveSession,
    });

    render(<App />);

    const sessionA = await screen.findByRole("button", { name: /Session A/ });
    expect(screen.getByRole("button", { name: "Active sessions" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Archived sessions" })).toHaveAttribute("aria-pressed", "false");
    const sessionAShell = sessionA.closest(".session-row-shell") as HTMLElement;
    fireEvent.click(within(sessionAShell).getByRole("button", { name: "Session actions" }));
    fireEvent.click(within(sessionAShell).getByRole("button", { name: "Archive" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: /Session A/ })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Session B/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Archived sessions" }));

    const archivedSessionA = await screen.findByRole("button", { name: /Session A/ });
    expect(screen.queryByRole("button", { name: /Session B/ })).not.toBeInTheDocument();
    fireEvent.click(archivedSessionA);

    const archivedComposer = await screen.findByPlaceholderText("Restore this session to continue.");
    expect(archivedComposer).toBeDisabled();
    expect(screen.getByText("Restore this session to continue.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Attach files" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();

    const archivedShell = archivedSessionA.closest(".session-row-shell") as HTMLElement;
    fireEvent.click(within(archivedShell).getByRole("button", { name: "Session actions" }));
    fireEvent.click(within(archivedShell).getByRole("button", { name: "Restore" }));

    expect(await screen.findByText("No archived sessions.")).toBeInTheDocument();
    expect(await screen.findByPlaceholderText("Describe the outcome you want...")).toBeEnabled();
    expect(archiveSession).toHaveBeenNthCalledWith(1, {
      agentId: "agent-1",
      sessionId: "session-a",
      archived: true,
    });
    expect(archiveSession).toHaveBeenNthCalledWith(2, {
      agentId: "agent-1",
      sessionId: "session-a",
      archived: false,
    });
  });

  it("keeps a session visible and explains archive failures", async () => {
    installClient({
      archiveSession: async () => {
        throw new Error("Error invoking remote method 'ppx-client:archive-session': internal failure");
      },
    });

    render(<App />);

    const sessionA = await screen.findByRole("button", { name: /Session A/ });
    const sessionAShell = sessionA.closest(".session-row-shell") as HTMLElement;
    fireEvent.click(within(sessionAShell).getByRole("button", { name: "Session actions" }));
    fireEvent.click(within(sessionAShell).getByRole("button", { name: "Archive" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn’t archive Session A. The session was not changed. Try again.",
    );
    expect(screen.getByRole("button", { name: /Session A/ })).toBeInTheDocument();
  });

  it("disables Archive while the session has an active Run", async () => {
    const archiveSession = vi.fn(async ({ sessionId, archived }: { sessionId: string; archived: boolean }) => ({
      sessionId,
      archived,
    }));
    installClient({
      archiveSession,
      sendMessage: async () => ({ runId: "run-archive-guard" }),
    });

    render(<App />);

    const composer = await screen.findByPlaceholderText("Describe the outcome you want...");
    fireEvent.change(composer, { target: { value: "Keep this session running" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByRole("button", { name: "Stop" });

    const sessionA = screen.getByRole("button", { name: /Keep this session running/ });
    const sessionAShell = sessionA.closest(".session-row-shell") as HTMLElement;
    fireEvent.click(within(sessionAShell).getByRole("button", { name: "Session actions" }));

    const archive = within(sessionAShell).getByRole("button", { name: "Archive" });
    expect(archive).toBeDisabled();
    expect(archive).toHaveAttribute("title", "Wait for the current Run to finish before archiving.");
    fireEvent.click(archive);
    expect(archiveSession).not.toHaveBeenCalled();
  });

  it("hides the generic OpenPPX session preview", async () => {
    installClient({
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        sessions: buildBootstrapPayload().sessions.map((session) => ({
          ...session,
          lastMessagePreview: "OpenPPX session",
        })),
      }),
    });

    render(<App />);

    await screen.findByText("Loaded Session A");
    expect(screen.queryByText("OpenPPX session")).not.toBeInTheDocument();
    expect(document.querySelectorAll(".session-row-meta > span")).toHaveLength(0);
    expect(document.querySelectorAll(".session-row.compact")).toHaveLength(2);
    expect(document.querySelectorAll(".session-row.compact .session-row-meta")).toHaveLength(0);
  });

  it("uses the visible Agent list for the Node count", async () => {
    installClient({
      getDiagnostics: async () => ({ ...buildDiagnostics(), agentCount: 99 }),
    });

    render(<App />);

    await screen.findByText("Loaded Session A");
    expect(document.querySelector(".node-card-count")).toHaveTextContent("1");
  });

  it("does not show an initial block in the selected Agent summary", async () => {
    render(<App />);

    await screen.findByText("Loaded Session A");
    const agentTrigger = screen.getByRole("button", { name: /Agent 1 Local test agent/ });

    expect(agentTrigger.querySelector(".agent-monogram")).not.toBeInTheDocument();
  });

  it("uses the first user message as the visible session title", async () => {
    const createdSession: SessionSummary = {
      id: "session-created",
      agentId: "agent-1",
      title: "New chat",
      updatedAt: "2026-04-02T10:01:00.000Z",
      lastMessagePreview: "",
    };
    const sendMessage = vi.fn(async () => ({ runId: "run-1" }));
    installClient({
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        sessions: [],
        messages: [],
        selectedSessionId: "",
      }),
      listSessions: async () => ({ sessions: [] }),
      createSession: async () => ({ session: createdSession }),
      sendMessage,
    });

    render(<App />);

    expect(await screen.findByText("Agent 1 is ready")).toBeInTheDocument();
    expect(screen.queryByText("New chat")).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Describe the outcome you want..."), {
      target: { value: "帮我查一下深圳到青岛的火车和费用" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(screen.getAllByText("帮我查一下深圳到青岛的火车和费用").length).toBeGreaterThan(0);
    });
    expect(sendMessage).toHaveBeenCalledWith({
      agentId: "agent-1",
      sessionId: "session-created",
      text: "帮我查一下深圳到青岛的火车和费用",
    });
  });

  it("shows assistant identity only once across consecutive assistant replies", async () => {
    installClient({
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        messages: [
          {
            id: "message-a",
            sessionId: "session-a",
            role: "assistant",
            status: "completed",
            createdAt: "2026-04-02T10:00:01.000Z",
            parts: [{ type: "markdown", text: "First chunk" }],
          },
          {
            id: "message-b",
            sessionId: "session-a",
            role: "assistant",
            status: "streaming",
            createdAt: "2026-04-02T10:00:02.000Z",
            parts: [{ type: "step_ref", stepId: "step-1", title: "exec", status: "running", detail: "command: pwd" }],
          },
        ],
      }),
    });

    render(<App />);

    await screen.findByText("First chunk");
    const transcript = document.querySelector<HTMLElement>(".transcript-column");
    expect(transcript).not.toBeNull();
    expect(within(transcript!).getAllByText("Agent")).toHaveLength(1);
  });

  it("switches transcript and artifacts together when selecting an Agent", async () => {
    const bootstrap = buildBootstrapPayload();
    const secondSession: SessionSummary = {
      id: "session-agent-2",
      agentId: "agent-2",
      title: "Agent 2 Session",
      updatedAt: "2026-04-02T11:00:00.000Z",
      lastMessagePreview: "Agent 2 context",
    };
    const firstMessages: BootstrapPayload["messages"] = [
      {
        id: "agent-1-message",
        sessionId: "session-a",
        role: "assistant",
        status: "completed",
        createdAt: "2026-04-02T10:00:01.000Z",
        parts: [
          { type: "markdown", text: "Agent 1 transcript" },
          { type: "step_ref", stepId: "agent-1-step", title: "Agent 1 progress", status: "completed", detail: "Done" },
          { type: "file", text: "First output", fileName: "agent-1.txt", mimeType: "text/plain" },
        ],
      },
    ];
    const secondMessages: BootstrapPayload["messages"] = [
      {
        id: "agent-2-message",
        sessionId: secondSession.id,
        role: "assistant",
        status: "completed",
        createdAt: "2026-04-02T11:00:01.000Z",
        parts: [
          { type: "markdown", text: "Agent 2 transcript" },
          { type: "step_ref", stepId: "agent-2-step", title: "Agent 2 progress", status: "completed", detail: "Done" },
          { type: "file", text: "Second output", fileName: "agent-2.txt", mimeType: "text/plain" },
        ],
      },
    ];

    installClient({
      bootstrap: async () => ({
        ...bootstrap,
        agents: [
          ...bootstrap.agents,
          {
            id: "agent-2",
            name: "Agent 2",
            description: "Remote test agent",
            enabled: true,
            status: "healthy",
            tags: ["lan"],
          },
        ],
        messages: firstMessages,
      }),
      listSessions: async (agentId) => ({
        sessions: agentId === "agent-2" ? [secondSession] : bootstrap.sessions,
      }),
      loadSession: async (sessionId) => ({
        messages: sessionId === secondSession.id ? secondMessages : firstMessages,
      }),
    });

    render(<App />);

    await screen.findByText("Agent 1 transcript");
    let taskPanel = screen.getByLabelText("Task panel");
    expect(within(taskPanel).queryByText("Activity details")).not.toBeInTheDocument();
    expect(within(taskPanel).queryByText("Used Agent 1 progress")).not.toBeInTheDocument();
    expect(within(taskPanel).getByText("agent-1.txt")).toBeInTheDocument();

    const agentTrigger = screen.getByRole("button", { name: /Agent 1 Local test agent/ });
    expect(screen.queryByRole("listbox", { name: "Select Agent" })).not.toBeInTheDocument();

    fireEvent.click(agentTrigger);
    expect(screen.getByRole("listbox", { name: "Select Agent" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("listbox", { name: "Select Agent" })).not.toBeInTheDocument();
    expect(agentTrigger).toHaveFocus();

    fireEvent.click(agentTrigger);
    fireEvent.pointerDown(screen.getByText("Sessions"));
    expect(screen.queryByRole("listbox", { name: "Select Agent" })).not.toBeInTheDocument();

    await openSettings();
    await screen.findByRole("heading", { name: "Connection" });
    fireEvent.click(agentTrigger);
    fireEvent.click(screen.getByRole("option", { name: /Agent 2 Remote test agent/ }));

    await screen.findByText("Agent 2 transcript");
    expect(screen.queryByRole("heading", { name: "Connection" })).not.toBeInTheDocument();
    taskPanel = screen.getByLabelText("Task panel");
    expect(screen.queryByText("Agent 1 transcript")).not.toBeInTheDocument();
    expect(within(taskPanel).queryByText("Activity details")).not.toBeInTheDocument();
    expect(within(taskPanel).queryByText("Used Agent 2 progress")).not.toBeInTheDocument();
    expect(within(taskPanel).getByText("agent-2.txt")).toBeInTheDocument();
    expect(within(taskPanel).queryByText("Agent 1 progress")).not.toBeInTheDocument();
    expect(within(taskPanel).queryByText("agent-1.txt")).not.toBeInTheDocument();
  });

  it("clears previous messages immediately when switching sessions", async () => {
    let resolveLoad: ((value: { messages: BootstrapPayload["messages"] }) => void) | null = null;
    installClient({
      loadSession: async () =>
        await new Promise<{ messages: BootstrapPayload["messages"] }>((resolve) => {
          resolveLoad = resolve;
        }),
    });

    render(<App />);

    await screen.findByText("Loaded Session A");

    fireEvent.click(screen.getByRole("button", { name: /Session B/ }));

    await waitFor(() => {
      expect(screen.queryByText("Loaded Session A")).not.toBeInTheDocument();
    });

    await act(async () => {
      resolveLoad?.({
        messages: [
          {
            id: "message-b",
            sessionId: "session-b",
            role: "assistant",
            status: "completed",
            createdAt: "2026-04-02T10:00:02.000Z",
            parts: [{ type: "markdown", text: "Loaded Session B" }],
          },
        ],
      });
    });

    await screen.findByText("Loaded Session B");
  });

  it("does not mix background Session events into the selected transcript", async () => {
    const { emit } = installClient({ loadSession: async () => ({ messages: [] }) });
    render(<App />);

    await screen.findByText("Loaded Session A");
    fireEvent.click(screen.getByRole("button", { name: /Session B/ }));
    await waitFor(() => expect(screen.queryByText("Loaded Session A")).not.toBeInTheDocument());

    act(() => {
      emit({
        type: "message.created",
        runId: "run-background",
        sessionId: "session-a",
        message: {
          id: "background-message",
          sessionId: "session-a",
          role: "assistant",
          status: "streaming",
          createdAt: "2026-04-02T10:00:03.000Z",
          parts: [{ type: "markdown", text: "Background Session A reply" }],
        },
      });
    });

    expect(screen.queryByText("Background Session A reply")).not.toBeInTheDocument();
  });

  it("renders live diagnostics in settings view", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });

    await openSettings();

    await screen.findByRole("heading", { name: "Connection" });
    expect(screen.getByText("http://127.0.0.1:8765")).toBeInTheDocument();
    expect(screen.getByText("/tmp/openppx_root")).toBeInTheDocument();
    expect(screen.getAllByText("This Mac").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.getByText("0.6.0")).toBeInTheDocument();
    expect(screen.getByText("0.4")).toBeInTheDocument();
    expect(screen.queryByTitle("Return to conversation")).not.toBeInTheDocument();
    const deviceCard = screen.getByRole("heading", { name: "Device" }).closest("section");
    expect(deviceCard).not.toBeNull();
    expect(within(deviceCard as HTMLElement).getByText("Node")).toBeInTheDocument();
    expect(within(deviceCard as HTMLElement).getAllByText("This Mac")).toHaveLength(2);
  });

  it("renders desktop-owned interface copy in English", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    expect(screen.queryByRole("button", { name: "OpenPPX workspace" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Agent workspace/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Workspace")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "User profile" })).toBeInTheDocument();
    expect(screen.getByText("Wenhao Jiang")).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.queryByText("Connections & Settings")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("Search sessions")).toBeInTheDocument();
    expect(screen.queryByText("工作区")).not.toBeInTheDocument();
  });

  it("opens Automations and creates one from a reviewed template", async () => {
    const createAutomation = vi.fn(async (input) => ({
      automationId: "auto_created",
      name: input.name,
      description: input.description ?? "",
      instructions: input.instructions,
      outputRequirements: input.outputRequirements ?? [],
      status: "active" as const,
      agentId: input.agentId,
      userId: input.userId,
      revision: 1,
      trigger: null,
      latestRun: null,
      workspaceRef: "",
      contextMode: "isolated" as const,
      modelProfileRef: "",
      extensionPolicy: {}, permissionPolicy: {}, deliveryPolicy: {}, concurrencyPolicy: {},
      missedRunPolicy: {}, retryPolicy: {}, budgetPolicy: {}, monitorPolicy: {},
      readiness: { ready: true, reasons: [] },
      createdAtMs: Date.now(), updatedAtMs: Date.now(),
    }));
    installClient({ createAutomation });
    render(<App />);
    await screen.findByRole("button", { name: "Send" });

    fireEvent.click(screen.getByRole("button", { name: "User profile" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Automations" }));
    expect(await screen.findByRole("heading", { name: "Automations" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Morning brief/ }));
    expect(await screen.findByRole("dialog", { name: /Define the work/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create automation" }));

    await waitFor(() => expect(createAutomation).toHaveBeenCalledWith(expect.objectContaining({
      userId: "ppx-client-user",
      agentId: "agent-1",
      name: "Morning brief",
      schedule: { kind: "cron", cronExpr: "0 8 * * 1-5", timezone: "" },
    })));
  });

  it("opens Settings from the user profile menu", async () => {
    installClient();
    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    fireEvent.click(screen.getByRole("button", { name: "User profile" }));

    const menu = await screen.findByRole("menu", { name: "User menu" });
    expect(within(menu).getByText("Wenhao Jiang")).toBeInTheDocument();
    expect(within(menu).getByText("Local account")).toBeInTheDocument();
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Automations",
      "Extensions",
      "Settings",
      "Sign out",
    ]);
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Settings" }));

    expect(await screen.findByRole("heading", { name: "Connection" })).toBeInTheDocument();
    const settingsNav = screen.getByRole("navigation", { name: "Settings sections" });
    expect(within(settingsNav).getAllByRole("button").map((item) => item.textContent)).toEqual([
      "General",
      "Models",
      "Agent",
      "Operations",
      "Preferences",
    ]);
    expect(within(settingsNav).queryByRole("button", { name: "Extensions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menu", { name: "User menu" })).not.toBeInTheDocument();
  });

  it("limits a product user to account, Agent, and personal workspace controls", async () => {
    const listExtensions = vi.fn(async () => ({ extensions: [] }));
    const getSetupReadiness = vi.fn(async () => ({
      state: "configured" as const,
      workspaceReady: true,
      steps: { node: "complete", agent: "complete", model: "complete", credential: "available" },
    }));
    const getSetupStatus = vi.fn(async () => {
      throw new Error("ordinary users must not request rich setup status");
    });
    installClient({
      getUserProfile: async () => ({
        id: "user-high",
        displayName: "jiang@example.com",
        accountKind: "product",
        email: "jiang@example.com",
        privilegeLevel: "high",
      }),
      listExtensions,
      getSetupReadiness,
      getSetupStatus,
    });
    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    fireEvent.click(screen.getByRole("button", { name: "User profile" }));
    const menu = await screen.findByRole("menu", { name: "User menu" });
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Automations",
      "Settings",
      "Sign out",
    ]);
    expect(within(menu).queryByRole("menuitem", { name: "Extensions" })).not.toBeInTheDocument();
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Settings" }));

    const settingsNav = await screen.findByRole("navigation", { name: "Settings sections" });
    expect(within(settingsNav).getAllByRole("button").map((item) => item.textContent)).toEqual([
      "General",
      "Agent",
      "Preferences",
    ]);
    expect(screen.getByRole("heading", { name: "Account" })).toBeInTheDocument();
    expect(screen.getAllByText("jiang@example.com")).toHaveLength(2);
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Connection" })).not.toBeInTheDocument();

    fireEvent.click(within(settingsNav).getByRole("button", { name: "Agent" }));
    const agentSettings = (await screen.findByRole("heading", { name: "Agents" })).closest("section");
    expect(agentSettings).not.toBeNull();
    fireEvent.click(within(agentSettings as HTMLElement).getByRole("button", { name: "New Agent" }));
    const privilege = within(agentSettings as HTMLElement).getByLabelText("Privilege");
    expect(within(privilege).getAllByRole("option").map((item) => item.textContent)).toEqual([
      "Low",
      "Medium",
      "High",
    ]);
    expect(screen.queryByRole("option", { name: "Root" })).not.toBeInTheDocument();
    expect(screen.getByText("Your Agent workspace is allocated automatically.")).toBeInTheDocument();
    expect(listExtensions).not.toHaveBeenCalled();
    expect(getSetupReadiness).toHaveBeenCalledTimes(1);
    expect(getSetupStatus).not.toHaveBeenCalled();
  });

  it("opens Extensions with its types in the middle navigation column", async () => {
    installClient();
    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    fireEvent.click(screen.getByRole("button", { name: "User profile" }));

    const userMenu = await screen.findByRole("menu", { name: "User menu" });
    fireEvent.click(within(userMenu).getByRole("menuitem", { name: "Extensions" }));

    const extensionNav = await screen.findByRole("navigation", { name: "Extension sections" });
    expect(within(extensionNav).getByRole("button", { name: "Plugins" })).toHaveClass("active");
    expect(within(extensionNav).getByRole("button", { name: "Apps" })).toBeInTheDocument();
    expect(within(extensionNav).getByRole("button", { name: "Skills" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Settings sections" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menu", { name: "Extension types" })).not.toBeInTheDocument();
    fireEvent.click(within(extensionNav).getByRole("button", { name: "MCP Servers" }));

    expect(await screen.findByRole("heading", { name: "MCP Servers" })).toBeInTheDocument();
    expect(within(extensionNav).getByRole("button", { name: "MCP Servers" })).toHaveClass("active");
    expect(screen.getAllByRole("button", { name: "Add MCP server" }).length).toBeGreaterThan(0);
  });

  it("returns from Settings to Workspace when a history Session is selected", async () => {
    installClient({
      loadSession: async (sessionId) => ({
        messages: sessionId === "session-b"
          ? [
              {
                id: "message-b",
                sessionId: "session-b",
                role: "assistant",
                status: "completed",
                createdAt: "2026-04-02T10:00:02.000Z",
                parts: [{ type: "markdown", text: "Loaded Session B from Settings" }],
              },
            ]
          : [],
      }),
    });
    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    await openSettings();
    await screen.findByRole("heading", { name: "Connection" });

    fireEvent.click(screen.getByRole("button", { name: /Session B/ }));

    expect(await screen.findByText("Loaded Session B from Settings")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Connection" })).not.toBeInTheDocument();
  });

  it("returns from Settings to Workspace from the Node card", async () => {
    installClient();
    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    await openSettings();
    await screen.findByRole("heading", { name: "Connection" });

    const navigation = screen.getByLabelText("OpenTeamwork navigation");
    fireEvent.click(navigation.querySelector(".node-card") as HTMLElement);

    expect(await screen.findByRole("button", { name: "Send" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Connection" })).not.toBeInTheDocument();
  });

  it("returns from Settings to Workspace when a Session is created", async () => {
    const createdSession: SessionSummary = {
      id: "session-created-from-settings",
      agentId: "agent-1",
      title: "New session from Settings",
      updatedAt: "2026-08-04T15:00:00.000Z",
      lastMessagePreview: "",
    };
    const createSession = vi.fn(async () => ({ session: createdSession }));
    installClient({ createSession });
    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    await openSettings();
    await screen.findByRole("heading", { name: "Connection" });
    const navigation = screen.getByLabelText("OpenTeamwork navigation");
    fireEvent.click(within(navigation).getByTitle("New session"));

    await waitFor(() => expect(createSession).toHaveBeenCalledWith("agent-1"));
    expect(await screen.findByRole("button", { name: "Send" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /New session from Settings/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Connection" })).not.toBeInTheDocument();
  });

  it("uses a balanced settings grid without redundant helper copy", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    await openSettings();

    fireEvent.click(screen.getByRole("button", { name: "Operations" }));
    await screen.findByRole("heading", { name: "Runtime" });
    expect(document.querySelector(".settings-card-runtime")).toBeInTheDocument();
    expect(document.querySelector(".settings-card-health")).toBeInTheDocument();
    expect(document.querySelector(".operations-metrics")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Operations sections" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Audit" }));
    expect(await screen.findByRole("heading", { name: "Action audit" })).toBeInTheDocument();
    expect(document.querySelector(".settings-card-diagnostics")).not.toBeInTheDocument();
    expect(document.querySelector(".settings-card-config")).not.toBeInTheDocument();
    expect(document.querySelectorAll(".settings-column")).toHaveLength(0);
    expect(screen.queryByText("detail")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
  });

  it("spans standalone Agent and Preferences views across the Settings content grid", async () => {
    installClient();

    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    await openSettings();

    fireEvent.click(screen.getByRole("button", { name: "Agent" }));
    const agentHeading = await screen.findByRole("heading", { name: "Agents" });
    expect(agentHeading.closest(".settings-card")).toHaveClass("settings-page-card", "agent-lifecycle-card");

    fireEvent.click(screen.getByRole("button", { name: "Preferences" }));
    const preferencesHeading = await screen.findByRole("heading", { name: "Preferences" });
    expect(preferencesHeading.closest(".settings-card")).toHaveClass("settings-page-card", "settings-card-preferences");
  });

  it("groups Node extensions and changes enablement for the selected agent", async () => {
    const extension: ExtensionSummary = {
      kind: "skill",
      id: "repo-guide",
      displayName: "Repository guide",
      description: "Explains repository conventions.",
      version: "1.0.0",
      status: "installed",
      revision: "sha256:skill-revision",
      source: { type: "local_directory", trust: "local" },
      risk: "low",
      enabledAgentIds: [],
      readiness: { ready: true, issues: [] },
      presentation: { icon: "skill", brandColor: null },
      managedBy: null,
    };
    const setExtensionAgentEnabled = vi.fn(async () => ({
      revision: "sha256:next-revision",
      status: "enabled",
    }));
    installClient({
      listExtensions: async () => ({ extensions: [extension] }),
      setExtensionAgentEnabled,
    });

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "User profile" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Extensions" }));

    const extensionNav = await screen.findByRole("navigation", { name: "Extension sections" });
    fireEvent.click(within(extensionNav).getByRole("button", { name: "Skills" }));
    expect(await screen.findByText("Repository guide")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));

    await waitFor(() => {
      expect(setExtensionAgentEnabled).toHaveBeenCalledWith({
        kind: "skill",
        extensionId: "repo-guide",
        agentId: "agent-1",
        expectedRevision: "sha256:skill-revision",
        enabled: true,
      });
    });
  });

  it("preserves unsaved connection edits during an Operations refresh", async () => {
    const getOperationsDashboard = vi.fn(async () => ({
      overview: { state: "healthy" as const, components: [], tasks: { total: 0, byStatus: {} }, automation: { cronJobs: 0, heartbeatEnabled: false } },
      tasks: { ok: true, items: [] },
      cron: { status: {}, items: [], history: [] },
      heartbeat: { running: true, enabled: false, intervalMs: null, wakePending: false, lastRunAtMs: null, lastStatus: null, lastReason: null, lastDurationMs: null, configuration: { enabled: false, everySeconds: 1800, prompt: "Review tasks", activeHours: { start: null, end: null, timezone: "user" } } },
      usage: { requests: 0, requestTokens: 0, responseTokens: 0, totalTokens: 0, recent: [] },
      audit: [],
    }));
    installClient({ getOperationsDashboard });
    render(<App />);

    await openSettings();
    const targetName = await screen.findByLabelText("Target name");
    fireEvent.change(targetName, { target: { value: "Draft Node Name" } });
    fireEvent.click(screen.getByRole("button", { name: "Operations" }));
    fireEvent.click(await screen.findByRole("button", { name: "Refresh" }));

    await waitFor(() => expect(getOperationsDashboard.mock.calls.length).toBeGreaterThanOrEqual(2));
    fireEvent.click(screen.getByRole("button", { name: "General" }));
    expect(screen.getByLabelText("Target name")).toHaveValue("Draft Node Name");
  });

  it("rehydrates Agents and Sessions after retrying an unavailable runtime", async () => {
    const healthy = buildBootstrapPayload();
    const unavailable: BootstrapPayload = {
      ...healthy,
      runtime: {
        ...healthy.runtime,
        state: "error",
        summary: "OpenPPX Client API is unavailable.",
      },
      agents: [],
      sessions: [],
      messages: [],
      selectedAgentId: "",
      selectedSessionId: "",
    };
    const bootstrap = vi.fn().mockResolvedValueOnce(unavailable).mockResolvedValue(healthy);
    installClient({
      bootstrap,
      runRuntimeCommand: async () => healthy.runtime,
    });
    render(<App />);

    await openSettings();
    fireEvent.click(screen.getByRole("button", { name: "Operations" }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));

    await waitFor(() => expect(bootstrap).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Agent 1")).toBeInTheDocument();
    expect(screen.getByText("Session A")).toBeInTheDocument();
  });

  it("renders LAN target diagnostics when provided", async () => {
    installClient({
      getDiagnostics: async () => ({
        ...buildDiagnostics(),
        mode: "lan",
        target: { id: "remote-default", type: "remote", name: "Ops Gateway" },
        clientApiManagedByClient: false,
        clientApiBaseUrl: "http://10.0.0.8:8765",
        clientApiProcessRunning: false,
      }),
    });

    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    await openSettings();

    const deviceCard = (await screen.findByRole("heading", { name: "Device" })).closest("section");
    expect(deviceCard).not.toBeNull();
    expect(within(deviceCard as HTMLElement).getByText("Ops Gateway")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Connect to a Node on the LAN")).toBeInTheDocument();
  });

  it("saves connection settings from the settings form", async () => {
    const saveConnectionSettings = vi.fn(async () => ({
      ...buildDiagnostics(),
      mode: "lan" as const,
      target: { id: "remote-ops-gateway", type: "remote" as const, name: "Ops Gateway" },
      clientApiManagedByClient: false,
      clientApiBaseUrl: "http://10.0.0.8:8765",
    }));

    installClient({
      saveConnectionSettings,
      getDiagnostics: async () => buildDiagnostics(),
      bootstrap: async () => ({
        ...buildBootstrapPayload(),
        runtime: {
          ...buildBootstrapPayload().runtime,
          target: { id: "remote-ops-gateway", type: "remote", name: "Ops Gateway" },
        },
      }),
    });

    render(<App />);

    await screen.findByRole("button", { name: "Send" });
    await openSettings();

    fireEvent.change(screen.getByDisplayValue("This Mac"), {
      target: { value: "Ops Gateway" },
    });
    fireEvent.change(screen.getByDisplayValue("http://127.0.0.1:8765"), {
      target: { value: "http://10.0.0.8:8765" },
    });
    fireEvent.change(screen.getByLabelText("Run location"), {
      target: { value: "lan" },
    });
    fireEvent.change(screen.getByLabelText("Access token"), {
      target: { value: "test-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save & apply" }));

    await waitFor(() => {
      expect(saveConnectionSettings).toHaveBeenCalledWith({
        targetType: "lan",
        targetId: "lan-ops-gateway",
        targetName: "Ops Gateway",
        clientApiBaseUrl: "http://10.0.0.8:8765",
        accessToken: "test-token",
      });
    });
  });

  it("tests a LAN connection without saving it", async () => {
    const testConnectionSettings = vi.fn(async () => ({
      ...buildDiagnostics(),
      mode: "lan" as const,
      target: { id: "lan-studio", type: "remote" as const, name: "Studio Node" },
      nodeName: "Studio Node",
    }));
    const saveConnectionSettings = vi.fn(async () => buildDiagnostics());
    installClient({ testConnectionSettings, saveConnectionSettings });

    render(<App />);
    await screen.findByRole("button", { name: "Send" });
    await openSettings();
    fireEvent.change(screen.getByLabelText("Run location"), { target: { value: "lan" } });
    fireEvent.change(screen.getByDisplayValue("http://127.0.0.1:8765"), {
      target: { value: "http://192.168.1.8:8765" },
    });
    fireEvent.change(screen.getByLabelText("Access token"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await screen.findByText(/Connection successful: Studio Node/);
    expect(testConnectionSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        targetType: "lan",
        clientApiBaseUrl: "http://192.168.1.8:8765",
        accessToken: "secret",
      }),
    );
    expect(saveConnectionSettings).not.toHaveBeenCalled();
  });

  it("switches saved Nodes and removes only an inactive target", async () => {
    let activeTargetId = "local-this-mac";
    const removedTargets = new Set<string>();
    const profiles = () => [
      {
        targetType: "local" as const,
        targetId: "local-this-mac",
        targetName: "This Mac",
        clientApiBaseUrl: "http://127.0.0.1:18765",
        active: activeTargetId === "local-this-mac",
        credentialConfigured: false,
      },
      {
        targetType: "lan" as const,
        targetId: "lan-studio",
        targetName: "Studio Node",
        clientApiBaseUrl: "http://studio.local:18765",
        active: activeTargetId === "lan-studio",
        credentialConfigured: true,
      },
    ].filter((profile) => !removedTargets.has(profile.targetId));
    const activateConnectionProfile = vi.fn(async (targetId: string) => {
      activeTargetId = targetId;
      return {
        ...buildDiagnostics(),
        mode: "lan" as const,
        target: { id: targetId, type: "remote" as const, name: "Studio Node" },
      };
    });
    const removeConnectionProfile = vi.fn(async (targetId: string) => {
      removedTargets.add(targetId);
      return { removed: true };
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    try {
      installClient({
        listConnectionProfiles: async () => ({ profiles: profiles() }),
        activateConnectionProfile,
        removeConnectionProfile,
      });
      render(<App />);
      await screen.findByRole("button", { name: "Send" });
      await openSettings();
      await screen.findByRole("button", { name: "Use" });

      const savedNodes = (await screen.findByRole("heading", { name: "Saved Nodes" })).closest("section");
      expect(savedNodes).not.toBeNull();
      const savedNodesView = within(savedNodes as HTMLElement);
      fireEvent.click(savedNodesView.getByRole("button", { name: "Use" }));

      await waitFor(() => expect(activateConnectionProfile).toHaveBeenCalledWith("lan-studio"));
      await waitFor(() => expect(savedNodesView.getByText("Studio Node").closest("article")).toHaveClass("active"));
      const localProfile = savedNodesView.getByText("This computer").closest("article");
      expect(localProfile).not.toBeNull();
      fireEvent.click(within(localProfile as HTMLElement).getByRole("button", { name: "Remove" }));

      await waitFor(() => expect(removeConnectionProfile).toHaveBeenCalledWith("local-this-mac"));
      await waitFor(() => expect(savedNodesView.queryByText("This computer")).not.toBeInTheDocument());
      expect(confirm).toHaveBeenCalledTimes(1);
    } finally {
      confirm.mockRestore();
    }
  });
});
