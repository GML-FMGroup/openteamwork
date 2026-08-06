import { app, BrowserWindow, dialog, ipcMain, Notification, shell } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { ClientDiagnostics, DesktopHostPreferences } from "../../app/src/types";
import {
  validateConnectionSettings,
  validateAutomationCreateInput,
  validateAutomationInput,
  validateAutomationOperation,
  validateAutomationRevision,
  validateAutomationStatuses,
  validateAutomationUpdateInput,
  validateAgentCreateRequest,
  validateAgentUpdateInput,
  validateArtifactSummaryInput,
  validateArtifactUploadInput,
  validateExtensionEnablement,
  validateExtensionInstallRequest,
  validateExtensionKind,
  validateExtensionHealthKind,
  validateExtensionHealthLimit,
  validateExtensionPreviewRequest,
  validateExtensionRemoveRequest,
  validateMcpMutationRequest,
  validateAppConnectionSaveRequest,
  validateAppConnectionEnablementRequest,
  validateAppConnectionRemoveRequest,
  validateExternalUrl,
  validateIdentifier,
  validateRuntimeCommand,
  validateSearchQuery,
  validateProviderId,
  validateModelProfileCreateInput,
  validateModelProfileUpdateInput,
  validateModelProfileId,
  validateOperationsCronCreateInput,
  validateOperationsCronUpdateInput,
  validateHeartbeatConfiguration,
  validateOperationsTaskControlInput,
  validatePluginMarketplaceSaveRequest,
  validateSendMessageInput,
  validateSetupApplyRequest,
  validateSetupHelloText,
  validateSlashCommandRequest,
  validateSessionArchiveRequest,
  validateSessionMutationRequest,
  validateSessionRenameRequest,
} from "./ipc-validation";
import { OpenPpxLocalAdapter } from "./openppx-local-adapter";
import {
  readSecureConnectionSettings,
  listSecureConnectionProfiles,
  readSecureConnectionProfile,
  removeSecureConnectionProfile,
  resolveCandidateConnectionSettings,
  setActiveSecureConnectionProfile,
  writeSecureConnectionSettings,
} from "./secure-connection-store";

let mainWindow: BrowserWindow | null = null;
let unsubscribeRunEvents: (() => void) | null = null;
let adapter: OpenPpxLocalAdapter | null = null;
let quitting = false;
let hostPreferences: DesktopHostPreferences = {
  backgroundBehavior: "confirm-before-close",
  notificationsEnabled: false,
  notificationSound: false,
};
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

app.setName("OpenPPX Desktop");

/** Attach the packaged Desktop version to Main-owned runtime diagnostics. */
function withDesktopVersion(diagnostics: ClientDiagnostics): ClientDiagnostics {
  return { ...diagnostics, desktopVersion: app.getVersion() };
}

