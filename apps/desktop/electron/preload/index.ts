import { contextBridge, ipcRenderer } from "electron";
import type { AgentCreateRequest, AgentUpdateInput, AppConnectionEnablementRequest, AppConnectionRemoveRequest, AppConnectionSaveRequest, ArtifactSummary, ArtifactUploadInput, AutomationCreateInput, AutomationStatus, AutomationUpdateRequest, ConnectionSettings, ContextCompactionConfiguration, CronCreateInput, CronUpdateInput, DesktopHostPreferences, DesktopPlatform, ExtensionEnablementRequest, ExtensionInstallRequest, ExtensionPreviewRequest, ExtensionRemoveRequest, GoalTransitionOperation, GoalUpdateRequest, HeartbeatConfiguration, McpMutationRequest, ModelProfileCreateInput, ModelProfileUpdateInput, OperationsTaskControlInput, PluginMarketplaceSourceSpec, PpxClientApi, RunEvent, RuntimeCommand, SendMessageInput, SessionMutationRequest, SetupApplyRequest, SlashCommandRequest, UserLoginRequest } from "../../app/src/types";

function desktopPlatform(): DesktopPlatform {
  if (process.platform === "darwin") {
    return "macos";
  }
  if (process.platform === "win32") {
    return "windows";
  }
  if (process.platform === "linux") {
    return "linux";
  }
  return "other";
}

