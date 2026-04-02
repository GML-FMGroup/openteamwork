const { contextBridge, ipcRenderer } = require("electron");

const api = {
  bootstrap: () => ipcRenderer.invoke("ppx-client:bootstrap"),
  getDiagnostics: () => ipcRenderer.invoke("ppx-client:get-diagnostics"),
  saveConnectionSettings: (settings) => ipcRenderer.invoke("ppx-client:save-connection-settings", settings),
  runRuntimeCommand: (command) => ipcRenderer.invoke("ppx-client:runtime-command", command),
  listSessions: (agentId) => ipcRenderer.invoke("ppx-client:list-sessions", agentId),
  createSession: (agentId) => ipcRenderer.invoke("ppx-client:create-session", agentId),
  loadSession: (sessionId) => ipcRenderer.invoke("ppx-client:load-session", sessionId),
  sendMessage: (input) => ipcRenderer.invoke("ppx-client:send-message", input),
  onRunEvent: (listener) => {
    const wrapped = (_event, payload) => listener(payload);
    ipcRenderer.on("ppx-client:run-event", wrapped);
    return () => {
      ipcRenderer.removeListener("ppx-client:run-event", wrapped);
    };
  },
};

contextBridge.exposeInMainWorld("ppxClient", api);
