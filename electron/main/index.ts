import { app, BrowserWindow, ipcMain } from "electron";
import path from "node:path";
import type { RuntimeCommand, SendMessageInput } from "../../app/src/types";
import { OpenPpxLocalAdapter } from "./openppx-local-adapter";

let mainWindow: BrowserWindow | null = null;
let unsubscribeRunEvents: (() => void) | null = null;
let adapter: OpenPpxLocalAdapter | null = null;

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1100,
    minHeight: 760,
    backgroundColor: "#f4efe5",
    title: "ppx-client",
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  adapter = new OpenPpxLocalAdapter();
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
  ipcMain.handle("ppx-client:runtime-command", async (_event, command: RuntimeCommand) =>
    adapter!.runRuntimeCommand(command),
  );
  ipcMain.handle("ppx-client:list-sessions", async (_event, agentId: string) => ({
    sessions: await adapter!.listSessions(agentId),
  }));
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
