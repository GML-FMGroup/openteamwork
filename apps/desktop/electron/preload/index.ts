import { contextBridge, ipcRenderer } from "electron";
import type { ConnectionSettings, PpxClientApi, RunEvent, RuntimeCommand, SendMessageInput } from "../../app/src/types";

const api: PpxClientApi = {
  bootstrap: () => ipcRenderer.invoke("ppx-client:bootstrap"),
  getDiagnostics: () => ipcRenderer.invoke("ppx-client:get-diagnostics"),
  testConnectionSettings: (settings: ConnectionSettings) => ipcRenderer.invoke("ppx-client:test-connection-settings", settings),
  saveConnectionSettings: (settings: ConnectionSettings) => ipcRenderer.invoke("ppx-client:save-connection-settings", settings),
  runRuntimeCommand: (command: RuntimeCommand) => ipcRenderer.invoke("ppx-client:runtime-command", command),
  listSessions: (agentId: string) => ipcRenderer.invoke("ppx-client:list-sessions", agentId),
  createSession: (agentId: string) => ipcRenderer.invoke("ppx-client:create-session", agentId),
  loadSession: (sessionId: string) => ipcRenderer.invoke("ppx-client:load-session", sessionId),
  sendMessage: (input: SendMessageInput) => ipcRenderer.invoke("ppx-client:send-message", input),
  onRunEvent: (listener: (event: RunEvent) => void) => {
    const wrapped = (_event: unknown, payload: RunEvent) => listener(payload);
    ipcRenderer.on("ppx-client:run-event", wrapped);
    return () => {
      ipcRenderer.removeListener("ppx-client:run-event", wrapped);
    };
  },
};

contextBridge.exposeInMainWorld("ppxClient", api);
