import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type {
  AgentProfile,
  AgentCreateRequest,
  ArtifactSummary,
  BootstrapPayload,
  ChatMessage,
  ClientDiagnostics,
  ConnectionSettings,
  ConnectionTestState,
  ExtensionSummary,
  GoalDetail,
  GoalMutationOperation,
  GoalTransitionOperation,
  ModelProfileSummary,
  ModelProfileResourceResult,
  ModelProfileCreateInput,
  ModelProfileUpdateInput,
  ModelCatalogResult,
  ProviderAuthStatus,
  OperationsAuditItem,
  OperationsOverviewResult,
  RuntimeStatus,
  SessionSummary,
  ProjectedSlashCommand,
  SetupApplyRequest,
  SetupForm,
  SetupReadinessResult,
  SetupStatusResult,
  SlashCommandResult,
  UserProfile,
  UserLoginRequest,
} from "../types";
import {
  attachmentPreflightError,
  MAX_MESSAGE_ATTACHMENT_BYTES,
  MAX_MESSAGE_ATTACHMENTS,
} from "../attachment-policy";
import { normalizeConnectionSettings, normalizeLoginConnectionSettings } from "../lib/connection-profile";
import { connectionFailureMessage } from "../lib/connection-feedback";
import { sortSessionsByRecency } from "../lib/session-order";
import { isWorkspaceConfigurationComplete, setupReadinessFromStatus } from "../lib/setup-status";
import { LOCAL_USER_ID } from "../types";
import { useActiveRuns } from "./use-active-runs";
import { useConnectionRecovery } from "./use-connection-recovery";
import { productProfile } from "../../../product";

export const ARCHIVED_SESSION_GUIDANCE = "Restore this session to continue.";

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
    clientApiBaseUrl: diagnostics?.clientApiBaseUrl ?? `http://127.0.0.1:${productProfile.defaultClientApiPort}`,
    accessToken: "",
  };
}

export interface PendingAttachment {
  id: string;
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  dataBase64: string;
  status: "ready" | "uploading" | "failed";
}

async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 32_768;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

function onboardingBootstrap(diagnostics: ClientDiagnostics): BootstrapPayload {
  return {
    runtime: {
      target: diagnostics.target,
      state: diagnostics.clientApiHealthy ? "needs_configuration" : "error",
      summary: diagnostics.clientApiHealthy
        ? `${productProfile.displayName} Node needs configuration.`
        : `${productProfile.displayName} Node is unavailable.`,
      detail: diagnostics.clientApiHealthy
        ? "Repair or complete the Node configuration to load the workspace."
        : diagnostics.clientApiLastError,
    },
    agents: [],
    sessions: [],
    messages: [],
    selectedAgentId: "",
    selectedSessionId: "",
  };
}

function initialSetupForm(ownerPrincipalId = LOCAL_USER_ID): SetupForm {
  return {
    nodeId: "local-node",
    nodeName: "This Mac",
    agentId: productProfile.defaultAgentId,
    agentName: productProfile.defaultAgentDisplayName,
    workspace: "",
    ownerPrincipalId,
    privilegeLevel: productProfile.defaultAgentPrivilegeLevel,
    profileId: "primary",
    provider: "google",
    model: "",
    executionLocation: "remote",
    credentialName: "primary-model-api-key",
    apiKey: "",
    hello: `Hello ${productProfile.displayName}`,
  };
}

function setupFormFromStatus(
  status: SetupStatusResult,
  diagnostics: ClientDiagnostics,
  ownerPrincipalId = LOCAL_USER_ID,
): SetupForm {
  const current = initialSetupForm(ownerPrincipalId);
  const node = record(status.current.node);
  const nodeMetadata = record(node.metadata);
  const nodeSpec = record(node.spec);
  const agent = record(status.current.agent);
  const agentMetadata = record(agent.metadata);
  const agentSpec = record(agent.spec);
  const modelPolicy = record(agentSpec.modelPolicy);
  const profile = record(status.current.profile);
  const profileMetadata = record(profile.metadata);
  const profileSpec = record(profile.spec);
  const credential = record(profileSpec.credential);
  const providerId = String(profileSpec.provider ?? current.provider);
  const provider = status.providers.find((item) => item.id === providerId) ?? status.providers[0];
  return {
    ...current,
    nodeId: String(nodeMetadata.name ?? current.nodeId),
    nodeName: String(nodeSpec.displayName ?? diagnostics.nodeName ?? diagnostics.target.name),
    agentId: String(agentMetadata.name ?? current.agentId),
    agentName: String(agentSpec.displayName ?? current.agentName),
    workspace: String(agentSpec.workspace ?? status.recommendedWorkspace),
    ownerPrincipalId: String(agentSpec.ownerPrincipalId ?? current.ownerPrincipalId),
    privilegeLevel: (String(agentSpec.privilegeLevel ?? current.privilegeLevel) as SetupForm["privilegeLevel"]),
    profileId: String(profileMetadata.name ?? modelPolicy.defaultProfile ?? current.profileId),
    provider: provider?.id ?? current.provider,
    model: String(profileSpec.model ?? provider?.defaultModel ?? current.model),
    executionLocation: (String(profileSpec.executionLocation ?? current.executionLocation) as SetupForm["executionLocation"]),
    credentialName: String(credential.name ?? current.credentialName),
  };
}

