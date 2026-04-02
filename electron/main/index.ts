import { app, BrowserWindow, ipcMain } from "electron";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { ConnectionSettings, RuntimeCommand, SendMessageInput } from "../../app/src/types";
import { OpenPpxLocalAdapter } from "./openppx-local-adapter";

let mainWindow: BrowserWindow | null = null;
let unsubscribeRunEvents: (() => void) | null = null;
let adapter: OpenPpxLocalAdapter | null = null;
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function connectionSettingsPath(): string {
  return path.join(app.getPath("userData"), "connection-settings.json");
}

function readConnectionSettings(): ConnectionSettings | null {
  try {
    const filePath = connectionSettingsPath();
    if (!fs.existsSync(filePath)) {
      return null;
    }
    return JSON.parse(fs.readFileSync(filePath, "utf-8")) as ConnectionSettings;
  } catch {
    return null;
  }
}

function writeConnectionSettings(settings: ConnectionSettings): void {
  const filePath = connectionSettingsPath();
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(settings, null, 2), "utf-8");
}

function createWindow(): void {
  const preloadPath = process.env.VITE_DEV_SERVER_URL
    ? path.resolve(process.cwd(), "electron/preload/index.cjs")
    : path.join(__dirname, "../preload/index.cjs");

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1100,
    minHeight: 760,
    backgroundColor: "#f4efe5",
    title: "ppx-client",
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  adapter = new OpenPpxLocalAdapter(readConnectionSettings() ?? undefined);
  unsubscribeRunEvents = adapter.onRunEvent((event) => {
    mainWindow?.webContents.send("ppx-client:run-event", event);
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    void mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    void mainWindow.loadFile(path.join(__dirname, "../../index.html"));
  }
}

app.whenReady().then(() => {
  ipcMain.handle("ppx-client:bootstrap", async () => adapter!.bootstrap());
  ipcMain.handle("ppx-client:get-diagnostics", async () => adapter!.getDiagnostics());
  ipcMain.handle("ppx-client:save-connection-settings", async (_event, settings: ConnectionSettings) => {
    writeConnectionSettings(settings);
    adapter!.applyConnectionSettings(settings);
    return adapter!.getDiagnostics();
  });
  ipcMain.handle("ppx-client:runtime-command", async (_event, command: RuntimeCommand) =>
    adapter!.runRuntimeCommand(command),
  );
  ipcMain.handle("ppx-client:list-sessions", async (_event, agentId: string) => adapter!.listSessions(agentId));
  ipcMain.handle("ppx-client:create-session", async (_event, agentId: string) => adapter!.createSession(agentId));
  ipcMain.handle("ppx-client:load-session", async (_event, sessionId: string) => adapter!.loadSession(sessionId));
  ipcMain.handle("ppx-client:send-message", async (_event, input: SendMessageInput) => adapter!.sendMessage(input));

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
