import { contextBridge, ipcRenderer } from "electron";
import type { AgentCreateRequest, ConnectionSettings, ExtensionEnablementRequest, PpxClientApi, RunEvent, RuntimeCommand, SendMessageInput, SetupApplyRequest, SlashCommandRequest } from "../../app/src/types";

const api: PpxClientApi = {
  bootstrap: () => ipcRenderer.invoke("ppx-client:bootstrap"),
  getDiagnostics: () => ipcRenderer.invoke("ppx-client:get-diagnostics"),
  testConnectionSettings: (settings: ConnectionSettings) => ipcRenderer.invoke("ppx-client:test-connection-settings", settings),
  saveConnectionSettings: (settings: ConnectionSettings) => ipcRenderer.invoke("ppx-client:save-connection-settings", settings),
  runRuntimeCommand: (command: RuntimeCommand) => ipcRenderer.invoke("ppx-client:runtime-command", command),
  createAgent: (input: AgentCreateRequest) => ipcRenderer.invoke("ppx-client:create-agent", input),
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
  getOperationsOverview: () => ipcRenderer.invoke("ppx-client:get-operations-overview"),
  listOperationsAudit: (limit?: number) => ipcRenderer.invoke("ppx-client:list-operations-audit", limit),
  listSessions: (agentId: string) => ipcRenderer.invoke("ppx-client:list-sessions", agentId),
  createSession: (agentId: string) => ipcRenderer.invoke("ppx-client:create-session", agentId),
  loadSession: (sessionId: string) => ipcRenderer.invoke("ppx-client:load-session", sessionId),
  sendMessage: (input: SendMessageInput) => ipcRenderer.invoke("ppx-client:send-message", input),
  cancelRun: (runId: string) => ipcRenderer.invoke("ppx-client:cancel-run", runId),
  listSlashCommands: () => ipcRenderer.invoke("ppx-client:list-slash-commands"),
  invokeSlashCommand: (input: SlashCommandRequest) => ipcRenderer.invoke("ppx-client:invoke-slash-command", input),
  listExtensions: () => ipcRenderer.invoke("ppx-client:list-extensions"),
  getExtension: (kind, extensionId) => ipcRenderer.invoke("ppx-client:get-extension", kind, extensionId),
  setExtensionAgentEnabled: (input: ExtensionEnablementRequest) => ipcRenderer.invoke("ppx-client:set-extension-agent-enabled", input),
  onRunEvent: (listener: (event: RunEvent) => void) => {
    const wrapped = (_event: unknown, payload: RunEvent) => listener(payload);
    ipcRenderer.on("ppx-client:run-event", wrapped);
    return () => {
      ipcRenderer.removeListener("ppx-client:run-event", wrapped);
    };
  },
};

contextBridge.exposeInMainWorld("ppxClient", api);