function buildSetupRequest(
  form: SetupForm,
  status: SetupStatusResult,
): SetupApplyRequest {
  const provider = status.providers.find((item) => item.id === form.provider);
  if (!provider) {
    throw new Error("Select an available model provider.");
  }
  const credential = { store: "system" as const, name: form.credentialName };
  const usesApiKey = provider.credentialMode === "api_key";
  const currentNode = record(status.current.node);
  const currentNodeMetadata = record(currentNode.metadata);
  const currentNodeSpec = record(currentNode.spec);
  const currentClientApi = record(currentNodeSpec.clientApi);
  const currentEnabledAgents = Array.isArray(currentNodeSpec.enabledAgents)
    ? currentNodeSpec.enabledAgents.filter((item): item is string => typeof item === "string")
    : [];
  const enabledAgents = Array.from(new Set([...currentEnabledAgents, form.agentId]));
  const currentPort = Number(currentClientApi.port);
  const clientApi = {
    listenHost: typeof currentClientApi.listenHost === "string" && currentClientApi.listenHost.trim()
      ? currentClientApi.listenHost
      : "127.0.0.1",
    port: Number.isSafeInteger(currentPort) && currentPort > 0
      ? currentPort
      : productProfile.defaultClientApiPort,
    authentication: currentClientApi.authentication === "disabled" ? "disabled" as const : "required" as const,
  };
  return {
    node: {
      apiVersion: "openppx.io/v1alpha1",
      kind: "NodeConfig",
      metadata: { ...currentNodeMetadata, name: String(currentNodeMetadata.name ?? form.nodeId) },
      spec: {
        ...currentNodeSpec,
        displayName: form.nodeName,
        enabledAgents,
        clientApi,
      },
    },
    agent: {
      apiVersion: "openppx.io/v1alpha1",
      kind: "AgentConfig",
      metadata: { name: form.agentId },
      spec: {
        displayName: form.agentName,
        workspace: form.workspace,
        ownerPrincipalId: form.ownerPrincipalId,
        privilegeLevel: form.privilegeLevel,
        modelPolicy: { defaultProfile: form.profileId },
      },
    },
    profile: {
      apiVersion: "openppx.io/v1alpha1",
      kind: "ModelProfile",
      metadata: { name: form.profileId },
      spec: {
        displayName: "Primary",
        provider: form.provider,
        model: form.model,
        ...(usesApiKey ? { credential } : {}),
        executionLocation: form.executionLocation,
        capabilities: ["text", "tool_calling"],
      },
    },
    secret: usesApiKey && form.apiKey ? { ref: credential, value: form.apiKey } : null,
    expectedRevisions: status.revisions,
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

function clientErrorMessage(error: unknown): string {
  let message = error instanceof Error ? error.message : String(error);
  message = message.replace(/^Error invoking remote method '[^']+':\s*/, "").trim();
  while (/^(?:Error|TypeError|ClientApiRequestError):\s*/.test(message)) {
    message = message.replace(/^(?:Error|TypeError|ClientApiRequestError):\s*/, "").trim();
  }
  return message;
}

function hasRootAccess(profile: UserProfile): boolean {
  return profile.accountKind === "local" || profile.privilegeLevel === "root";
}

/** Render structured command data only at the Desktop presentation boundary. */
function formatSlashCommandResult(outcome: SlashCommandResult): string {
  const result = record(outcome.result);
  if (outcome.targetActionId === "system.status") {
    const node = record(result.node);
    return `Node status: ${String(result.state ?? "unknown")} · ${String(node.displayName ?? node.id ?? `${productProfile.displayName} Node`)}`;
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
  if (outcome.targetActionId === "task.command") {
    const task = record(result.task);
    const items = Array.isArray(result.items) ? result.items : [];
    if (task.taskId) {
      return `${String(task.status ?? "unknown")} · ${String(task.title ?? task.taskId)}`;
    }
    return items.length
      ? items.map((item) => {
          const entry = record(item);
          return `${String(entry.status ?? "unknown")} · ${String(entry.title ?? entry.taskId ?? "Task")}`;
        }).join("\n")
      : "No matching Task information was found.";
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
  if (outcome.targetActionId === "goal.command") {
    const current = record(result.current);
    const goal = Object.keys(current).length ? current : result;
    if (goal.goalId) {
      return `${String(goal.status ?? "active")} · ${String(goal.objective ?? "Goal")}`;
    }
    const items = Array.isArray(result.items) ? result.items : [];
    return items.length
      ? items.map((item) => {
          const history = record(item);
          return `${String(history.status ?? "unknown")} · ${String(history.objective ?? history.goalId ?? "Goal")}`;
        }).join("\n")
      : "This Session has no active Goal.";
  }
  if (outcome.targetActionId === "skill.draft.command") {
    const operation = String(result.operation ?? "");
    if (operation === "published") {
      const skill = record(result.skill);
      return `Skill created: ${String(skill.displayName ?? skill.skillId ?? "Skill")}\n\nEnabled for the current Agent · user confirmed`;
    }
    if (operation === "cancelled") {
      return "Skill draft cancelled. No Skill was created.";
    }
    const draft = record(result.draft);
    const displayName = String(draft.displayName ?? draft.skillId ?? "Untitled Skill");
    const status = String(draft.status ?? "needs_input");
    const lines = [
      `## Skill draft: ${displayName}`,
      "",
      status === "ready_for_review"
        ? "**Not execution-verified.** Review the workflow before confirming it."
        : "**More information is required before this Skill can be created.**",
      "",
      String(draft.description ?? ""),
    ];
    const appendList = (title: string, values: unknown): void => {
      const items = Array.isArray(values) ? values.map((item) => String(item)).filter(Boolean) : [];
      if (items.length) {
        lines.push("", `### ${title}`, "", ...items.map((item) => `- ${item}`));
      }
    };
    appendList("When to use", draft.triggers);
    appendList("Inputs", draft.inputs);
    appendList("Outputs", draft.outputs);
    const steps = Array.isArray(draft.steps) ? draft.steps : [];
    if (steps.length) {
      lines.push("", "### Steps", "");
      steps.forEach((step, index) => lines.push(`${index + 1}. ${String(record(step).text ?? "")}`));
    }
    appendList("Boundaries", draft.limitations);
    appendList("Questions", draft.unresolvedQuestions);
    const sourceCount = Number(draft.sourceMessageCount ?? 0);
    const redactionCount = Number(draft.redactionCount ?? 0);
    lines.push("", `Source: ${sourceCount} visible ${sourceCount === 1 ? "message" : "messages"}.`);
    if (redactionCount > 0) {
      lines.push(`${redactionCount} sensitive ${redactionCount === 1 ? "value was" : "values were"} redacted.`);
    }
    lines.push(
      "",
      status === "ready_for_review"
        ? "Use `/make-skill approve`, `/make-skill revise <changes>`, or `/make-skill cancel`."
        : "Use `/make-skill revise <missing details>` or `/make-skill cancel`.",
    );
    return lines.join("\n");
  }
  return JSON.stringify(result, null, 2);
}

/** Own Desktop bootstrap, connection, Agent/Session, message, and active-Run state. */
export function useDesktopWorkspace() {
  const [ready, setReady] = useState(false);
  const [authenticationRequired, setAuthenticationRequired] = useState(false);
  const [authenticating, setAuthenticating] = useState(false);
  const [authenticationError, setAuthenticationError] = useState<string | null>(null);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [diagnostics, setDiagnostics] = useState<ClientDiagnostics | null>(null);
  const [userProfile, setUserProfile] = useState<UserProfile>({
    id: LOCAL_USER_ID,
    displayName: "Local user",
    accountKind: "local",
  });
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [agentCreating, setAgentCreating] = useState(false);
  const [agentCreateError, setAgentCreateError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionArtifacts, setSessionArtifacts] = useState<ArtifactSummary[]>([]);
  const [currentGoal, setCurrentGoal] = useState<GoalDetail | null>(null);
  const [goalMutation, setGoalMutation] = useState<GoalMutationOperation | null>(null);
  const [goalMutationError, setGoalMutationError] = useState<string | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [composer, setComposer] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [sendError, setSendError] = useState<string | null>(null);
  const [cancellingRunId, setCancellingRunId] = useState<string | null>(null);
  const [connectionForm, setConnectionForm] = useState<ConnectionSettings>(buildConnectionSettings(null));
  const [savingConnection, setSavingConnection] = useState(false);
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionTestState, setConnectionTestState] = useState<ConnectionTestState>("untested");
  const [connectionFeedback, setConnectionFeedback] = useState<string | null>(null);
  const [extensions, setExtensions] = useState<ExtensionSummary[]>([]);
  const [extensionsLoading, setExtensionsLoading] = useState(false);
  const [extensionsError, setExtensionsError] = useState<string | null>(null);
  const [extensionMutationId, setExtensionMutationId] = useState<string | null>(null);
  const [slashCommands, setSlashCommands] = useState<ProjectedSlashCommand[]>([]);
  const [setupReadiness, setSetupReadiness] = useState<SetupReadinessResult | null>(null);
  const [setupStatus, setSetupStatus] = useState<SetupStatusResult | null>(null);
  const [setupForm, setSetupForm] = useState<SetupForm>(initialSetupForm);
  const [setupSubmitting, setSetupSubmitting] = useState(false);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [providerModels, setProviderModels] = useState<ModelCatalogResult | null>(null);
  const [providerAuth, setProviderAuth] = useState<ProviderAuthStatus | null>(null);
  const [providerAccessLoading, setProviderAccessLoading] = useState(false);
  const [providerAccessError, setProviderAccessError] = useState<string | null>(null);
  const [modelProfiles, setModelProfiles] = useState<ModelProfileSummary[]>([]);
  const [operationsOverview, setOperationsOverview] = useState<OperationsOverviewResult | null>(null);
  const [operationsAudit, setOperationsAudit] = useState<OperationsAuditItem[]>([]);
  const [operationsLoading, setOperationsLoading] = useState(false);
  const [operationsError, setOperationsError] = useState<string | null>(null);
  const [transcriptResetKey, setTranscriptResetKey] = useState(0);
  const switchRequestIdRef = useRef(0);
  const selectedAgentIdRef = useRef("");
  const selectedSessionIdRef = useRef("");
  const connectionProfileKeyRef = useRef("");
  const connectionCandidateVersionRef = useRef(0);
  const setupFormInitializedRef = useRef(false);
  const activeRuns = useActiveRuns();

  const updateConnectionForm: Dispatch<SetStateAction<ConnectionSettings>> = (next) => {
    connectionCandidateVersionRef.current += 1;
    setConnectionTestState("untested");
    setConnectionFeedback(null);
    setConnectionForm(next);
  };

  async function refreshCurrentGoal(sessionId = selectedSessionIdRef.current): Promise<void> {
    if (!sessionId) {
      setCurrentGoal(null);
      return;
    }
    try {
      const result = await window.ppxClient.getCurrentGoal(sessionId);
      if (sessionId === selectedSessionIdRef.current) {
        setCurrentGoal(result.goal);
      }
    } catch {
      if (sessionId === selectedSessionIdRef.current) {
        setCurrentGoal(null);
      }
    }
  }

  async function refreshSessionArtifacts(
    agentId = selectedAgentIdRef.current,
    sessionId = selectedSessionIdRef.current,
  ): Promise<void> {
    if (!agentId || !sessionId) {
      setSessionArtifacts([]);
      return;
    }
    try {
      const { artifacts } = await window.ppxClient.listArtifacts(agentId, sessionId);
      if (agentId === selectedAgentIdRef.current && sessionId === selectedSessionIdRef.current) {
        setSessionArtifacts(artifacts);
      }
    } catch {
      if (agentId === selectedAgentIdRef.current && sessionId === selectedSessionIdRef.current) {
        setSessionArtifacts([]);
      }
    }
  }

  /** Persist a new objective while preserving the current Goal identity and policy. */
  async function updateCurrentGoal(objective: string): Promise<boolean> {
    const goal = currentGoal;
    const normalized = objective.trim();
    if (!goal || goalMutation || !normalized) return false;
    if (normalized === goal.objective) {
      setGoalMutationError(null);
      return true;
    }
    const sessionId = selectedSessionIdRef.current;
    setGoalMutation("update");
    setGoalMutationError(null);
    try {
      const updated = await window.ppxClient.updateGoal({
        goalId: goal.goalId,
        expectedRevision: goal.revision,
        objective: normalized,
      });
      if (sessionId === selectedSessionIdRef.current) setCurrentGoal(updated);
      return true;
    } catch (error) {
      setGoalMutationError(clientErrorMessage(error));
      await refreshCurrentGoal(sessionId);
      return false;
    } finally {
      setGoalMutation(null);
    }
  }

  /** Transition the current Goal and immediately reconcile the shared Desktop fact. */
  async function transitionCurrentGoal(operation: GoalTransitionOperation): Promise<boolean> {
    const goal = currentGoal;
    if (!goal || goalMutation) return false;
    const sessionId = selectedSessionIdRef.current;
    setGoalMutation(operation);
    setGoalMutationError(null);
    try {
      const updated = await window.ppxClient.transitionGoal(operation, goal.goalId, goal.revision);
      if (sessionId === selectedSessionIdRef.current) {
        setCurrentGoal(
          updated.status === "completed" || updated.status === "cancelled" || updated.status === "failed"
            ? null
            : updated,
        );
      }
      if (operation === "resume" && sessionId === selectedSessionIdRef.current) {
        await sendMessage(`Resume the active Goal and continue working toward: ${updated.objective}`);
      }
      return true;
    } catch (error) {
      setGoalMutationError(clientErrorMessage(error));
      await refreshCurrentGoal(sessionId);
      return false;
    } finally {
      setGoalMutation(null);
    }
  }

  /** Retry the supervisor-selected blocked step and resume execution in one user action. */
  async function retryCurrentGoal(): Promise<boolean> {
    const goal = currentGoal;
    if (!goal || goal.status !== "blocked" || goalMutation) return false;
    const sessionId = selectedSessionIdRef.current;
    const stepId = typeof goal.flow?.waitReason.stepId === "string" ? goal.flow.waitReason.stepId : null;
    setGoalMutation("retry");
    setGoalMutationError(null);
    try {
      const updated = await window.ppxClient.retryGoalStep(goal.goalId, goal.revision, stepId);
      if (sessionId === selectedSessionIdRef.current) setCurrentGoal(updated);
      if (sessionId === selectedSessionIdRef.current) {
        await sendMessage(`Retry the blocked Goal step and continue working toward: ${updated.objective}`);
      }
      return true;
    } catch (error) {
      setGoalMutationError(clientErrorMessage(error));
      await refreshCurrentGoal(sessionId);
      return false;
    } finally {
      setGoalMutation(null);
    }
  }

  useEffect(() => {
    if (!selectedAgentId || !selectedSessionId) {
      setSessionArtifacts([]);
      return;
    }
    void refreshSessionArtifacts(selectedAgentId, selectedSessionId);
  }, [selectedAgentId, selectedSessionId]);

  useEffect(() => {
    setGoalMutationError(null);
    void refreshCurrentGoal(selectedSessionId);
  }, [selectedSessionId]);

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
      } else if (event.type === "session.updated") {
        if (event.session.agentId === selectedAgentIdRef.current) {
          setSessions((current) =>
            sortSessionsByRecency([
              mergeSessionSummary(
                current.find((item) => item.id === event.session.id),
                event.session,
              ),
              ...current.filter((item) => item.id !== event.session.id),
            ]),
          );
        }
      } else if (event.type === "run.finished") {
        if (event.sessionId === selectedSessionIdRef.current) {
          setMessages((current) => current.map((message) => (
            message.role === "assistant" && message.runId === event.runId
              ? { ...message, status: event.status }
              : message
          )));
        }
        activeRuns.finish(event.sessionId);
        setCancellingRunId((current) => (current === event.runId ? null : current));
        if (event.sessionId === selectedSessionIdRef.current) {
          void refreshCurrentGoal(event.sessionId);
          void refreshSessionArtifacts(selectedAgentIdRef.current, event.sessionId);
        }
      }
    });

    void (async () => {
      let nextUserProfile: UserProfile;
      try {
        nextUserProfile = await window.ppxClient.getUserProfile();
      } catch {
        if (mounted) {
          setAuthenticationRequired(true);
          setAuthenticationError(null);
          setBootstrapError(null);
          setReady(true);
        }
        return;
      }
      try {
        const [nextDiagnostics, setupProjection] = await Promise.all([
          window.ppxClient.getDiagnostics(),
          hasRootAccess(nextUserProfile)
            ? window.ppxClient.getSetupStatus()
            : window.ppxClient.getSetupReadiness(),
        ]);
        const nextSetupStatus = hasRootAccess(nextUserProfile)
          ? setupProjection as SetupStatusResult
          : null;
        const nextSetupReadiness = nextSetupStatus
          ? setupReadinessFromStatus(nextSetupStatus)
          : setupProjection as SetupReadinessResult;
        let payload: BootstrapPayload;
        try {
          payload = await window.ppxClient.bootstrap();
        } catch (error) {
          if (isWorkspaceConfigurationComplete(nextSetupReadiness)) throw error;
          payload = onboardingBootstrap(nextDiagnostics);
        }
        if (!mounted) {
          return;
        }
        setRuntime(payload.runtime);
        setSetupReadiness(nextSetupReadiness);
        setSetupStatus(nextSetupStatus);
        setDiagnostics(nextDiagnostics);
        setUserProfile(nextUserProfile);
        if (nextSetupStatus && !setupFormInitializedRef.current) {
          setupFormInitializedRef.current = true;
          setSetupForm(setupFormFromStatus(nextSetupStatus, nextDiagnostics, nextUserProfile.id));
        }
        setAgents(payload.agents);
        setSessions(payload.sessions);
        replaceMessages(payload.messages);
        selectAgentId(payload.selectedAgentId);
        selectSessionId(payload.selectedSessionId);
        setReady(true);
        setBootstrapError(null);
        if (isWorkspaceConfigurationComplete(nextSetupReadiness) && hasRootAccess(nextUserProfile)) void window.ppxClient
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
        if (isWorkspaceConfigurationComplete(nextSetupReadiness)) void window.ppxClient
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
        if (isWorkspaceConfigurationComplete(nextSetupReadiness)) void window.ppxClient
          .listModelProfiles()
          .then((nextDiagnostics) => {
            if (mounted) {
              setModelProfiles(nextDiagnostics.profiles);
            }
          })
          .catch(() => {
            if (mounted) setModelProfiles([]);
          });
        if (isWorkspaceConfigurationComplete(nextSetupReadiness) && hasRootAccess(nextUserProfile)) void Promise.all([
          window.ppxClient.getOperationsOverview(),
          window.ppxClient.listOperationsAudit(20),
        ])
          .then(([overview, audit]) => {
            if (mounted) {
              setOperationsOverview(overview);
              setOperationsAudit(audit.items);
              setOperationsError(null);
            }
          })
          .catch((error: unknown) => {
            if (mounted) setOperationsError(error instanceof Error ? error.message : String(error));
          });
      } catch (error) {
        if (mounted) {
          setAuthenticationRequired(true);
          setAuthenticationError(
            `Your saved sign-in is valid, but the Node workspace could not be loaded: ${clientErrorMessage(error)}`,
          );
          setBootstrapError(null);
          setReady(true);
        }
      }
    })();

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

  useEffect(() => {
    if (!setupStatus || !setupForm.provider || !hasRootAccess(userProfile)) {
      return;
    }
    let active = true;
    const provider = setupStatus.providers.find((item) => item.id === setupForm.provider);
    setProviderAccessLoading(true);
    setProviderAccessError(null);
    setProviderAuth(null);
    Promise.all([
      window.ppxClient.getProviderModels(setupForm.provider),
      provider?.id === "openai_codex"
        ? window.ppxClient.getProviderAuthStatus(setupForm.provider)
        : Promise.resolve(null),
    ])
      .then(([catalog, auth]) => {
        if (!active) return;
        setProviderModels(catalog);
        setProviderAuth(auth);
        if (catalog.authoritative && !catalog.items.some((item) => item.id === setupForm.model)) {
          const nextModel = catalog.items[0]?.id ?? catalog.defaultModel;
          setSetupForm((current) => current.provider === catalog.providerId
            ? { ...current, model: nextModel }
            : current);
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setProviderModels(null);
          setProviderAccessError(error instanceof Error ? error.message : String(error));
        }
      })
      .finally(() => {
        if (active) setProviderAccessLoading(false);
      });
    return () => {
      active = false;
    };
  }, [setupForm.provider, setupStatus, userProfile]);

  useEffect(() => {
    if (providerAuth?.state !== "pending") {
      return;
    }
    const timer = window.setInterval(() => {
      void window.ppxClient.getProviderAuthStatus(providerAuth.providerId)
        .then((status) => {
          setProviderAuth(status);
          if (status.state === "authenticated") setProviderAccessError(null);
          if (status.session?.state === "failed") {
            setProviderAccessError(status.session.error ?? "Codex sign-in did not complete.");
          }
        })
        .catch((error: unknown) => {
          setProviderAccessError(error instanceof Error ? error.message : String(error));
        });
    }, 1_500);
    return () => window.clearInterval(timer);
  }, [providerAuth?.providerId, providerAuth?.state]);

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
    active: ready && isWorkspaceConfigurationComplete(setupReadiness) && runtime?.state !== "stopped",
    check: async () => {
      let nextDiagnostics = await window.ppxClient.getDiagnostics();
      setDiagnostics(nextDiagnostics);
      if (nextDiagnostics.clientApiHealthy) {
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
              summary: `Reconnecting to ${productProfile.displayName} Node...`,
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
    setAttachments([]);
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
  }

  async function createAgent(input: AgentCreateRequest): Promise<boolean> {
    setAgentCreating(true);
    setAgentCreateError(null);
    setSendError(null);
    try {
      const result = await window.ppxClient.createAgent(input);
      const nextAgent: AgentProfile = result.agent;
      setAgents((current) => [...current.filter((agent) => agent.id !== nextAgent.id), nextAgent]);
      selectAgentId(nextAgent.id);
      selectSessionId("");
      setSessions([]);
      replaceMessages([]);
      setDiagnostics((current) => current ? { ...current, agentCount: current.agentCount + 1 } : current);
      return true;
    } catch (error) {
      setAgentCreateError(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setAgentCreating(false);
    }
  }

  async function reloadWorkspace(): Promise<void> {
    // Refresh Node-owned workspace projections after lifecycle mutations.
    const payload = await window.ppxClient.bootstrap();
    setRuntime(payload.runtime);
    setAgents(payload.agents);
    setSessions(payload.sessions);
    replaceMessages(payload.messages);
    selectAgentId(payload.selectedAgentId);
    selectSessionId(payload.selectedSessionId);
    setDiagnostics(await window.ppxClient.getDiagnostics());
  }

  async function login(email: string, secret: string): Promise<boolean> {
    setAuthenticating(true);
    setAuthenticationError(null);
    let authenticated = false;
    try {
      const connection = normalizeLoginConnectionSettings(connectionForm);
      if (connection.targetType === "lan" && new URL(connection.clientApiBaseUrl).protocol !== "https:") {
        throw new Error("Sign-in requires an HTTPS Node URL unless it uses localhost or a loopback IP.");
      }
      const request: UserLoginRequest = { connection, email, secret };
      const profile = await window.ppxClient.login(request);
      authenticated = true;
      setUserProfile(profile);
      setAuthenticationRequired(false);
      const [setupProjection, nextDiagnostics] = await Promise.all([
        hasRootAccess(profile)
          ? window.ppxClient.getSetupStatus()
          : window.ppxClient.getSetupReadiness(),
        window.ppxClient.getDiagnostics(),
      ]);
      const nextSetupStatus = hasRootAccess(profile)
        ? setupProjection as SetupStatusResult
        : null;
      const nextSetupReadiness = nextSetupStatus
        ? setupReadinessFromStatus(nextSetupStatus)
        : setupProjection as SetupReadinessResult;
      setSetupReadiness(nextSetupReadiness);
      setSetupStatus(nextSetupStatus);
      setDiagnostics(nextDiagnostics);
      if (nextSetupStatus) {
        setupFormInitializedRef.current = true;
        setSetupForm(setupFormFromStatus(nextSetupStatus, nextDiagnostics, profile.id));
      }
      await reloadWorkspace();
      setSlashCommands((await window.ppxClient.listSlashCommands()).commands);
      if (isWorkspaceConfigurationComplete(nextSetupReadiness)) {
        await refreshModelProfiles();
        if (hasRootAccess(profile)) await refreshExtensions();
        if (hasRootAccess(profile)) await refreshOperations();
      }
      return true;
    } catch (error) {
      setAuthenticationRequired(true);
      const message = clientErrorMessage(error);
      setAuthenticationError(authenticated
        ? `Sign-in succeeded, but the Node workspace could not be loaded: ${message}`
        : message);
      return false;
    } finally {
      setAuthenticating(false);
    }
  }

  async function logout(): Promise<void> {
    await window.ppxClient.logout();
    setAuthenticationRequired(true);
    setAuthenticationError(null);
    setUserProfile({ id: "", displayName: "Signed out", accountKind: "product" });
    setRuntime(null);
    setDiagnostics(null);
    setSetupReadiness(null);
    setSetupStatus(null);
    setAgents([]);
    setSessions([]);
    replaceMessages([]);
    setExtensions([]);
    setModelProfiles([]);
    setSlashCommands([]);
  }

  async function switchSession(session: SessionSummary): Promise<void> {
    const requestId = ++switchRequestIdRef.current;
    setSendError(null);
    setAttachments([]);
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

  async function refreshOperations(): Promise<void> {
    setOperationsLoading(true);
    setOperationsError(null);
    try {
      const [overview, audit] = await Promise.all([
        window.ppxClient.getOperationsOverview(),
        window.ppxClient.listOperationsAudit(20),
      ]);
      setOperationsOverview(overview);
      setOperationsAudit(audit.items);
    } catch (error) {
      setOperationsError(error instanceof Error ? error.message : String(error));
    } finally {
      setOperationsLoading(false);
    }
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

  async function refreshModelProfiles(): Promise<void> {
    const result = await window.ppxClient.listModelProfiles();
    setModelProfiles(result.profiles);
  }

  async function readModelProfile(profileId: string): Promise<ModelProfileResourceResult> {
    return window.ppxClient.readModelProfile(profileId);
  }

  async function createModelProfile(input: ModelProfileCreateInput): Promise<ModelProfileResourceResult> {
    const result = await window.ppxClient.createModelProfile(input);
    await refreshModelProfiles();
    return result;
  }

  async function updateModelProfile(input: ModelProfileUpdateInput): Promise<ModelProfileResourceResult> {
    const result = await window.ppxClient.updateModelProfile(input);
    await refreshModelProfiles();
    return result;
  }

  async function getModelProviderModels(providerId: string): Promise<ModelCatalogResult> {
    return window.ppxClient.getProviderModels(providerId);
  }

  async function getModelProviderAuth(providerId: string): Promise<ProviderAuthStatus> {
    return window.ppxClient.getProviderAuthStatus(providerId);
  }

  async function beginModelProviderAuth(providerId: string): Promise<ProviderAuthStatus> {
    return window.ppxClient.beginProviderAuth(providerId);
  }

  async function refreshModelProviderAuth(providerId: string): Promise<ProviderAuthStatus> {
    return window.ppxClient.refreshProviderAuth(providerId);
  }

  async function openExternalUrl(url: string): Promise<void> {
    await window.ppxClient.openExternalUrl(url);
  }

  async function completeSetup(applyConfiguration = false): Promise<void> {
    if (!setupStatus || setupSubmitting) {
      return;
    }
    setSetupSubmitting(true);
    setSetupError(null);
    try {
      if (setupStatus.state !== "configured" || applyConfiguration) {
        const request = buildSetupRequest(setupForm, setupStatus);
        const applied = await window.ppxClient.applySetup(request);
        if (applied.restartRequired) {
          setRuntime(await window.ppxClient.runRuntimeCommand("restart"));
        }
      }
      await window.ppxClient.runSetupHello(setupForm.agentId, setupForm.ownerPrincipalId, setupForm.hello);
      const verified = await window.ppxClient.getSetupStatus();
      if (verified.state !== "ready") {
        throw new Error("The first model turn completed, but setup verification is not ready.");
      }
      setSetupStatus(verified);
      setSetupReadiness(setupReadinessFromStatus(verified));
      setSetupForm((current) => ({ ...current, apiKey: "" }));
      applyConnectionBootstrap(await window.ppxClient.bootstrap());
      await Promise.all([refreshExtensions(), refreshModelProfiles()]);
      setSlashCommands((await window.ppxClient.listSlashCommands()).commands);
    } catch (error) {
      setSetupError(error instanceof Error ? error.message : String(error));
      try {
        const latest = await window.ppxClient.getSetupStatus();
        setSetupStatus(latest);
        setSetupReadiness(setupReadinessFromStatus(latest));
      } catch {
        // Preserve the actionable setup error when the endpoint also became unavailable.
      }
    } finally {
      setSetupSubmitting(false);
    }
  }

  async function beginProviderAuth(): Promise<void> {
    setProviderAccessLoading(true);
    setProviderAccessError(null);
    try {
      const status = await window.ppxClient.beginProviderAuth(setupForm.provider);
      setProviderAuth(status);
      const url = status.session?.verificationUrl;
      if (url) await window.ppxClient.openExternalUrl(url);
    } catch (error) {
      setProviderAccessError(error instanceof Error ? error.message : String(error));
    } finally {
      setProviderAccessLoading(false);
    }
  }

  async function refreshProviderAuth(): Promise<void> {
    setProviderAccessLoading(true);
    setProviderAccessError(null);
    try {
      setProviderAuth(await window.ppxClient.refreshProviderAuth(setupForm.provider));
    } catch (error) {
      setProviderAccessError(error instanceof Error ? error.message : String(error));
    } finally {
      setProviderAccessLoading(false);
    }
  }

  async function openProviderAuthPage(): Promise<void> {
    const url = providerAuth?.session?.verificationUrl;
    if (url) await window.ppxClient.openExternalUrl(url);
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
    const candidateVersion = connectionCandidateVersionRef.current;
    setSavingConnection(true);
    setConnectionTestState("testing");
    setConnectionFeedback(null);
    let attemptedSettings = connectionForm;
    try {
      const nextSettings = normalizeConnectionSettings(connectionForm);
      attemptedSettings = nextSettings;
      const nextDiagnostics = await window.ppxClient.saveConnectionSettings(nextSettings);
      setDiagnostics(nextDiagnostics);
      if (nextSettings.targetType === "local") {
        await window.ppxClient.runRuntimeCommand("restart");
      }
      try {
        const payload = await window.ppxClient.bootstrap();
        applyConnectionBootstrap(payload);
        const nextSetupStatus = await window.ppxClient.getSetupStatus();
        setSetupStatus(nextSetupStatus);
        setSetupReadiness(setupReadinessFromStatus(nextSetupStatus));
        setupFormInitializedRef.current = true;
        setSetupForm(setupFormFromStatus(nextSetupStatus, nextDiagnostics, userProfile.id));
      } catch {
        setAgents([]);
        setSessions([]);
        replaceMessages([]);
        selectAgentId("");
        selectSessionId("");
      }
      if (candidateVersion === connectionCandidateVersionRef.current) {
        setConnectionTestState("connected");
        setConnectionFeedback(`Connected to ${nextDiagnostics.nodeName ?? nextDiagnostics.target.name}.`);
      }
    } catch (error) {
      if (candidateVersion === connectionCandidateVersionRef.current) {
        setConnectionTestState("failed");
        setConnectionFeedback(connectionFailureMessage(error, attemptedSettings));
      }
    } finally {
      setSavingConnection(false);
    }
  }

  async function testConnection(): Promise<void> {
    const candidateVersion = connectionCandidateVersionRef.current;
    setTestingConnection(true);
    setConnectionTestState("testing");
    setConnectionFeedback(null);
    let attemptedSettings = connectionForm;
    try {
      const nextSettings = normalizeConnectionSettings(connectionForm);
      attemptedSettings = nextSettings;
      const nextDiagnostics = await window.ppxClient.testConnectionSettings(nextSettings);
      if (candidateVersion === connectionCandidateVersionRef.current) {
        setConnectionTestState("connected");
        setConnectionFeedback(
          `Connection successful: ${nextDiagnostics.nodeName ?? nextDiagnostics.target.name} · ${nextDiagnostics.clientApiProductVersion ?? "unknown"}`,
        );
      }
    } catch (error) {
      if (candidateVersion === connectionCandidateVersionRef.current) {
        setConnectionTestState("failed");
        setConnectionFeedback(connectionFailureMessage(error, attemptedSettings));
      }
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

  async function refreshSelectedAgentSessions(preferredSessionId = selectedSessionIdRef.current): Promise<void> {
    const agentId = selectedAgentIdRef.current;
    if (!agentId) return;
    const listed = await window.ppxClient.listSessions(agentId);
    setSessions(listed.sessions);
    const next = listed.sessions.find((session) => session.id === preferredSessionId && !session.archived)
      ?? listed.sessions.find((session) => !session.archived)
      ?? listed.sessions[0];
    if (!next) {
      selectSessionId("");
      replaceMessages([]);
      return;
    }
    selectSessionId(next.id);
    replaceMessages((await window.ppxClient.loadSession(next.id)).messages);
  }

  async function renameSession(session: SessionSummary, title: string): Promise<void> {
    await window.ppxClient.renameSession({ agentId: session.agentId, sessionId: session.id, title });
    await refreshSelectedAgentSessions(session.id);
  }

  async function archiveSession(session: SessionSummary): Promise<void> {
    if (!session.archived && activeRuns.isSessionRunning(session.id)) {
      throw new Error("Wait for the current Run to finish before archiving.");
    }
    await window.ppxClient.archiveSession({ agentId: session.agentId, sessionId: session.id, archived: !session.archived });
    await refreshSelectedAgentSessions(session.archived ? session.id : "");
  }

  async function forkSession(session: SessionSummary): Promise<void> {
    const created = await window.ppxClient.forkSession({ agentId: session.agentId, sessionId: session.id });
    await refreshSelectedAgentSessions(created.session.id);
  }

  async function exportSession(session: SessionSummary): Promise<void> {
    const payload = await window.ppxClient.exportSession({ agentId: session.agentId, sessionId: session.id });
    const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${session.title.replace(/[^a-z0-9-_]+/gi, "-").replace(/^-+|-+$/g, "") || "openppx-session"}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function deleteSession(session: SessionSummary): Promise<void> {
    await window.ppxClient.deleteSession({ agentId: session.agentId, sessionId: session.id });
    await refreshSelectedAgentSessions("");
  }

  async function ensureActiveSession(agentId: string, preferredSessionId: string): Promise<SessionSummary> {
    const existing = sessions.find((session) => session.id === preferredSessionId && session.agentId === agentId);
    if (existing) {
      if (existing.archived) {
        throw new Error(ARCHIVED_SESSION_GUIDANCE);
      }
      return existing;
    }
    const listed = await window.ppxClient.listSessions(agentId);
    const firstSession = listed.sessions.find((session) => !session.archived);
    if (firstSession) {
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

  function applyUserActivityToSession(sessionId: string, text: string, timestamp: string): void {
    const title = compactSessionTitle(text);
    setSessions((current) =>
      sortSessionsByRecency(
        current.map((session) => {
          if (session.id !== sessionId) {
            return session;
          }
          return {
            ...session,
            ...(title && isGenericSessionTitle(session.title) ? { title } : {}),
            updatedAt: timestamp,
          };
        }),
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
      if (outcome.lifecycle === "agent_turn") {
        await refreshCurrentGoal(selectedSessionIdRef.current);
        const startAgentTurn = record(record(outcome.result).startAgentTurn);
        const turnText = String(startAgentTurn.text ?? "").trim();
        if (turnText) {
          await sendMessage(turnText);
          return;
        }
      }
      appendCommandNotice(outcome);
    } catch (error) {
      setSendError(clientErrorMessage(error));
    }
  }

  async function sendMessage(rawText?: string): Promise<void> {
    const text = (rawText ?? composer).trim();
    const queuedAttachments = [...attachments];
    if ((!text && queuedAttachments.length === 0) || !selectedAgentId) {
      return;
    }
    const targetSession = sessions.find((session) => session.id === selectedSessionIdRef.current);
    if (targetSession?.archived) {
      setSendError(ARCHIVED_SESSION_GUIDANCE);
      return;
    }
    if (text.startsWith("/") && queuedAttachments.length === 0) {
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
    let uploadedArtifacts: ArtifactSummary[] = [];
    if (queuedAttachments.length) {
      setAttachments((current) => current.map((item) => ({ ...item, status: "uploading" })));
      try {
        for (const attachment of queuedAttachments) {
          uploadedArtifacts.push(await window.ppxClient.uploadArtifact({
            agentId: selectedAgentId,
            sessionId,
            fileName: attachment.fileName,
            mimeType: attachment.mimeType,
            dataBase64: attachment.dataBase64,
          }));
        }
      } catch (error) {
        setAttachments((current) => current.map((item) => ({ ...item, status: "failed" })));
        setSendError(error instanceof Error ? error.message : String(error));
        return;
      }
    }
    if (uploadedArtifacts.length) {
      setSessionArtifacts((current) => [
        ...uploadedArtifacts,
        ...current.filter((item) => !uploadedArtifacts.some((uploaded) => uploaded.id === item.id)),
      ]);
    }
    activeRuns.begin(sessionId);
    setComposer("");
    setAttachments([]);
    const attachmentParts: ChatMessage["parts"] = queuedAttachments.map((attachment) =>
      attachment.mimeType.startsWith("image/")
        ? {
            type: "image" as const,
            text: attachment.fileName,
            url: `data:${attachment.mimeType};base64,${attachment.dataBase64}`,
            mimeType: attachment.mimeType,
          }
        : {
            type: "file" as const,
            text: "Attached file",
            fileName: attachment.fileName,
            sizeBytes: attachment.sizeBytes,
            mimeType: attachment.mimeType,
          },
    );
    const optimisticMessage: ChatMessage = {
      id: `local-user-${crypto.randomUUID()}`,
      sessionId,
      role: "user",
      status: "completed",
      createdAt: new Date().toISOString(),
      parts: [...(text ? [{ type: "markdown" as const, text }] : []), ...attachmentParts],
    };
    applyUserActivityToSession(
      sessionId,
      text || queuedAttachments.map((item) => item.fileName).join(", "),
      optimisticMessage.createdAt,
    );
    setMessages((current) => [...current, optimisticMessage]);
    try {
      const artifactRefs = uploadedArtifacts.map(({ key, version }) => ({ key, version }));
      const result = await window.ppxClient.sendMessage({
        agentId: selectedAgentId,
        sessionId,
        text,
        ...(artifactRefs.length ? { artifactRefs } : {}),
      });
      activeRuns.attachRunId(sessionId, result.runId);
    } catch (error) {
      console.error("Failed to send message", error);
      activeRuns.finish(sessionId);
      setAttachments(queuedAttachments.map((item) => ({ ...item, status: "ready" })));
      setSendError(error instanceof Error ? error.message : String(error));
    }
  }

  async function addAttachments(files: File[]): Promise<void> {
    if (!files.length) return;
    const targetSession = sessions.find((session) => session.id === selectedSessionIdRef.current);
    if (targetSession?.archived) {
      setSendError(ARCHIVED_SESSION_GUIDANCE);
      return;
    }
    if (attachments.length + files.length > MAX_MESSAGE_ATTACHMENTS) {
      setSendError(`A message can include at most ${MAX_MESSAGE_ATTACHMENTS} files.`);
      return;
    }
    const accepted: PendingAttachment[] = [];
    let totalBytes = attachments.reduce((sum, attachment) => sum + attachment.sizeBytes, 0);
    for (const file of files) {
      const preflightError = attachmentPreflightError(file);
      if (preflightError) {
        setSendError(preflightError);
        continue;
      }
      if (totalBytes + file.size > MAX_MESSAGE_ATTACHMENT_BYTES) {
        setSendError("Attachments for one message cannot exceed 50 MB in total.");
        continue;
      }
      try {
        accepted.push({
          id: crypto.randomUUID(),
          fileName: file.name,
          mimeType: file.type || "application/octet-stream",
          sizeBytes: file.size,
          dataBase64: await fileToBase64(file),
          status: "ready",
        });
        totalBytes += file.size;
      } catch (error) {
        setSendError(error instanceof Error ? error.message : String(error));
      }
    }
    if (accepted.length) {
      setAttachments((current) => [...current, ...accepted].slice(0, MAX_MESSAGE_ATTACHMENTS));
      setSendError(null);
    }
  }

  async function loadArtifactData(artifact: ArtifactSummary): Promise<string> {
    if (!selectedAgentId || !selectedSessionId) {
      throw new Error("Select a Session before opening an artifact.");
    }
    const result = await window.ppxClient.downloadArtifact(
      selectedAgentId,
      selectedSessionId,
      artifact,
    );
    return `data:${result.mimeType};base64,${result.dataBase64}`;
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
    authenticationRequired,
    authenticating,
    authenticationError,
    bootstrapError,
    runtime,
    diagnostics,
    userProfile,
    login,
    logout,
    agents,
    agentCreating,
    agentCreateError,
    clearAgentCreateError: () => setAgentCreateError(null),
    sessions,
    messages,
    sessionArtifacts,
    currentGoal,
    goalMutation,
    goalMutationError,
    selectedAgentId,
    selectedSessionId,
    selectedAgent,
    selectedSession,
    composer,
    setComposer,
    attachments,
    addAttachments,
    removeAttachment: (attachmentId: string) => setAttachments((current) => current.filter((item) => item.id !== attachmentId)),
    loadArtifactData,
    sendError,
    activeSessionIds: activeRuns.sessionIds,
    currentSessionRunning,
    selectedAgentBusy,
    activeRunId,
    cancellingCurrentRun: Boolean(activeRunId && cancellingRunId === activeRunId),
    connectionForm,
    setConnectionForm: updateConnectionForm,
    savingConnection,
    testingConnection,
    connectionTestState,
    connectionFeedback,
    extensions,
    extensionsLoading,
    extensionsError,
    extensionMutationId,
    slashCommands,
    setupReadiness,
    setupStatus,
    setupForm,
    setSetupForm,
    setupSubmitting,
    setupError,
    providerModels,
    providerAuth,
    providerAccessLoading,
    providerAccessError,
    modelProfiles,
    operationsOverview,
    operationsAudit,
    operationsLoading,
    operationsError,
    transcriptResetKey,
    switchAgent,
    createAgent,
    reloadWorkspace,
    switchSession,
    runRuntimeAction,
    stopRuntime,
    refreshOperations,
    refreshExtensions,
    refreshModelProfiles,
    readModelProfile,
    createModelProfile,
    updateModelProfile,
    getModelProviderModels,
    getModelProviderAuth,
    beginModelProviderAuth,
    refreshModelProviderAuth,
    openExternalUrl,
    completeSetup,
    beginProviderAuth,
    refreshProviderAuth,
    openProviderAuthPage,
    setExtensionEnabled,
    saveConnection,
    testConnection,
    createSession,
    renameSession,
    archiveSession,
    forkSession,
    exportSession,
    deleteSession,
    sendMessage,
    cancelCurrentRun,
    updateCurrentGoal,
    transitionCurrentGoal,
    retryCurrentGoal,
  };
}