const api: PpxClientApi = {
  platform: desktopPlatform(),
  bootstrap: () => ipcRenderer.invoke("ppx-client:bootstrap"),
  getUserProfile: () => ipcRenderer.invoke("ppx-client:get-user-profile"),
  login: (request: UserLoginRequest) => ipcRenderer.invoke("ppx-client:login", request),
  logout: () => ipcRenderer.invoke("ppx-client:logout"),
  getDiagnostics: () => ipcRenderer.invoke("ppx-client:get-diagnostics"),
  setDesktopHostPreferences: (preferences: DesktopHostPreferences) => ipcRenderer.invoke("ppx-client:set-desktop-host-preferences", preferences),
  testConnectionSettings: (settings: ConnectionSettings) => ipcRenderer.invoke("ppx-client:test-connection-settings", settings),
  saveConnectionSettings: (settings: ConnectionSettings) => ipcRenderer.invoke("ppx-client:save-connection-settings", settings),
  listConnectionProfiles: () => ipcRenderer.invoke("ppx-client:list-connection-profiles"),
  activateConnectionProfile: (targetId: string) => ipcRenderer.invoke("ppx-client:activate-connection-profile", targetId),
  removeConnectionProfile: (targetId: string) => ipcRenderer.invoke("ppx-client:remove-connection-profile", targetId),
  runRuntimeCommand: (command: RuntimeCommand) => ipcRenderer.invoke("ppx-client:runtime-command", command),
  createAgent: (input: AgentCreateRequest) => ipcRenderer.invoke("ppx-client:create-agent", input),
  listManagedAgents: () => ipcRenderer.invoke("ppx-client:list-managed-agents"),
  updateAgent: (input: AgentUpdateInput) => ipcRenderer.invoke("ppx-client:update-agent", input),
  setAgentEnabled: (agentId: string, enabled: boolean) => ipcRenderer.invoke("ppx-client:set-agent-enabled", agentId, enabled),
  removeAgent: (agentId: string, expectedRevision: string) => ipcRenderer.invoke("ppx-client:remove-agent", agentId, expectedRevision),
  getSetupStatus: () => ipcRenderer.invoke("ppx-client:get-setup-status"),
  getSetupReadiness: () => ipcRenderer.invoke("ppx-client:get-setup-readiness"),
  applySetup: (request: SetupApplyRequest) => ipcRenderer.invoke("ppx-client:apply-setup", request),
  runSetupHello: (agentId: string, userId: string, text: string) =>
    ipcRenderer.invoke("ppx-client:run-setup-hello", agentId, userId, text),
  getProviderModels: (providerId: string) => ipcRenderer.invoke("ppx-client:get-provider-models", providerId),
  getProviderAuthStatus: (providerId: string) => ipcRenderer.invoke("ppx-client:get-provider-auth-status", providerId),
  beginProviderAuth: (providerId: string) => ipcRenderer.invoke("ppx-client:begin-provider-auth", providerId),
  refreshProviderAuth: (providerId: string) => ipcRenderer.invoke("ppx-client:refresh-provider-auth", providerId),
  openExternalUrl: (url: string) => ipcRenderer.invoke("ppx-client:open-external-url", url),
  listModelProfiles: () => ipcRenderer.invoke("ppx-client:list-model-profiles"),
  readModelProfile: (profileId: string) => ipcRenderer.invoke("ppx-client:read-model-profile", profileId),
  createModelProfile: (input: ModelProfileCreateInput) => ipcRenderer.invoke("ppx-client:create-model-profile", input),
  updateModelProfile: (input: ModelProfileUpdateInput) => ipcRenderer.invoke("ppx-client:update-model-profile", input),
  getOperationsOverview: () => ipcRenderer.invoke("ppx-client:get-operations-overview"),
  listOperationsAudit: (limit?: number) => ipcRenderer.invoke("ppx-client:list-operations-audit", limit),
  getOperationsDashboard: () => ipcRenderer.invoke("ppx-client:get-operations-dashboard"),
  getOperationsTask: (taskId: string) => ipcRenderer.invoke("ppx-client:get-operations-task", taskId),
  getOperationsTaskOutput: (taskId: string) => ipcRenderer.invoke("ppx-client:get-operations-task-output", taskId),
  controlOperationsTask: (input: OperationsTaskControlInput) => ipcRenderer.invoke("ppx-client:control-operations-task", input),
  createOperationsCron: (input: CronCreateInput) => ipcRenderer.invoke("ppx-client:create-operations-cron", input),
  updateOperationsCron: (input: CronUpdateInput) => ipcRenderer.invoke("ppx-client:update-operations-cron", input),
  setOperationsCronEnabled: (jobId: string, enabled: boolean) => ipcRenderer.invoke("ppx-client:set-operations-cron-enabled", jobId, enabled),
  runOperationsCron: (jobId: string) => ipcRenderer.invoke("ppx-client:run-operations-cron", jobId),
  removeOperationsCron: (jobId: string) => ipcRenderer.invoke("ppx-client:remove-operations-cron", jobId),
  runOperationsHeartbeat: () => ipcRenderer.invoke("ppx-client:run-operations-heartbeat"),
  configureOperationsHeartbeat: (input: HeartbeatConfiguration) => ipcRenderer.invoke("ppx-client:configure-operations-heartbeat", input),
  configureOperationsContextCompaction: (input: ContextCompactionConfiguration) => ipcRenderer.invoke("ppx-client:configure-operations-context-compaction", input),
  listSessions: (agentId: string) => ipcRenderer.invoke("ppx-client:list-sessions", agentId),
  createSession: (agentId: string) => ipcRenderer.invoke("ppx-client:create-session", agentId),
  renameSession: (input: SessionMutationRequest & { title: string }) => ipcRenderer.invoke("ppx-client:rename-session", input),
  archiveSession: (input: SessionMutationRequest & { archived: boolean }) => ipcRenderer.invoke("ppx-client:archive-session", input),
  forkSession: (input: SessionMutationRequest) => ipcRenderer.invoke("ppx-client:fork-session", input),
  exportSession: (input: SessionMutationRequest) => ipcRenderer.invoke("ppx-client:export-session", input),
  deleteSession: (input: SessionMutationRequest) => ipcRenderer.invoke("ppx-client:delete-session", input),
  loadSession: (sessionId: string) => ipcRenderer.invoke("ppx-client:load-session", sessionId),
  getCurrentGoal: (sessionId: string) => ipcRenderer.invoke("ppx-client:get-current-goal", sessionId),
  updateGoal: (input: GoalUpdateRequest) => ipcRenderer.invoke("ppx-client:update-goal", input),
  transitionGoal: (operation: GoalTransitionOperation, goalId: string, expectedRevision: number) =>
    ipcRenderer.invoke("ppx-client:transition-goal", operation, goalId, expectedRevision),
  retryGoalStep: (goalId: string, expectedRevision: number, stepId?: string | null) =>
    ipcRenderer.invoke("ppx-client:retry-goal-step", goalId, expectedRevision, stepId ?? null),
  listAutomations: (statuses?: AutomationStatus[]) => ipcRenderer.invoke("ppx-client:list-automations", statuses ?? []),
  getAutomation: (automationId: string) => ipcRenderer.invoke("ppx-client:get-automation", automationId),
  createAutomation: (input: AutomationCreateInput) => ipcRenderer.invoke("ppx-client:create-automation", input),
  updateAutomation: (input: AutomationUpdateRequest) => ipcRenderer.invoke("ppx-client:update-automation", input),
  transitionAutomation: (operation, automationId, expectedRevision) =>
    ipcRenderer.invoke("ppx-client:transition-automation", operation, automationId, expectedRevision),
  runAutomation: (automationId: string, input?: Record<string, unknown>) =>
    ipcRenderer.invoke("ppx-client:run-automation", automationId, input ?? {}),
  getAutomationHistory: (automationId: string) => ipcRenderer.invoke("ppx-client:get-automation-history", automationId),
  listAutomationTemplates: () => ipcRenderer.invoke("ppx-client:list-automation-templates"),
  uploadArtifact: (input: ArtifactUploadInput) => ipcRenderer.invoke("ppx-client:upload-artifact", input),
  listArtifacts: (agentId: string, sessionId: string) => ipcRenderer.invoke("ppx-client:list-artifacts", agentId, sessionId),
  downloadArtifact: (agentId: string, sessionId: string, artifact: ArtifactSummary) => ipcRenderer.invoke("ppx-client:download-artifact", agentId, sessionId, artifact),
  sendMessage: (input: SendMessageInput) => ipcRenderer.invoke("ppx-client:send-message", input),
  cancelRun: (runId: string) => ipcRenderer.invoke("ppx-client:cancel-run", runId),
  listSlashCommands: (agentId?: string | null) =>
    ipcRenderer.invoke("ppx-client:list-slash-commands", agentId ?? null),
  invokeSlashCommand: (input: SlashCommandRequest) => ipcRenderer.invoke("ppx-client:invoke-slash-command", input),
  listExtensions: () => ipcRenderer.invoke("ppx-client:list-extensions"),
  listExtensionStarters: (kind, query) => ipcRenderer.invoke("ppx-client:list-extension-starters", kind ?? null, query ?? null),
  installAppStarter: (starterId: string) => ipcRenderer.invoke("ppx-client:install-app-starter", starterId),
  getExtension: (kind, extensionId) => ipcRenderer.invoke("ppx-client:get-extension", kind, extensionId),
  getExtensionReadiness: (kind, extensionId) => ipcRenderer.invoke("ppx-client:get-extension-readiness", kind, extensionId),
  getExtensionHealthHistory: (kind, extensionId, limit) => ipcRenderer.invoke("ppx-client:get-extension-health-history", kind, extensionId, limit ?? 10),
  previewExtension: (input: ExtensionPreviewRequest) => ipcRenderer.invoke("ppx-client:preview-extension", input),
  installExtension: (input: ExtensionInstallRequest) => ipcRenderer.invoke("ppx-client:install-extension", input),
  setExtensionAgentEnabled: (input: ExtensionEnablementRequest) => ipcRenderer.invoke("ppx-client:set-extension-agent-enabled", input),
  removeExtension: (input: ExtensionRemoveRequest) => ipcRenderer.invoke("ppx-client:remove-extension", input),
  getPluginHookStatus: (pluginId: string, expectedRevision: string) => ipcRenderer.invoke("ppx-client:get-plugin-hook-status", pluginId, expectedRevision),
  setPluginHookTrust: (pluginId: string, expectedRevision: string, trusted: boolean) => ipcRenderer.invoke("ppx-client:set-plugin-hook-trust", pluginId, expectedRevision, trusted),
  listPluginMarketplaces: () => ipcRenderer.invoke("ppx-client:list-plugin-marketplaces"),
  listPluginMarketplaceEntries: (query?: string) => ipcRenderer.invoke("ppx-client:list-plugin-marketplace-entries", query ?? null),
  savePluginMarketplace: (input: { marketplaceId: string; spec: PluginMarketplaceSourceSpec; expectedRevision: string | null }) => ipcRenderer.invoke("ppx-client:save-plugin-marketplace", input),
  refreshPluginMarketplace: (marketplaceId: string, expectedRevision: string) => ipcRenderer.invoke("ppx-client:refresh-plugin-marketplace", marketplaceId, expectedRevision),
  removePluginMarketplace: (marketplaceId: string, expectedRevision: string) => ipcRenderer.invoke("ppx-client:remove-plugin-marketplace", marketplaceId, expectedRevision),
  createMcpServer: (input: McpMutationRequest) => ipcRenderer.invoke("ppx-client:create-mcp-server", input),
  updateMcpServer: (input: McpMutationRequest) => ipcRenderer.invoke("ppx-client:update-mcp-server", input),
  beginMcpOAuth: (serverId: string) => ipcRenderer.invoke("ppx-client:begin-mcp-oauth", serverId),
  getMcpOAuthStatus: (serverId: string) => ipcRenderer.invoke("ppx-client:get-mcp-oauth-status", serverId),
  signOutMcpOAuth: (serverId: string) => ipcRenderer.invoke("ppx-client:sign-out-mcp-oauth", serverId),
  testMcpServer: (serverId: string) => ipcRenderer.invoke("ppx-client:test-mcp-server", serverId),
  saveAppConnection: (input: AppConnectionSaveRequest) => ipcRenderer.invoke("ppx-client:save-app-connection", input),
  testAppConnection: (connectionId: string) => ipcRenderer.invoke("ppx-client:test-app-connection", connectionId),
  setAppConnectionAgentEnabled: (input: AppConnectionEnablementRequest) => ipcRenderer.invoke("ppx-client:set-app-connection-agent-enabled", input),
  removeAppConnection: (input: AppConnectionRemoveRequest) => ipcRenderer.invoke("ppx-client:remove-app-connection", input),
  onRunEvent: (listener: (event: RunEvent) => void) => {
    const wrapped = (_event: unknown, payload: RunEvent) => listener(payload);
    ipcRenderer.on("ppx-client:run-event", wrapped);
    return () => {
      ipcRenderer.removeListener("ppx-client:run-event", wrapped);
    };
  },
};

contextBridge.exposeInMainWorld("ppxClient", api);
