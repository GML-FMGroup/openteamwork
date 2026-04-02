import { app, BrowserWindow, ipcMain } from "electron";
import path from "node:path";
import {
  bootstrap,
  createSession,
  loadSession,
  runRuntimeCommand,
  sendMessage,
  subscribe,
} from "../../app/src/lib/mock-client";
import type { RuntimeCommand, SendMessageInput } from "../../app/src/types";

let mainWindow: BrowserWindow | null = null;
let unsubscribeRunEvents: (() => void) | null = null;

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

  unsubscribeRunEvents = subscribe((event) => {
    mainWindow?.webContents.send("ppx-client:run-event", event);
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    void mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    void mainWindow.loadFile(path.join(__dirname, "../../index.html"));
  }
}

app.whenReady().then(() => {
  ipcMain.handle("ppx-client:bootstrap", async () => bootstrap());
  ipcMain.handle("ppx-client:runtime-command", async (_event, command: RuntimeCommand) =>
    runRuntimeCommand(command),
  );
  ipcMain.handle("ppx-client:create-session", async (_event, agentId: string) => createSession(agentId));
  ipcMain.handle("ppx-client:load-session", async (_event, sessionId: string) => loadSession(sessionId));
  ipcMain.handle("ppx-client:send-message", async (_event, input: SendMessageInput) => sendMessage(input));

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
  if (process.platform !== "darwin") {
    app.quit();
  }
});
