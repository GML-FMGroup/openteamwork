import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AgentProfile,
  BootstrapPayload,
  ChatMessage,
  ClientDiagnostics,
  ConnectionSettings,
  ExtensionSummary,
  RuntimeStatus,
  SessionSummary,
  ProjectedSlashCommand,
  SlashCommandResult,
} from "../types";
import { normalizeConnectionSettings } from "../lib/connection-profile";
import { useActiveRuns } from "./use-active-runs";
import { useConnectionRecovery } from "./use-connection-recovery";

function mergeMessages(current: ChatMessage[], incoming: ChatMessage): ChatMessage[] {
  return current.some((item) => item.id === incoming.id) ? current : [...current, incoming];
}

function updateMessage(
  current: ChatMessage[],
  messageId: string,
  updater: (message: ChatMessage) => ChatMessage,
): ChatMessage[] {
  return current.map((message) => (message.id === messageId ? updater(message) : message));
}

function compactSessionTitle(text: string): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= 64) {
    return normalized;
  }
  return `${normalized.slice(0, 61).trimEnd()}...`;
}

function isGenericSessionTitle(title: string): boolean {
  const normalized = title.trim();
  return (
    !normalized ||
    normalized === "New local session" ||
    normalized === "New chat" ||
    normalized === "新对话" ||
    normalized.startsWith("Session ")
  );
}

function mergeSessionSummary(existing: SessionSummary | undefined, incoming: SessionSummary): SessionSummary {
  if (!existing) {
    return incoming;
  }
  if (isGenericSessionTitle(incoming.title) && !isGenericSessionTitle(existing.title)) {
    return { ...incoming, title: existing.title };
  }
  return incoming;
}

