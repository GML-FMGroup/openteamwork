import { app, BrowserWindow, ipcMain } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { ClientDiagnostics } from "../../app/src/types";
import {
  validateConnectionSettings,
  validateExtensionEnablement,
  validateExtensionKind,
  validateIdentifier,
  validateRuntimeCommand,
  validateSendMessageInput,
} from "./ipc-validation";
import { OpenPpxLocalAdapter } from "./openppx-local-adapter";
import {
  readSecureConnectionSettings,
  resolveCandidateConnectionSettings,
  writeSecureConnectionSettings,
} from "./secure-connection-store";

let mainWindow: BrowserWindow | null = null;
let unsubscribeRunEvents: (() => void) | null = null;
let adapter: OpenPpxLocalAdapter | null = null;
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
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    void mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    void mainWindow.loadFile(path.join(__dirname, "../../dist/index.html"));
  }
}

app.whenReady().then(() => {
  ipcMain.handle("ppx-client:bootstrap", async () => adapter!.bootstrap());
  ipcMain.handle("ppx-client:get-diagnostics", async () => withDesktopVersion(await adapter!.getDiagnostics()));
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
  ipcMain.handle("ppx-client:runtime-command", async (_event, command: unknown) =>
    adapter!.runRuntimeCommand(validateRuntimeCommand(command)),
  );
  ipcMain.handle("ppx-client:list-sessions", async (_event, agentId: unknown) =>
    adapter!.listSessions(validateIdentifier(agentId, "Agent id")),
  );
  ipcMain.handle("ppx-client:create-session", async (_event, agentId: unknown) =>
    adapter!.createSession(validateIdentifier(agentId, "Agent id")),
  );
  ipcMain.handle("ppx-client:load-session", async (_event, sessionId: unknown) =>
    adapter!.loadSession(validateIdentifier(sessionId, "Session id")),
  );
  ipcMain.handle("ppx-client:send-message", async (_event, input: unknown) =>
    adapter!.sendMessage(validateSendMessageInput(input)),
  );
  ipcMain.handle("ppx-client:cancel-run", async (_event, runId: unknown) =>
    adapter!.cancelRun(validateIdentifier(runId, "Run id")),
  );
  ipcMain.handle("ppx-client:list-extensions", async () => adapter!.listExtensions());
  ipcMain.handle("ppx-client:get-extension", async (_event, kind: unknown, extensionId: unknown) =>
    adapter!.getExtension(
      validateExtensionKind(kind),
      validateIdentifier(extensionId, "Extension id"),
    ),
  );
  ipcMain.handle("ppx-client:set-extension-agent-enabled", async (_event, input: unknown) =>
    adapter!.setExtensionAgentEnabled(validateExtensionEnablement(input)),
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
