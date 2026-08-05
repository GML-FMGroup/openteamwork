import { contextBridge, ipcRenderer } from "electron";
import type { AgentCreateRequest, AgentUpdateInput, AppConnectionEnablementRequest, AppConnectionRemoveRequest, AppConnectionSaveRequest, ArtifactSummary, ArtifactUploadInput, ConnectionSettings, CronCreateInput, CronUpdateInput, DesktopPlatform, ExtensionEnablementRequest, ExtensionInstallRequest, ExtensionPreviewRequest, ExtensionRemoveRequest, HeartbeatConfiguration, McpMutationRequest, ModelProfileCreateInput, ModelProfileUpdateInput, OperationsTaskControlInput, PpxClientApi, RunEvent, RuntimeCommand, SendMessageInput, SessionMutationRequest, SetupApplyRequest, SlashCommandRequest } from "../../app/src/types";

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
  getDiagnostics: () => ipcRenderer.invoke("ppx-client:get-diagnostics"),
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
  listSessions: (agentId: string) => ipcRenderer.invoke("ppx-client:list-sessions", agentId),
  createSession: (agentId: string) => ipcRenderer.invoke("ppx-client:create-session", agentId),
  renameSession: (input: SessionMutationRequest & { title: string }) => ipcRenderer.invoke("ppx-client:rename-session", input),
  archiveSession: (input: SessionMutationRequest & { archived: boolean }) => ipcRenderer.invoke("ppx-client:archive-session", input),
  forkSession: (input: SessionMutationRequest) => ipcRenderer.invoke("ppx-client:fork-session", input),
  exportSession: (input: SessionMutationRequest) => ipcRenderer.invoke("ppx-client:export-session", input),
  deleteSession: (input: SessionMutationRequest) => ipcRenderer.invoke("ppx-client:delete-session", input),
  loadSession: (sessionId: string) => ipcRenderer.invoke("ppx-client:load-session", sessionId),
  uploadArtifact: (input: ArtifactUploadInput) => ipcRenderer.invoke("ppx-client:upload-artifact", input),
  listArtifacts: (agentId: string, sessionId: string) => ipcRenderer.invoke("ppx-client:list-artifacts", agentId, sessionId),
  downloadArtifact: (agentId: string, sessionId: string, artifact: ArtifactSummary) => ipcRenderer.invoke("ppx-client:download-artifact", agentId, sessionId, artifact),
  sendMessage: (input: SendMessageInput) => ipcRenderer.invoke("ppx-client:send-message", input),
  cancelRun: (runId: string) => ipcRenderer.invoke("ppx-client:cancel-run", runId),
  listSlashCommands: () => ipcRenderer.invoke("ppx-client:list-slash-commands"),
  invokeSlashCommand: (input: SlashCommandRequest) => ipcRenderer.invoke("ppx-client:invoke-slash-command", input),
  listExtensions: () => ipcRenderer.invoke("ppx-client:list-extensions"),
  listExtensionStarters: (kind, query) => ipcRenderer.invoke("ppx-client:list-extension-starters", kind ?? null, query ?? null),
  getExtension: (kind, extensionId) => ipcRenderer.invoke("ppx-client:get-extension", kind, extensionId),
  getExtensionReadiness: (kind, extensionId) => ipcRenderer.invoke("ppx-client:get-extension-readiness", kind, extensionId),
  previewExtension: (input: ExtensionPreviewRequest) => ipcRenderer.invoke("ppx-client:preview-extension", input),
  installExtension: (input: ExtensionInstallRequest) => ipcRenderer.invoke("ppx-client:install-extension", input),
  setExtensionAgentEnabled: (input: ExtensionEnablementRequest) => ipcRenderer.invoke("ppx-client:set-extension-agent-enabled", input),
  removeExtension: (input: ExtensionRemoveRequest) => ipcRenderer.invoke("ppx-client:remove-extension", input),
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