function buildConnectionSettings(diagnostics: ClientDiagnostics | null): ConnectionSettings {
  return {
    targetType: diagnostics?.mode === "lan" ? "lan" : "local",
    targetId: diagnostics?.target.id ?? "local-default",
    targetName: diagnostics?.target.name ?? "This Mac",
    clientApiBaseUrl: diagnostics?.clientApiBaseUrl ?? "http://127.0.0.1:8765",
    accessToken: "",
  };
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function compactText(value: unknown, limit = 180): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 3).trimEnd()}...` : text;
}

/** Render structured command data only at the Desktop presentation boundary. */
function formatSlashCommandResult(outcome: SlashCommandResult): string {
  const result = record(outcome.result);
  if (outcome.targetActionId === "system.status") {
    const node = record(result.node);
    return `Node status: ${String(result.state ?? "unknown")} · ${String(node.displayName ?? node.id ?? "OpenPPX Node")}`;
  }
  if (outcome.targetActionId === "system.help") {
    const commands = (Array.isArray(result.items) ? result.items : []).flatMap((item) => {
      const action = record(item);
      return Array.isArray(action.slashCommands)
        ? action.slashCommands.map((command) => String(record(command).command ?? "")).filter(Boolean)
        : [];
    });
    return commands.length ? `Available commands: ${commands.join(", ")}` : "No commands are available.";
  }
  if (outcome.targetActionId === "model.list") {
    const items = Array.isArray(result.items) ? result.items : [];
    return items.length
      ? items.map((item) => {
          const model = record(item);
          return `${String(model.id ?? "model")} · ${String(model.model ?? model.provider ?? "unknown")} · ${String(model.credentialState ?? "unknown")}`;
        }).join("\n")
      : "No Model Profiles are configured.";
  }
  if (outcome.targetActionId === "session.history") {
    const items = Array.isArray(result.items) ? result.items : [];
    return items.length
      ? items.map((item) => {
          const message = record(item);
          return `${String(message.role ?? "message")}: ${compactText(message.text)}`;
        }).join("\n")
      : "This Session has no visible history yet.";
  }
  if (outcome.targetActionId === "task.list") {
    const items = Array.isArray(result.items) ? result.items : [];
    return items.length
      ? items.map((item) => {
          const task = record(item);
          return `${String(task.status ?? "unknown")} · ${String(task.title ?? task.taskId ?? "Task")}`;
        }).join("\n")
      : "No Tasks were found for this Session.";
  }
  if (outcome.targetActionId === "extension.list") {
    const items = Array.isArray(result.items) ? result.items : [];
    return items.length
      ? items.map((item) => {
          const extension = record(item);
          return `${String(extension.kind ?? "extension")} · ${String(extension.displayName ?? extension.id ?? "unknown")} · ${String(extension.status ?? "unknown")}`;
        }).join("\n")
      : "No matching Extensions are installed.";
  }
  if (outcome.targetActionId === "session.rewind") {
    return `Conversation rewound before invocation ${String(result.rewindBeforeInvocationId ?? "unknown")}. External side effects were not rolled back.`;
  }
  if (outcome.targetActionId === "run.stop") {
    return "Stop requested for the active Run.";
  }
  return JSON.stringify(result, null, 2);
}

/** Own Desktop bootstrap, connection, Agent/Session, message, and active-Run state. */
export function useDesktopWorkspace() {
  const [ready, setReady] = useState(false);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [diagnostics, setDiagnostics] = useState<ClientDiagnostics | null>(null);
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [composer, setComposer] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const [cancellingRunId, setCancellingRunId] = useState<string | null>(null);
  const [connectionForm, setConnectionForm] = useState<ConnectionSettings>(buildConnectionSettings(null));
  const [savingConnection, setSavingConnection] = useState(false);
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionFeedback, setConnectionFeedback] = useState<string | null>(null);
  const [extensions, setExtensions] = useState<ExtensionSummary[]>([]);
  const [extensionsLoading, setExtensionsLoading] = useState(false);
  const [extensionsError, setExtensionsError] = useState<string | null>(null);
  const [extensionMutationId, setExtensionMutationId] = useState<string | null>(null);
  const [slashCommands, setSlashCommands] = useState<ProjectedSlashCommand[]>([]);
  const [transcriptResetKey, setTranscriptResetKey] = useState(0);
  const switchRequestIdRef = useRef(0);
  const selectedAgentIdRef = useRef("");
  const selectedSessionIdRef = useRef("");
  const connectionProfileKeyRef = useRef("");
  const activeRuns = useActiveRuns();

  const selectAgentId = (agentId: string): void => {
    selectedAgentIdRef.current = agentId;
    setSelectedAgentId(agentId);
  };
  const selectSessionId = (sessionId: string): void => {
    selectedSessionIdRef.current = sessionId;
    setSelectedSessionId(sessionId);
  };
  const replaceMessages = (nextMessages: ChatMessage[]): void => {
    setMessages(nextMessages);
    setTranscriptResetKey((current) => current + 1);
  };

  useEffect(() => {
    if (!window.ppxClient) {
      setBootstrapError("Preload host API was not injected. Check Electron preload output and restart dev.");
      return;
    }
    let mounted = true;
    const off = window.ppxClient.onRunEvent((event) => {
      if (event.type === "message.created") {
        if (event.sessionId === selectedSessionIdRef.current) {
          setMessages((current) => mergeMessages(current, event.message));
        }
      } else if (event.type === "message.updated") {
        if (event.sessionId === selectedSessionIdRef.current) {
          setMessages((current) =>
            updateMessage(current, event.messageId, (message) => ({
              ...message,
              status: event.status ?? message.status,
              parts: event.replaceParts ?? [...message.parts, ...(event.appendParts ?? [])],
            })),
          );
        }
        if (event.status === "completed" || event.status === "failed" || event.status === "cancelled") {
          activeRuns.finish(event.sessionId);
          setCancellingRunId((current) => (current === event.runId ? null : current));
        }
      } else if (event.type === "session.updated") {
        if (event.session.agentId === selectedAgentIdRef.current) {
          setSessions((current) =>
            [
              mergeSessionSummary(
                current.find((item) => item.id === event.session.id),
                event.session,
              ),
              ...current.filter((item) => item.id !== event.session.id),
            ].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt)),
          );
        }
      } else if (event.type === "run.finished") {
        activeRuns.finish(event.sessionId);
        setCancellingRunId((current) => (current === event.runId ? null : current));
      }
    });

    window.ppxClient
      .bootstrap()
      .then(async (payload: BootstrapPayload) => {
        if (!mounted) {
          return;
        }
        let nextSessions = payload.sessions;
        let nextSelectedSessionId = payload.selectedSessionId;
        let nextMessages = payload.messages;
        if (payload.selectedAgentId && !nextSelectedSessionId) {
          const created = await window.ppxClient.createSession(payload.selectedAgentId);
          nextSessions = [created.session, ...nextSessions.filter((session) => session.id !== created.session.id)];
          nextSelectedSessionId = created.session.id;
          nextMessages = [];
        }
        if (!mounted) {
          return;
        }
        setRuntime(payload.runtime);
        setAgents(payload.agents);
        setSessions(nextSessions);
        replaceMessages(nextMessages);
        selectAgentId(payload.selectedAgentId);
        selectSessionId(nextSelectedSessionId);
        setReady(true);
        setBootstrapError(null);
        void window.ppxClient
          .listExtensions()
          .then((result) => {
            if (mounted) {
              setExtensions(result.extensions);
              setExtensionsError(null);
            }
          })
          .catch((error: unknown) => {
            if (mounted) {
              setExtensionsError(error instanceof Error ? error.message : String(error));
            }
          });
        void window.ppxClient
          .listSlashCommands()
          .then((result) => {
            if (mounted) {
              setSlashCommands(result.commands);
            }
          })
          .catch(() => {
            if (mounted) {
              setSlashCommands([]);
            }
          });
        void window.ppxClient
          .getDiagnostics()
          .then((nextDiagnostics) => {
            if (mounted) {
              setDiagnostics(nextDiagnostics);
            }
          })
          .catch(() => undefined);
      })
      .catch((error: unknown) => {
        if (mounted) {
          setBootstrapError(error instanceof Error ? error.message : String(error));
        }
      });

    return () => {
      mounted = false;
      off();
    };
  }, [activeRuns.finish]);

  useEffect(() => {
    if (!diagnostics) {
      return;
    }
    const profileKey = [
      diagnostics.mode,
      diagnostics.target.id,
      diagnostics.target.name,
      diagnostics.clientApiBaseUrl,
    ].join("\n");
    if (profileKey === connectionProfileKeyRef.current) {
      return;
    }
    connectionProfileKeyRef.current = profileKey;
    setConnectionForm(buildConnectionSettings(diagnostics));
  }, [diagnostics]);

  const applyConnectionBootstrap = (payload: BootstrapPayload): void => {
    setRuntime(payload.runtime);
    setAgents(payload.agents);
    setSessions(payload.sessions);
    replaceMessages(payload.messages);
    selectAgentId(payload.selectedAgentId);
    selectSessionId(payload.selectedSessionId);
    setBootstrapError(null);
  };

  useConnectionRecovery({
    active: ready && runtime?.state !== "stopped",
    check: async () => {
      let nextDiagnostics = await window.ppxClient.getDiagnostics();
      setDiagnostics(nextDiagnostics);
      if (nextDiagnostics.clientApiHealthy || nextDiagnostics.mode === "mock") {
        if (runtime?.state !== "healthy") {
          applyConnectionBootstrap(await window.ppxClient.bootstrap());
        }
        return true;
      }
      const payload = await window.ppxClient.bootstrap();
      if (payload.runtime.state !== "healthy") {
        return false;
      }
      applyConnectionBootstrap(payload);
      nextDiagnostics = await window.ppxClient.getDiagnostics();
      setDiagnostics(nextDiagnostics);
      return nextDiagnostics.clientApiHealthy;
    },
    onUnavailable: () => {
      setRuntime((current) =>
        current
          ? {
              ...current,
              state: "reconnecting",
              summary: "Reconnecting to OpenPPX Node...",
              detail: diagnostics?.clientApiLastError ?? "The connection will be retried automatically.",
            }
          : current,
      );
    },
    onRecovered: async () => {
      applyConnectionBootstrap(await window.ppxClient.bootstrap());
    },
  });

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedAgentId) ?? null,
    [agents, selectedAgentId],
  );
  const selectedSession = useMemo(
    () => sessions.find((session) => session.id === selectedSessionId) ?? null,
    [sessions, selectedSessionId],
  );
  const currentSessionRunning = selectedSessionId ? activeRuns.isSessionRunning(selectedSessionId) : false;
  const selectedAgentBusy = selectedAgentId
    ? activeRuns.isSessionRunning(selectedSessionId) || activeRuns.isAgentRunning(selectedAgentId, sessions)
    : false;
  const activeRunId = activeRuns.runIdForSession(selectedSessionId);

  async function switchAgent(agentId: string): Promise<void> {
    if (agentId === selectedAgentIdRef.current) {
      return;
    }
    const requestId = ++switchRequestIdRef.current;
    setSendError(null);
    selectAgentId(agentId);
    selectSessionId("");
    setSessions([]);
    replaceMessages([]);
    const listed = await window.ppxClient.listSessions(agentId);
    if (requestId !== switchRequestIdRef.current) {
      return;
    }
    setSessions(listed.sessions);
    if (listed.sessions[0]) {
      const nextSessionId = listed.sessions[0].id;
      selectSessionId(nextSessionId);
      const loaded = await window.ppxClient.loadSession(nextSessionId);
      if (requestId === switchRequestIdRef.current) {
        replaceMessages(loaded.messages);
      }
      return;
    }
    const created = await window.ppxClient.createSession(agentId);
    if (requestId === switchRequestIdRef.current) {
      setSessions([created.session]);
      selectSessionId(created.session.id);
      replaceMessages([]);
    }
  }

  async function switchSession(session: SessionSummary): Promise<void> {
    const requestId = ++switchRequestIdRef.current;
    setSendError(null);
    selectAgentId(session.agentId);
    selectSessionId(session.id);
    replaceMessages([]);
    const loaded = await window.ppxClient.loadSession(session.id);
    if (requestId === switchRequestIdRef.current) {
      replaceMessages(loaded.messages);
    }
  }

  async function runRuntimeAction(): Promise<void> {
    if (!runtime) {
      return;
    }
    const command = runtime.state === "stopped" ? "start" : "restart";
    const nextRuntime = await window.ppxClient.runRuntimeCommand(command);
    setRuntime(nextRuntime);
    setDiagnostics(await window.ppxClient.getDiagnostics());
    if (nextRuntime.state === "healthy") {
      applyConnectionBootstrap(await window.ppxClient.bootstrap());
    }
  }

  async function stopRuntime(): Promise<void> {
    setRuntime(await window.ppxClient.runRuntimeCommand("stop"));
  }

  async function refreshDiagnostics(): Promise<void> {
    setDiagnostics(await window.ppxClient.getDiagnostics());
  }

  async function refreshExtensions(): Promise<void> {
    setExtensionsLoading(true);
    setExtensionsError(null);
    try {
      const result = await window.ppxClient.listExtensions();
      setExtensions(result.extensions);
    } catch (error) {
      setExtensionsError(error instanceof Error ? error.message : String(error));
    } finally {
      setExtensionsLoading(false);
    }
  }

  async function setExtensionEnabled(extension: ExtensionSummary, enabled: boolean): Promise<void> {
    if (!selectedAgentId || extension.kind === "app") {
      return;
    }
    setExtensionMutationId(`${extension.kind}:${extension.id}`);
    setExtensionsError(null);
    try {
      await window.ppxClient.setExtensionAgentEnabled({
        kind: extension.kind,
        extensionId: extension.id,
        agentId: selectedAgentId,
        expectedRevision: extension.revision,
        enabled,
      });
      await refreshExtensions();
    } catch (error) {
      setExtensionsError(error instanceof Error ? error.message : String(error));
    } finally {
      setExtensionMutationId(null);
    }
  }

  async function saveConnection(): Promise<void> {
    setSavingConnection(true);
    setConnectionFeedback(null);
    try {
      const nextSettings = normalizeConnectionSettings(connectionForm);
      const nextDiagnostics = await window.ppxClient.saveConnectionSettings(nextSettings);
      setDiagnostics(nextDiagnostics);
      if (nextSettings.targetType === "local") {
        await window.ppxClient.runRuntimeCommand("restart");
      }
      try {
        const payload = await window.ppxClient.bootstrap();
        applyConnectionBootstrap(payload);
      } catch {
        setAgents([]);
        setSessions([]);
        replaceMessages([]);
        selectAgentId("");
        selectSessionId("");
      }
      setConnectionFeedback(`Connected to ${nextDiagnostics.nodeName ?? nextDiagnostics.target.name}.`);
    } catch (error) {
      setConnectionFeedback(error instanceof Error ? error.message : String(error));
    } finally {
      setSavingConnection(false);
    }
  }

  async function testConnection(): Promise<void> {
    setTestingConnection(true);
    setConnectionFeedback(null);
    try {
      const nextDiagnostics = await window.ppxClient.testConnectionSettings(normalizeConnectionSettings(connectionForm));
      setConnectionFeedback(
        `Connection successful: ${nextDiagnostics.nodeName ?? nextDiagnostics.target.name} · ${nextDiagnostics.clientApiProductVersion ?? "unknown"}`,
      );
    } catch (error) {
      setConnectionFeedback(error instanceof Error ? error.message : String(error));
    } finally {
      setTestingConnection(false);
    }
  }

  async function createSession(): Promise<void> {
    if (!selectedAgentId) {
      return;
    }
    setSendError(null);
    const created = await window.ppxClient.createSession(selectedAgentId);
    setSessions((current) => [created.session, ...current.filter((item) => item.id !== created.session.id)]);
    selectSessionId(created.session.id);
    replaceMessages([]);
  }

  async function ensureActiveSession(agentId: string, preferredSessionId: string): Promise<SessionSummary> {
    const existing = sessions.find((session) => session.id === preferredSessionId && session.agentId === agentId);
    if (existing) {
      return existing;
    }
    const listed = await window.ppxClient.listSessions(agentId);
    if (listed.sessions[0]) {
      const firstSession = listed.sessions[0];
      setSessions(listed.sessions);
      selectSessionId(firstSession.id);
      if (firstSession.id !== preferredSessionId) {
        replaceMessages((await window.ppxClient.loadSession(firstSession.id)).messages);
      }
      return firstSession;
    }
    const created = await window.ppxClient.createSession(agentId);
    setSessions((current) => [created.session, ...current.filter((item) => item.id !== created.session.id)]);
    selectSessionId(created.session.id);
    replaceMessages([]);
    return created.session;
  }

  function applyFirstUserTitle(sessionId: string, text: string, timestamp: string): void {
    const title = compactSessionTitle(text);
    if (!title) {
      return;
    }
    setSessions((current) =>
      current.map((session) =>
        session.id === sessionId && isGenericSessionTitle(session.title)
          ? { ...session, title, updatedAt: timestamp }
          : session,
      ),
    );
  }

  function appendCommandNotice(outcome: SlashCommandResult): void {
    if (!selectedSessionIdRef.current) {
      return;
    }
    const text = formatSlashCommandResult(outcome);
    if (!text) {
      return;
    }
    setMessages((current) => [
      ...current,
      {
        id: `local-command-${crypto.randomUUID()}`,
        sessionId: selectedSessionIdRef.current,
        role: "system",
        status: "completed",
        createdAt: new Date().toISOString(),
        parts: [{ type: "markdown", text }],
      },
    ]);
  }

  async function executeSlashCommand(rawCommand: string): Promise<void> {
    setSendError(null);
    setComposer("");
    try {
      const outcome = await window.ppxClient.invokeSlashCommand({
        rawCommand,
        agentId: selectedAgentIdRef.current || null,
        sessionId: selectedSessionIdRef.current || null,
        runId: activeRunId ?? null,
      });
      if (outcome.targetActionId === "session.new") {
        const payload = record(record(outcome.result).session);
        const session: SessionSummary = {
          id: String(payload.id ?? ""),
          agentId: String(payload.agentId ?? selectedAgentIdRef.current),
          title: String(payload.title ?? "New chat"),
          updatedAt: String(payload.updatedAt ?? new Date().toISOString()),
          lastMessagePreview: String(payload.lastMessagePreview ?? ""),
        };
        if (session.id) {
          setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)]);
          selectSessionId(session.id);
          replaceMessages([]);
        }
        return;
      }
      if (outcome.targetActionId === "session.rewind" && selectedSessionIdRef.current) {
        replaceMessages((await window.ppxClient.loadSession(selectedSessionIdRef.current)).messages);
      }
      if (outcome.targetActionId === "run.stop" && activeRunId) {
        setCancellingRunId(activeRunId);
      }
      appendCommandNotice(outcome);
    } catch (error) {
      setSendError(error instanceof Error ? error.message : String(error));
    }
  }

  async function sendMessage(rawText?: string): Promise<void> {
    const text = (rawText ?? composer).trim();
    if (!text || !selectedAgentId) {
      return;
    }
    if (text.startsWith("/")) {
      await executeSlashCommand(text);
      return;
    }
    setSendError(null);
    let session: SessionSummary;
    try {
      session = await ensureActiveSession(selectedAgentId, selectedSessionId);
    } catch (error) {
      setSendError(error instanceof Error ? error.message : String(error));
      return;
    }
    const sessionId = session.id;
    activeRuns.begin(sessionId);
    setComposer("");
    const optimisticMessage: ChatMessage = {
      id: `local-user-${crypto.randomUUID()}`,
      sessionId,
      role: "user",
      status: "completed",
      createdAt: new Date().toISOString(),
      parts: [{ type: "markdown", text }],
    };
    applyFirstUserTitle(sessionId, text, optimisticMessage.createdAt);
    setMessages((current) => [...current, optimisticMessage]);
    try {
      const result = await window.ppxClient.sendMessage({ agentId: selectedAgentId, sessionId, text });
      activeRuns.attachRunId(sessionId, result.runId);
    } catch (error) {
      console.error("Failed to send message", error);
      activeRuns.finish(sessionId);
      setSendError(error instanceof Error ? error.message : String(error));
    }
  }

  async function cancelCurrentRun(): Promise<void> {
    if (!activeRunId || cancellingRunId === activeRunId) {
      return;
    }
    setSendError(null);
    setCancellingRunId(activeRunId);
    try {
      await window.ppxClient.cancelRun(activeRunId);
    } catch (error) {
      setCancellingRunId(null);
      setSendError(error instanceof Error ? error.message : String(error));
    }
  }

  return {
    ready,
    bootstrapError,
    runtime,
    diagnostics,
    agents,
    sessions,
    messages,
    selectedAgentId,
    selectedSessionId,
    selectedAgent,
    selectedSession,
    composer,
    setComposer,
    sendError,
    activeSessionIds: activeRuns.sessionIds,
    currentSessionRunning,
    selectedAgentBusy,
    activeRunId,
    cancellingCurrentRun: Boolean(activeRunId && cancellingRunId === activeRunId),
    connectionForm,
    setConnectionForm,
    savingConnection,
    testingConnection,
    connectionFeedback,
    extensions,
    extensionsLoading,
    extensionsError,
    extensionMutationId,
    slashCommands,
    transcriptResetKey,
    switchAgent,
    switchSession,
    runRuntimeAction,
    stopRuntime,
    refreshDiagnostics,
    refreshExtensions,
    setExtensionEnabled,
    saveConnection,
    testConnection,
    createSession,
    sendMessage,
    cancelCurrentRun,
  };
}