function createWindow(): void {
  const preloadPath = path.join(__dirname, "../preload/index.cjs");
  const isMac = process.platform === "darwin";

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 960,
    minHeight: 760,
    backgroundColor: "#f5f6f7",
    title: "OpenPPX Desktop",
    titleBarStyle: isMac ? "hiddenInset" : "default",
    trafficLightPosition: isMac ? { x: 22, y: 22 } : undefined,
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  adapter = new OpenPpxLocalAdapter(readSecureConnectionSettings() ?? undefined);
  unsubscribeRunEvents = adapter.onRunEvent((event) => {
    mainWindow?.webContents.send("ppx-client:run-event", event);
    if (
      event.type === "run.finished"
      && hostPreferences.notificationsEnabled
      && Notification.isSupported()
      && !mainWindow?.isFocused()
    ) {
      new Notification({
        title: "OpenPPX",
        body: "Agent run finished.",
        silent: !hostPreferences.notificationSound,
      }).show();
    }
  });

  mainWindow.on("close", (event) => {
    if (quitting) return;
    if (hostPreferences.backgroundBehavior === "keep-running") {
      event.preventDefault();
      mainWindow?.hide();
      return;
    }
    const choice = dialog.showMessageBoxSync(mainWindow!, {
      type: "question",
      buttons: ["Keep Open", "Close Desktop"],
      defaultId: 0,
      cancelId: 0,
      title: "Close OpenPPX Desktop?",
      message: "Close the Desktop window?",
      detail: "Active Node work is not treated as cancelled, but this Desktop connection will close.",
      noLink: true,
    });
    if (choice === 0) event.preventDefault();
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    void mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    void mainWindow.loadFile(path.join(__dirname, "../../dist/index.html"));
  }
}

app.whenReady().then(() => {
  ipcMain.handle("ppx-client:bootstrap", async () => adapter!.bootstrap());
  ipcMain.handle("ppx-client:get-user-profile", () => adapter!.getUserProfile());
  ipcMain.handle("ppx-client:get-diagnostics", async () => withDesktopVersion(await adapter!.getDiagnostics()));
  ipcMain.handle("ppx-client:set-desktop-host-preferences", (_event, preferences: DesktopHostPreferences) => {
    if (
      !preferences
      || (preferences.backgroundBehavior !== "keep-running" && preferences.backgroundBehavior !== "confirm-before-close")
      || typeof preferences.notificationsEnabled !== "boolean"
      || typeof preferences.notificationSound !== "boolean"
    ) {
      throw new TypeError("Invalid Desktop host preferences.");
    }
    hostPreferences = { ...preferences };
  });
  ipcMain.handle("ppx-client:test-connection-settings", async (_event, settings: unknown) => {
    const candidate = resolveCandidateConnectionSettings(validateConnectionSettings(settings));
    return withDesktopVersion(await adapter!.testConnectionSettings(candidate));
  });
  ipcMain.handle("ppx-client:save-connection-settings", async (_event, settings: unknown) => {
    const candidate = resolveCandidateConnectionSettings(validateConnectionSettings(settings));
    await adapter!.testConnectionSettings(candidate);
    writeSecureConnectionSettings(candidate);
    adapter!.applyConnectionSettings(candidate);
    return withDesktopVersion(await adapter!.getDiagnostics());
  });
  ipcMain.handle("ppx-client:list-connection-profiles", () => ({ profiles: listSecureConnectionProfiles() }));
  ipcMain.handle("ppx-client:activate-connection-profile", async (_event, targetId: unknown) => {
    const settings = readSecureConnectionProfile(validateIdentifier(targetId, "Node target id"));
    await adapter!.testConnectionSettings(settings);
    setActiveSecureConnectionProfile(settings.targetId);
    adapter!.applyConnectionSettings(settings);
    return withDesktopVersion(await adapter!.getDiagnostics());
  });
  ipcMain.handle("ppx-client:remove-connection-profile", (_event, targetId: unknown) => {
    removeSecureConnectionProfile(validateIdentifier(targetId, "Node target id"));
    return { removed: true };
  });
  ipcMain.handle("ppx-client:runtime-command", async (_event, command: unknown) =>
    adapter!.runRuntimeCommand(validateRuntimeCommand(command)),
  );
  ipcMain.handle("ppx-client:create-agent", async (_event, input: unknown) =>
    adapter!.createAgent(validateAgentCreateRequest(input)),
  );
  ipcMain.handle("ppx-client:list-managed-agents", async () => adapter!.listManagedAgents());
  ipcMain.handle("ppx-client:update-agent", async (_event, input: unknown) =>
    adapter!.updateAgent(validateAgentUpdateInput(input)),
  );
  ipcMain.handle("ppx-client:set-agent-enabled", async (_event, agentId: unknown, enabled: unknown) => {
    if (typeof enabled !== "boolean") throw new TypeError("Agent enabled must be a boolean.");
    return adapter!.setAgentEnabled(validateIdentifier(agentId, "Agent id"), enabled);
  });
  ipcMain.handle("ppx-client:remove-agent", async (_event, agentId: unknown, expectedRevision: unknown) =>
    adapter!.removeAgent(
      validateIdentifier(agentId, "Agent id"),
      validateIdentifier(expectedRevision, "Expected Agent revision"),
    ),
  );
  ipcMain.handle("ppx-client:get-setup-status", async () => adapter!.getSetupStatus());
  ipcMain.handle("ppx-client:apply-setup", async (_event, request: unknown) =>
    adapter!.applySetup(validateSetupApplyRequest(request)),
  );
  ipcMain.handle("ppx-client:run-setup-hello", async (_event, agentId: unknown, userId: unknown, text: unknown) =>
    adapter!.runSetupHello(
      validateIdentifier(agentId, "Agent id"),
      validateIdentifier(userId, "User id"),
      validateSetupHelloText(text),
    ),
  );
  ipcMain.handle("ppx-client:get-provider-models", async (_event, providerId: unknown) =>
    adapter!.getProviderModels(validateProviderId(providerId)),
  );
  ipcMain.handle("ppx-client:get-provider-auth-status", async (_event, providerId: unknown) =>
    adapter!.getProviderAuthStatus(validateProviderId(providerId)),
  );
  ipcMain.handle("ppx-client:begin-provider-auth", async (_event, providerId: unknown) =>
    adapter!.beginProviderAuth(validateProviderId(providerId)),
  );
  ipcMain.handle("ppx-client:refresh-provider-auth", async (_event, providerId: unknown) =>
    adapter!.refreshProviderAuth(validateProviderId(providerId)),
  );
  ipcMain.handle("ppx-client:open-external-url", async (_event, url: unknown) => {
    await shell.openExternal(validateExternalUrl(url));
  });
  ipcMain.handle("ppx-client:list-model-profiles", async () => adapter!.listModelProfiles());
  ipcMain.handle("ppx-client:read-model-profile", async (_event, profileId: unknown) =>
    adapter!.readModelProfile(validateModelProfileId(profileId)),
  );
  ipcMain.handle("ppx-client:create-model-profile", async (_event, input: unknown) =>
    adapter!.createModelProfile(validateModelProfileCreateInput(input)),
  );
  ipcMain.handle("ppx-client:update-model-profile", async (_event, input: unknown) =>
    adapter!.updateModelProfile(validateModelProfileUpdateInput(input)),
  );
  ipcMain.handle("ppx-client:get-operations-overview", async () => adapter!.getOperationsOverview());
  ipcMain.handle("ppx-client:list-operations-audit", async (_event, limit: unknown) =>
    adapter!.listOperationsAudit(
      typeof limit === "number" && Number.isInteger(limit) && limit > 0 && limit <= 200 ? limit : 20,
    ),
  );
  ipcMain.handle("ppx-client:get-operations-dashboard", async () => adapter!.getOperationsDashboard());
  ipcMain.handle("ppx-client:get-operations-task", async (_event, taskId: unknown) =>
    adapter!.getOperationsTask(validateIdentifier(taskId, "Task id")),
  );
  ipcMain.handle("ppx-client:get-operations-task-output", async (_event, taskId: unknown) =>
    adapter!.getOperationsTaskOutput(validateIdentifier(taskId, "Task id")),
  );
  ipcMain.handle("ppx-client:control-operations-task", async (_event, input: unknown) =>
    adapter!.controlOperationsTask(validateOperationsTaskControlInput(input)),
  );
  ipcMain.handle("ppx-client:create-operations-cron", async (_event, input: unknown) =>
    adapter!.createOperationsCron(validateOperationsCronCreateInput(input)),
  );
  ipcMain.handle("ppx-client:update-operations-cron", async (_event, input: unknown) =>
    adapter!.updateOperationsCron(validateOperationsCronUpdateInput(input)),
  );
  ipcMain.handle("ppx-client:set-operations-cron-enabled", async (_event, jobId: unknown, enabled: unknown) => {
    if (typeof enabled !== "boolean") throw new TypeError("Cron enabled must be a boolean.");
    return adapter!.setOperationsCronEnabled(validateIdentifier(jobId, "Cron job id"), enabled);
  });
  ipcMain.handle("ppx-client:run-operations-cron", async (_event, jobId: unknown) =>
    adapter!.runOperationsCron(validateIdentifier(jobId, "Cron job id")),
  );
  ipcMain.handle("ppx-client:remove-operations-cron", async (_event, jobId: unknown) =>
    adapter!.removeOperationsCron(validateIdentifier(jobId, "Cron job id")),
  );
  ipcMain.handle("ppx-client:run-operations-heartbeat", async () => adapter!.runOperationsHeartbeat());
  ipcMain.handle("ppx-client:configure-operations-heartbeat", async (_event, input: unknown) =>
    adapter!.configureOperationsHeartbeat(validateHeartbeatConfiguration(input)),
  );
  ipcMain.handle("ppx-client:list-sessions", async (_event, agentId: unknown) =>
    adapter!.listSessions(validateIdentifier(agentId, "Agent id")),
  );
  ipcMain.handle("ppx-client:create-session", async (_event, agentId: unknown) =>
    adapter!.createSession(validateIdentifier(agentId, "Agent id")),
  );
  ipcMain.handle("ppx-client:rename-session", async (_event, input: unknown) =>
    adapter!.renameSession(validateSessionRenameRequest(input)),
  );
  ipcMain.handle("ppx-client:archive-session", async (_event, input: unknown) =>
    adapter!.archiveSession(validateSessionArchiveRequest(input)),
  );
  ipcMain.handle("ppx-client:fork-session", async (_event, input: unknown) =>
    adapter!.forkSession(validateSessionMutationRequest(input)),
  );
  ipcMain.handle("ppx-client:export-session", async (_event, input: unknown) =>
    adapter!.exportSession(validateSessionMutationRequest(input)),
  );
  ipcMain.handle("ppx-client:delete-session", async (_event, input: unknown) =>
    adapter!.deleteSession(validateSessionMutationRequest(input)),
  );
  ipcMain.handle("ppx-client:load-session", async (_event, sessionId: unknown) =>
    adapter!.loadSession(validateIdentifier(sessionId, "Session id")),
  );
  ipcMain.handle("ppx-client:get-current-goal", async (_event, sessionId: unknown) =>
    adapter!.getCurrentGoal(validateIdentifier(sessionId, "Session id")),
  );
  ipcMain.handle("ppx-client:list-automations", async (_event, statuses: unknown) =>
    adapter!.listAutomations(validateAutomationStatuses(statuses)),
  );
  ipcMain.handle("ppx-client:get-automation", async (_event, automationId: unknown) =>
    adapter!.getAutomation(validateIdentifier(automationId, "Automation id")),
  );
  ipcMain.handle("ppx-client:create-automation", async (_event, input: unknown) =>
    adapter!.createAutomation(validateAutomationCreateInput(input)),
  );
  ipcMain.handle("ppx-client:update-automation", async (_event, input: unknown) =>
    adapter!.updateAutomation(validateAutomationUpdateInput(input)),
  );
  ipcMain.handle("ppx-client:transition-automation", async (_event, operation: unknown, automationId: unknown, expectedRevision: unknown) =>
    adapter!.transitionAutomation(
      validateAutomationOperation(operation),
      validateIdentifier(automationId, "Automation id"),
      validateAutomationRevision(expectedRevision),
    ),
  );
  ipcMain.handle("ppx-client:run-automation", async (_event, automationId: unknown, input: unknown) =>
    adapter!.runAutomation(
      validateIdentifier(automationId, "Automation id"),
      validateAutomationInput(input),
    ),
  );
  ipcMain.handle("ppx-client:get-automation-history", async (_event, automationId: unknown) =>
    adapter!.getAutomationHistory(validateIdentifier(automationId, "Automation id")),
  );
  ipcMain.handle("ppx-client:list-automation-templates", async () => adapter!.listAutomationTemplates());
  ipcMain.handle("ppx-client:upload-artifact", async (_event, input: unknown) =>
    adapter!.uploadArtifact(validateArtifactUploadInput(input)),
  );
  ipcMain.handle("ppx-client:list-artifacts", async (_event, agentId: unknown, sessionId: unknown) =>
    adapter!.listArtifacts(
      validateIdentifier(agentId, "Agent id"),
      validateIdentifier(sessionId, "Session id"),
    ),
  );
  ipcMain.handle("ppx-client:download-artifact", async (_event, agentId: unknown, sessionId: unknown, artifact: unknown) =>
    adapter!.downloadArtifact(
      validateIdentifier(agentId, "Agent id"),
      validateIdentifier(sessionId, "Session id"),
      validateArtifactSummaryInput(artifact),
    ),
  );
  ipcMain.handle("ppx-client:send-message", async (_event, input: unknown) =>
    adapter!.sendMessage(validateSendMessageInput(input)),
  );
  ipcMain.handle("ppx-client:cancel-run", async (_event, runId: unknown) =>
    adapter!.cancelRun(validateIdentifier(runId, "Run id")),
  );
  ipcMain.handle("ppx-client:list-slash-commands", async () => adapter!.listSlashCommands());
  ipcMain.handle("ppx-client:invoke-slash-command", async (_event, input: unknown) =>
    adapter!.invokeSlashCommand(validateSlashCommandRequest(input)),
  );
  ipcMain.handle("ppx-client:list-extensions", async () => adapter!.listExtensions());
  ipcMain.handle("ppx-client:list-extension-starters", async (_event, kind: unknown, query: unknown) =>
    adapter!.listExtensionStarters(
      kind === null || kind === undefined ? undefined : validateExtensionKind(kind),
      query === null || query === undefined || query === "" ? undefined : validateSearchQuery(query),
    ),
  );
  ipcMain.handle("ppx-client:install-app-starter", async (_event, starterId: unknown) =>
    adapter!.installAppStarter(validateIdentifier(starterId, "App starter id")),
  );
  ipcMain.handle("ppx-client:get-extension", async (_event, kind: unknown, extensionId: unknown) =>
    adapter!.getExtension(
      validateExtensionKind(kind),
      validateIdentifier(extensionId, "Extension id"),
    ),
  );
  ipcMain.handle("ppx-client:get-extension-readiness", async (_event, kind: unknown, extensionId: unknown) =>
    adapter!.getExtensionReadiness(
      validateExtensionKind(kind),
      validateIdentifier(extensionId, "Extension id"),
    ),
  );
  ipcMain.handle("ppx-client:get-extension-health-history", async (_event, kind: unknown, extensionId: unknown, limit: unknown) =>
    adapter!.getExtensionHealthHistory(
      validateExtensionHealthKind(kind),
      validateIdentifier(extensionId, "Extension id"),
      limit === null || limit === undefined ? 10 : validateExtensionHealthLimit(limit),
    ),
  );
  ipcMain.handle("ppx-client:preview-extension", async (_event, input: unknown) =>
    adapter!.previewExtension(validateExtensionPreviewRequest(input)),
  );
  ipcMain.handle("ppx-client:install-extension", async (_event, input: unknown) =>
    adapter!.installExtension(validateExtensionInstallRequest(input)),
  );
  ipcMain.handle("ppx-client:set-extension-agent-enabled", async (_event, input: unknown) =>
    adapter!.setExtensionAgentEnabled(validateExtensionEnablement(input)),
  );
  ipcMain.handle("ppx-client:remove-extension", async (_event, input: unknown) =>
    adapter!.removeExtension(validateExtensionRemoveRequest(input)),
  );
  ipcMain.handle("ppx-client:get-plugin-hook-status", async (_event, pluginId: unknown, expectedRevision: unknown) =>
    adapter!.getPluginHookStatus(
      validateIdentifier(pluginId, "Plugin id"),
      validateIdentifier(expectedRevision, "Expected Plugin revision"),
    ),
  );
  ipcMain.handle("ppx-client:set-plugin-hook-trust", async (_event, pluginId: unknown, expectedRevision: unknown, trusted: unknown) => {
    if (typeof trusted !== "boolean") throw new TypeError("Plugin Hook trust state must be boolean.");
    return adapter!.setPluginHookTrust(
      validateIdentifier(pluginId, "Plugin id"),
      validateIdentifier(expectedRevision, "Expected Plugin revision"),
      trusted,
    );
  });
  ipcMain.handle("ppx-client:list-plugin-marketplaces", async () => adapter!.listPluginMarketplaces());
  ipcMain.handle("ppx-client:list-plugin-marketplace-entries", async (_event, query: unknown) =>
    adapter!.listPluginMarketplaceEntries(
      query === null || query === undefined || query === "" ? undefined : validateSearchQuery(query),
    ),
  );
  ipcMain.handle("ppx-client:save-plugin-marketplace", async (_event, input: unknown) =>
    adapter!.savePluginMarketplace(validatePluginMarketplaceSaveRequest(input)),
  );
  ipcMain.handle("ppx-client:refresh-plugin-marketplace", async (_event, marketplaceId: unknown, expectedRevision: unknown) =>
    adapter!.refreshPluginMarketplace(
      validateIdentifier(marketplaceId, "Plugin Marketplace id"),
      validateIdentifier(expectedRevision, "Expected Plugin Marketplace revision"),
    ),
  );
  ipcMain.handle("ppx-client:remove-plugin-marketplace", async (_event, marketplaceId: unknown, expectedRevision: unknown) =>
    adapter!.removePluginMarketplace(
      validateIdentifier(marketplaceId, "Plugin Marketplace id"),
      validateIdentifier(expectedRevision, "Expected Plugin Marketplace revision"),
    ),
  );
  ipcMain.handle("ppx-client:create-mcp-server", async (_event, input: unknown) =>
    adapter!.createMcpServer(validateMcpMutationRequest(input)),
  );
  ipcMain.handle("ppx-client:update-mcp-server", async (_event, input: unknown) =>
    adapter!.updateMcpServer(validateMcpMutationRequest(input)),
  );
  ipcMain.handle("ppx-client:begin-mcp-oauth", async (_event, serverId: unknown) =>
    adapter!.beginMcpOAuth(validateIdentifier(serverId, "MCP server id")),
  );
  ipcMain.handle("ppx-client:get-mcp-oauth-status", async (_event, serverId: unknown) =>
    adapter!.getMcpOAuthStatus(validateIdentifier(serverId, "MCP server id")),
  );
  ipcMain.handle("ppx-client:sign-out-mcp-oauth", async (_event, serverId: unknown) =>
    adapter!.signOutMcpOAuth(validateIdentifier(serverId, "MCP server id")),
  );
  ipcMain.handle("ppx-client:test-mcp-server", async (_event, serverId: unknown) =>
    adapter!.testMcpServer(validateIdentifier(serverId, "MCP server id")),
  );
  ipcMain.handle("ppx-client:save-app-connection", async (_event, input: unknown) =>
    adapter!.saveAppConnection(validateAppConnectionSaveRequest(input)),
  );
  ipcMain.handle("ppx-client:test-app-connection", async (_event, connectionId: unknown) =>
    adapter!.testAppConnection(validateIdentifier(connectionId, "App Connection id")),
  );
  ipcMain.handle("ppx-client:set-app-connection-agent-enabled", async (_event, input: unknown) =>
    adapter!.setAppConnectionAgentEnabled(validateAppConnectionEnablementRequest(input)),
  );
  ipcMain.handle("ppx-client:remove-app-connection", async (_event, input: unknown) =>
    adapter!.removeAppConnection(validateAppConnectionRemoveRequest(input)),
  );

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  unsubscribeRunEvents?.();
  unsubscribeRunEvents = null;
  adapter?.dispose();
  adapter = null;
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  quitting = true;
});
