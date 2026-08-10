import type { AgentCreateRequest, AgentUpdateInput, AppConnectionEnablementRequest, AppConnectionRemoveRequest, AppConnectionSaveRequest, ArtifactSummary, ArtifactUploadInput, AutomationCreateInput, AutomationStatus, AutomationUpdateRequest, ConnectionSettings, CronCreateInput, CronUpdateInput, ExtensionEnablementRequest, ExtensionInstallRequest, ExtensionPreviewRequest, ExtensionRemoveRequest, GoalTransitionOperation, GoalUpdateRequest, HeartbeatConfiguration, McpMutationRequest, McpServerResource, McpValueBinding, ModelCapability, ModelProfileCreateInput, ModelProfileUpdateInput, OperationsTaskControlInput, PluginMarketplaceSourceSpec, RuntimeCommand, SendMessageInput, SessionMutationRequest, SetupApplyRequest, SlashCommandRequest, UserLoginRequest } from "../../app/src/types";

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object.`);
  }
  return value as Record<string, unknown>;
}

function string(value: unknown, label: string, maxLength: number, allowEmpty = false): string {
  if (typeof value !== "string") {
    throw new TypeError(`${label} must be a string.`);
  }
  if (!allowEmpty && !value.trim()) {
    throw new TypeError(`${label} is required.`);
  }
  if (value.length > maxLength) {
    throw new TypeError(`${label} exceeds ${maxLength} characters.`);
  }
  return value;
}

function integer(value: unknown, label: string, minimum: number, maximum: number): number {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new TypeError(`${label} must be an integer between ${minimum} and ${maximum}.`);
  }
  return parsed;
}

/** Validate untrusted Renderer input before it reaches Main-process services. */
export function validateConnectionSettings(value: unknown): ConnectionSettings {
  const input = record(value, "Connection settings");
  if (input.targetType !== "local" && input.targetType !== "lan") {
    throw new TypeError("Connection targetType must be local or lan.");
  }
  return {
    targetType: input.targetType,
    targetId: string(input.targetId, "Connection targetId", 256),
    targetName: string(input.targetName, "Connection targetName", 256),
    clientApiBaseUrl: string(input.clientApiBaseUrl, "Client API URL", 2_048),
    accessToken:
      input.accessToken === undefined ? undefined : string(input.accessToken, "Access token", 16_384, true),
    userId: input.userId === undefined ? undefined : string(input.userId, "User id", 128, true),
    userEmail: input.userEmail === undefined ? undefined : string(input.userEmail, "User email", 254, true),
    userPrivilegeLevel: ["low", "medium", "high", "root"].includes(String(input.userPrivilegeLevel))
      ? input.userPrivilegeLevel as ConnectionSettings["userPrivilegeLevel"]
      : undefined,
  };
}

/** Validate transient login credentials without persisting them in Renderer state stores. */
export function validateUserLoginRequest(value: unknown): UserLoginRequest {
  const input = record(value, "User login");
  const email = string(input.email, "Email", 254).trim();
  const secret = string(input.secret, "Secret", 4_096);
  const connection = validateConnectionSettings(input.connection);
  if (!email.includes("@")) throw new TypeError("Enter a valid email address.");
  if (new TextEncoder().encode(secret).length < 8) {
    throw new TypeError("Secret must contain at least 8 UTF-8 bytes.");
  }
  if (connection.targetType === "lan" && new URL(connection.clientApiBaseUrl).protocol !== "https:") {
    throw new TypeError("Remote user login requires an HTTPS Node URL.");
  }
  return {
    connection,
    email,
    secret,
  };
}

/** Validate a runtime command from the isolated Renderer. */
export function validateRuntimeCommand(value: unknown): RuntimeCommand {
  if (value !== "start" && value !== "stop" && value !== "restart") {
    throw new TypeError("Runtime command must be start, stop, or restart.");
  }
  return value;
}

/** Validate one session or Run identifier crossing the IPC trust boundary. */
export function validateIdentifier(value: unknown, label: string): string {
  return string(value, label, 512);
}

/** Validate a bounded JSON object without allowing functions or cyclic values through IPC. */
function boundedJsonRecord(value: unknown, label: string, maxLength = 65_536): Record<string, unknown> {
  const input = record(value, label);
  let encoded: string;
  try {
    encoded = JSON.stringify(input);
  } catch {
    throw new TypeError(`${label} must be JSON serializable.`);
  }
  if (encoded.length > maxLength) throw new TypeError(`${label} is too large.`);
  return JSON.parse(encoded) as Record<string, unknown>;
}

export function validateAutomationStatuses(value: unknown): AutomationStatus[] {
  if (!Array.isArray(value) || value.length > 3) throw new TypeError("Automation statuses must be an array.");
  return value.map((status) => {
    if (status !== "active" && status !== "paused" && status !== "blocked") {
      throw new TypeError("Automation status is not supported.");
    }
    return status;
  });
}

export function validateAutomationCreateInput(value: unknown): AutomationCreateInput {
  const input = boundedJsonRecord(value, "Automation create request");
  const outputRequirements = input.outputRequirements === undefined ? [] : input.outputRequirements;
  if (!Array.isArray(outputRequirements) || outputRequirements.length > 50 || outputRequirements.some((item) => typeof item !== "string" || item.length > 1_024)) {
    throw new TypeError("Automation output requirements are invalid.");
  }
  return {
    ...input,
    userId: string(input.userId, "Automation user id", 128),
    agentId: string(input.agentId, "Automation Agent id", 63),
    name: string(input.name, "Automation name", 128),
    description: input.description === undefined ? "" : string(input.description, "Automation description", 1_024, true),
    instructions: string(input.instructions, "Automation instructions", 16_384),
    outputRequirements: outputRequirements as string[],
  } as AutomationCreateInput;
}

export function validateAutomationUpdateInput(value: unknown): AutomationUpdateRequest {
  const input = boundedJsonRecord(value, "Automation update request");
  return {
    ...input,
    automationId: validateIdentifier(input.automationId, "Automation id"),
    userId: string(input.userId, "Automation user id", 128),
    expectedRevision: integer(input.expectedRevision, "Automation revision", 1, Number.MAX_SAFE_INTEGER),
  } as AutomationUpdateRequest;
}

export function validateAutomationOperation(value: unknown): "pause" | "resume" | "delete" {
  if (value !== "pause" && value !== "resume" && value !== "delete") {
    throw new TypeError("Automation transition is not supported.");
  }
  return value;
}

export function validateAutomationRevision(value: unknown): number {
  return integer(value, "Automation revision", 1, Number.MAX_SAFE_INTEGER);
}

export function validateAutomationInput(value: unknown): Record<string, unknown> {
  return boundedJsonRecord(value, "Automation input");
}

/** Validate one user-authored Goal objective and its optimistic revision. */
export function validateGoalUpdateRequest(value: unknown): GoalUpdateRequest {
  const input = record(value, "Goal update request");
  return {
    goalId: validateIdentifier(input.goalId, "Goal id"),
    expectedRevision: integer(input.expectedRevision, "Goal revision", 1, Number.MAX_SAFE_INTEGER),
    objective: string(input.objective, "Goal objective", 16_384),
  };
}

export function validateGoalTransitionOperation(value: unknown): GoalTransitionOperation {
  if (value !== "pause" && value !== "resume" && value !== "cancel") {
    throw new TypeError("Goal transition is not supported.");
  }
  return value;
}

export function validateGoalRevision(value: unknown): number {
  return integer(value, "Goal revision", 1, Number.MAX_SAFE_INTEGER);
}

/** Validate one durable Task control request from the isolated Renderer. */
export function validateOperationsTaskControlInput(value: unknown): OperationsTaskControlInput {
  const input = record(value, "Task control request");
  const allowed = new Set(["interrupt", "cancel", "pause", "resume", "restart", "send_input"]);
  if (typeof input.action !== "string" || !allowed.has(input.action)) {
    throw new TypeError("Task action is not supported.");
  }
  const content = input.content === undefined ? "" : string(input.content, "Task input", 8_000, true);
  if (input.action === "send_input" && !content.trim()) {
    throw new TypeError("Task input is required.");
  }
  const inlineBudgetMs = input.inlineBudgetMs === undefined || input.inlineBudgetMs === null
    ? undefined
    : Number(input.inlineBudgetMs);
  if (inlineBudgetMs !== undefined && (!Number.isInteger(inlineBudgetMs) || inlineBudgetMs < 100 || inlineBudgetMs > 300_000)) {
    throw new TypeError("Task inline budget must be between 100 and 300000 milliseconds.");
  }
  return {
    taskId: validateIdentifier(input.taskId, "Task id"),
    action: input.action as OperationsTaskControlInput["action"],
    ...(content ? { content } : {}),
    ...(inlineBudgetMs !== undefined ? { inlineBudgetMs } : {}),
  };
}

/** Validate one Agent-scoped Cron create request at the IPC trust boundary. */
export function validateOperationsCronCreateInput(value: unknown): CronCreateInput {
  const input = record(value, "Cron create request");
  const rawSchedule = record(input.schedule, "Cron schedule");
  if (rawSchedule.kind !== "every" && rawSchedule.kind !== "cron" && rawSchedule.kind !== "at") {
    throw new TypeError("Cron schedule kind is not supported.");
  }
  const schedule: CronCreateInput["schedule"] = { kind: rawSchedule.kind };
  if (rawSchedule.kind === "every") {
    const seconds = Number(rawSchedule.everySeconds);
    if (!Number.isInteger(seconds) || seconds < 1 || seconds > 31_536_000) {
      throw new TypeError("Cron interval must be between 1 and 31536000 seconds.");
    }
    schedule.everySeconds = seconds;
  } else if (rawSchedule.kind === "cron") {
    schedule.cronExpression = string(rawSchedule.cronExpression, "Cron expression", 256);
    if (rawSchedule.timezone !== undefined && rawSchedule.timezone !== "") {
      schedule.timezone = string(rawSchedule.timezone, "Cron timezone", 128);
    }
  } else {
    const atMs = Number(rawSchedule.atMs);
    if (!Number.isInteger(atMs) || atMs < 1) {
      throw new TypeError("Cron run time must be a positive epoch millisecond value.");
    }
    schedule.atMs = atMs;
  }
  if (input.deleteAfterRun !== undefined && typeof input.deleteAfterRun !== "boolean") {
    throw new TypeError("Cron delete-after-run must be a boolean.");
  }
  return {
    name: string(input.name, "Cron name", 120),
    agentId: resourceName(input.agentId, "Cron Agent id"),
    userId: string(input.userId, "Cron user id", 128),
    message: string(input.message, "Cron message", 8_000),
    schedule,
    deleteAfterRun: input.deleteAfterRun === true,
  };
}

/** Validate a Cron update while preserving the server-owned job identity. */
export function validateOperationsCronUpdateInput(value: unknown): CronUpdateInput {
  const input = record(value, "Cron update request");
  return {
    ...validateOperationsCronCreateInput(input),
    jobId: validateIdentifier(input.jobId, "Cron job id"),
  };
}

/** Validate a complete persisted heartbeat policy. */
export function validateHeartbeatConfiguration(value: unknown): HeartbeatConfiguration {
  const input = record(value, "Heartbeat configuration");
  const activeHours = record(input.activeHours, "Heartbeat active hours");
  if (typeof input.enabled !== "boolean") throw new TypeError("Heartbeat enabled must be a boolean.");
  const everySeconds = Number(input.everySeconds);
  if (!Number.isInteger(everySeconds) || everySeconds < 30 || everySeconds > 604_800) {
    throw new TypeError("Heartbeat interval must be between 30 and 604800 seconds.");
  }
  const start = optionalText(activeHours.start, "Heartbeat start time", 5);
  const end = optionalText(activeHours.end, "Heartbeat end time", 5);
  if ((start === null) !== (end === null)) throw new TypeError("Heartbeat active hours require start and end.");
  if ((start && !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(start)) || (end && !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(end))) {
    throw new TypeError("Heartbeat active hours must use HH:MM.");
  }
  return {
    enabled: input.enabled,
    everySeconds,
    prompt: string(input.prompt, "Heartbeat prompt", 4_000),
    activeHours: {
      start,
      end,
      timezone: string(activeHours.timezone, "Heartbeat timezone", 128),
    },
  };
}

/** Validate a provider id before using it in a Node Action. */
export function validateProviderId(value: unknown): string {
  const providerId = string(value, "Provider id", 63);
  if (!/^[a-z][a-z0-9_]*$/.test(providerId)) {
    throw new TypeError("Provider id is not valid.");
  }
  return providerId;
}

/** Restrict renderer-requested external navigation to public HTTPS pages. */
export function validateExternalUrl(value: unknown): string {
  const raw = string(value, "External URL", 2_048);
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new TypeError("External URL is not valid.");
  }
  if (parsed.protocol !== "https:" || !parsed.hostname) {
    throw new TypeError("External URL must use HTTPS.");
  }
  return parsed.toString();
}

/** Validate the bounded first-turn prompt crossing the IPC boundary. */
export function validateSetupHelloText(value: unknown): string {
  return string(value, "Setup Hello", 2_000);
}

function resourceName(value: unknown, label: string): string {
  const candidate = string(value, label, 63);
  if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(candidate)) {
    throw new TypeError(`${label} must be a lowercase resource name.`);
  }
  return candidate;
}

/** Validate a Model Profile resource identifier crossing the IPC boundary. */
export function validateModelProfileId(value: unknown): string {
  return resourceName(value, "Model Profile id");
}

const MODEL_CAPABILITIES = new Set<ModelCapability>([
  "text",
  "vision",
  "audio_input",
  "audio_output",
  "tool_calling",
  "structured_output",
  "reasoning",
  "long_context",
]);

function optionalText(value: unknown, label: string, maxLength: number): string | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  return string(value, label, maxLength);
}

function optionalPositiveInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  if (!Number.isInteger(value) || Number(value) < 1) {
    throw new TypeError(`${label} must be a positive integer.`);
  }
  return Number(value);
}

function optionalDecimalText(value: unknown, label: string): string | null {
  const candidate = optionalText(value, label, 64);
  if (candidate === null) {
    return null;
  }
  if (!/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(candidate)) {
    throw new TypeError(`${label} must be a non-negative decimal.`);
  }
  return candidate;
}

/** Reconstruct mutable Model Profile fields at the Renderer/Main trust boundary. */
function validateModelProfileWriteInput(value: unknown, label: string): ModelProfileCreateInput {
  const input = record(value, label);
  if (input.executionLocation !== "local" && input.executionLocation !== "remote") {
    throw new TypeError("Model execution location must be local or remote.");
  }
  if (typeof input.enabled !== "boolean") {
    throw new TypeError("Model Profile enabled must be a boolean.");
  }
  if (!Array.isArray(input.capabilities)) {
    throw new TypeError("Model capabilities must be an array.");
  }
  const capabilities = input.capabilities.map((item) => {
    if (typeof item !== "string" || !MODEL_CAPABILITIES.has(item as ModelCapability)) {
      throw new TypeError("Model capability is not supported.");
    }
    return item as ModelCapability;
  });
  if (new Set(capabilities).size !== capabilities.length) {
    throw new TypeError("Model capabilities must be unique.");
  }
  if (!Array.isArray(input.fallbackProfileIds)) {
    throw new TypeError("Fallback Model Profiles must be an array.");
  }
  const fallbackProfileIds = input.fallbackProfileIds.map((item) => resourceName(item, "Fallback Model Profile id"));
  if (new Set(fallbackProfileIds).size !== fallbackProfileIds.length) {
    throw new TypeError("Fallback Model Profiles must be unique.");
  }
  return {
    displayName: string(input.displayName, "Model Profile name", 80).trim(),
    providerId: validateProviderId(input.providerId),
    model: string(input.model, "Model", 256),
    executionLocation: input.executionLocation,
    apiBase: optionalText(input.apiBase, "API Base URL", 2_048),
    capabilities,
    contextWindowTokens: optionalPositiveInteger(input.contextWindowTokens, "Context window"),
    inputCostPerMillionUsd: optionalDecimalText(input.inputCostPerMillionUsd, "Input cost"),
    outputCostPerMillionUsd: optionalDecimalText(input.outputCostPerMillionUsd, "Output cost"),
    fallbackProfileIds,
    enabled: input.enabled,
    apiKey: optionalText(input.apiKey, "API key", 16_384),
  };
}

/** Validate a create request without accepting a Renderer-selected resource ID. */
export function validateModelProfileCreateInput(value: unknown): ModelProfileCreateInput {
  return validateModelProfileWriteInput(value, "Model Profile create request");
}

/** Validate an update request with immutable identity and optimistic concurrency. */
export function validateModelProfileUpdateInput(value: unknown): ModelProfileUpdateInput {
  const input = record(value, "Model Profile update request");
  const expectedRevision = revision(input.expectedRevision, "Expected Model Profile revision");
  if (expectedRevision === null) {
    throw new TypeError("Expected Model Profile revision is required.");
  }
  return {
    ...validateModelProfileWriteInput(input, "Model Profile update request"),
    profileId: resourceName(input.profileId, "Model Profile id"),
    expectedRevision,
  };
}

/** Validate Agent creation fields while keeping owner identity out of Renderer control. */
export function validateAgentCreateRequest(value: unknown): AgentCreateRequest {
  const input = record(value, "Agent creation request");
  if (input.privilegeLevel !== "low" && input.privilegeLevel !== "medium" && input.privilegeLevel !== "high" && input.privilegeLevel !== "root") {
    throw new TypeError("Agent privilege level is not supported.");
  }
  const workspace = input.workspace === null || input.workspace === undefined || input.workspace === ""
    ? null
    : string(input.workspace, "Agent workspace", 1_024);
  return {
    agentId: resourceName(input.agentId, "Agent id"),
    displayName: string(input.displayName, "Agent display name", 80),
    workspace,
    privilegeLevel: input.privilegeLevel,
    modelProfileId: resourceName(input.modelProfileId, "Model Profile id"),
    instruction: string(input.instruction ?? "", "Agent instruction", 16_384, true),
  };
}

/** Validate editable Agent policy under an optimistic revision precondition. */
export function validateAgentUpdateInput(value: unknown): AgentUpdateInput {
  const input = record(value, "Agent update request");
  if (input.privilegeLevel !== "low" && input.privilegeLevel !== "medium" && input.privilegeLevel !== "high" && input.privilegeLevel !== "root") {
    throw new TypeError("Agent privilege level is not supported.");
  }
  const expectedRevision = revision(input.expectedRevision, "Expected Agent revision");
  if (!expectedRevision) throw new TypeError("Expected Agent revision is required.");
  return {
    agentId: resourceName(input.agentId, "Agent id"),
    displayName: string(input.displayName, "Agent display name", 80),
    workspace: string(input.workspace, "Agent workspace", 1_024),
    instruction: string(input.instruction ?? "", "Agent instruction", 16_384, true),
    privilegeLevel: input.privilegeLevel,
    modelProfileId: resourceName(input.modelProfileId, "Model Profile id"),
    expectedRevision,
  };
}

/** Validate one Session lifecycle target. */
export function validateSessionMutationRequest(value: unknown): SessionMutationRequest {
  const input = record(value, "Session mutation request");
  return {
    agentId: resourceName(input.agentId, "Agent id"),
    sessionId: validateIdentifier(input.sessionId, "Session id"),
  };
}

/** Validate one bounded Session title. */
export function validateSessionRenameRequest(value: unknown): SessionMutationRequest & { title: string } {
  const input = record(value, "Session rename request");
  return {
    ...validateSessionMutationRequest(input),
    title: string(input.title, "Session title", 120).trim(),
  };
}

/** Validate archive state at the process isolation boundary. */
export function validateSessionArchiveRequest(value: unknown): SessionMutationRequest & { archived: boolean } {
  const input = record(value, "Session archive request");
  if (typeof input.archived !== "boolean") throw new TypeError("Session archived must be a boolean.");
  return { ...validateSessionMutationRequest(input), archived: input.archived };
}

function revision(value: unknown, label: string): string | null {
  if (value === null) return null;
  return string(value, label, 512);
}

function stringList(value: unknown, label: string, maxItems: number, maxLength = 128): string[] {
  if (!Array.isArray(value) || value.length > maxItems) {
    throw new TypeError(`${label} must be an array with at most ${maxItems} entries.`);
  }
  const items = value.map((item) => string(item, label, maxLength, true));
  if (new Set(items).size !== items.length) {
    throw new TypeError(`${label} entries must be unique.`);
  }
  return items;
}

function stringMap(value: unknown, label: string, maxItems: number, maxValueLength: number): Record<string, string> {
  const input = record(value, label);
  const entries = Object.entries(input);
  if (entries.length > maxItems) {
    throw new TypeError(`${label} has too many entries.`);
  }
  return Object.fromEntries(entries.map(([key, item]) => [
    string(key, `${label} key`, 128),
    string(item, `${label} value`, maxValueLength, true),
  ]));
}

function validateMcpBinding(value: unknown, label: string): McpValueBinding {
  const input = record(value, label);
  if (input.kind === "literal") {
    return { kind: "literal", value: string(input.value, `${label} literal`, 4_096, true) };
  }
  if (input.kind === "environment") {
    const name = string(input.name, `${label} environment name`, 128);
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(name)) {
      throw new TypeError(`${label} environment name is invalid.`);
    }
    return {
      kind: "environment",
      name,
      prefix: string(input.prefix ?? "", `${label} prefix`, 128, true),
      suffix: string(input.suffix ?? "", `${label} suffix`, 128, true),
    };
  }
  if (input.kind !== "secret") {
    throw new TypeError(`${label} kind must be literal, environment, or secret.`);
  }
  const secretRef = record(input.secretRef, `${label} SecretRef`);
  if (secretRef.store !== "system") {
    throw new TypeError(`${label} SecretRef store must be system.`);
  }
  return {
    kind: "secret",
    secretRef: { store: "system", name: resourceName(secretRef.name, `${label} SecretRef name`) },
    prefix: string(input.prefix ?? "", `${label} prefix`, 128, true),
    suffix: string(input.suffix ?? "", `${label} suffix`, 128, true),
  };
}

function validateMcpBindings(value: unknown, label: string): Record<string, McpValueBinding> {
  const input = record(value, label);
  const entries = Object.entries(input);
  if (entries.length > 64) {
    throw new TypeError(`${label} has too many entries.`);
  }
  return Object.fromEntries(entries.map(([key, item]) => [
    string(key, `${label} key`, 128),
    validateMcpBinding(item, `${label} ${key}`),
  ]));
}

function nullableBoundedJsonRecord(value: unknown, label: string): Record<string, unknown> | null {
  if (value === null || value === undefined) return null;
  const input = record(value, label);
  const encoded = JSON.stringify(input);
  if (encoded.length > 65_536) {
    throw new TypeError(`${label} exceeds the allowed size.`);
  }
  return structuredClone(input);
}

/** Validate one source-backed Skill or Plugin preview request. */
export function validateExtensionPreviewRequest(value: unknown): ExtensionPreviewRequest {
  const input = record(value, "Extension preview request");
  if (input.kind !== "plugin" && input.kind !== "skill") {
    throw new TypeError("Installable Extension kind must be plugin or skill.");
  }
  const source = record(input.source, "Extension source");
  if (!["builtin", "local_directory", "local_archive", "git", "npm", "catalog"].includes(String(source.type))) {
    throw new TypeError("Extension source type is not supported.");
  }
  return {
    kind: input.kind,
    source: {
      type: source.type as ExtensionPreviewRequest["source"]["type"],
      locator: string(source.locator, "Extension source locator", 4_096),
      ...(source.version ? { version: string(source.version, "Extension source version", 128) } : {}),
      ...(source.revision ? { revision: string(source.revision, "Extension source revision", 128) } : {}),
      ...(source.provider ? { provider: string(source.provider, "Extension source provider", 63) } : {}),
      ...(source.subpath ? { subpath: string(source.subpath, "Extension source subpath", 256) } : {}),
    },
  };
}

/** Validate a Plugin Marketplace create or update request. */
export function validatePluginMarketplaceSaveRequest(value: unknown): {
  marketplaceId: string;
  spec: PluginMarketplaceSourceSpec;
  expectedRevision: string | null;
} {
  const input = record(value, "Plugin Marketplace request");
  const spec = record(input.spec, "Plugin Marketplace source");
  if (spec.type !== "local" && spec.type !== "git") {
    throw new TypeError("Plugin Marketplace source type must be local or git.");
  }
  return {
    marketplaceId: resourceName(input.marketplaceId, "Plugin Marketplace id"),
    spec: {
      displayName: string(spec.displayName, "Plugin Marketplace name", 80),
      type: spec.type,
      locator: string(spec.locator, "Plugin Marketplace locator", 2_048),
      ref: string(spec.ref ?? "HEAD", "Plugin Marketplace Git ref", 256),
    },
    expectedRevision: revision(input.expectedRevision, "Expected Plugin Marketplace revision"),
  };
}

/** Validate a confirmed source-backed Extension installation. */
export function validateExtensionInstallRequest(value: unknown): ExtensionInstallRequest {
  const input = record(value, "Extension install request");
  const preview = validateExtensionPreviewRequest(input);
  const expectedDigest = string(input.expectedDigest, "Expected Extension digest", 128);
  if (!/^sha256:[0-9a-f]{64}$/.test(expectedDigest)) {
    throw new TypeError("Expected Extension digest is not valid.");
  }
  return {
    ...preview,
    expectedDigest,
    expectedRevision: revision(input.expectedRevision, "Expected Extension revision"),
  };
}

/** Validate one direct Extension removal request. */
export function validateExtensionRemoveRequest(value: unknown): ExtensionRemoveRequest {
  const input = record(value, "Extension removal request");
  if (input.kind !== "plugin" && input.kind !== "mcp" && input.kind !== "skill") {
    throw new TypeError("Removable Extension kind must be plugin, mcp, or skill.");
  }
  return {
    kind: input.kind,
    extensionId: resourceName(input.extensionId, "Extension id"),
    expectedRevision: string(input.expectedRevision, "Expected Extension revision", 512),
  };
}

/** Reconstruct one direct MCP resource at the Renderer/Main trust boundary. */
export function validateMcpMutationRequest(value: unknown): McpMutationRequest {
  const input = record(value, "MCP mutation request");
  const rawResource = record(input.resource, "MCP resource");
  const metadata = record(rawResource.metadata, "MCP metadata");
  const spec = record(rawResource.spec, "MCP spec");
  const transport = record(spec.transport, "MCP transport");
  const policy = record(spec.policy, "MCP policy");
  const presentation = record(spec.presentation, "MCP presentation");
  const icon = string(presentation.icon, "MCP icon", 63);
  if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(icon)) {
    throw new TypeError("MCP icon must be a lowercase identifier.");
  }
  const brandColor = presentation.brandColor;
  if (brandColor !== null && (typeof brandColor !== "string" || !/^#[0-9A-Fa-f]{6}$/.test(brandColor))) {
    throw new TypeError("MCP brand color must be a six-digit hex color or null.");
  }
  let safeTransport: McpServerResource["spec"]["transport"];
  if (transport.type === "stdio") {
    safeTransport = {
      type: "stdio",
      command: string(transport.command, "MCP command", 4_096),
      args: stringList(transport.args ?? [], "MCP arguments", 128, 4_096),
      cwd: optionalText(transport.cwd, "MCP working directory", 4_096),
      environment: validateMcpBindings(transport.environment ?? {}, "MCP environment"),
    };
  } else if (transport.type === "streamable_http" || transport.type === "sse") {
    if (transport.auth !== undefined && transport.auth !== "none" && transport.auth !== "oauth") {
      throw new TypeError("MCP authentication mode is not supported.");
    }
    safeTransport = {
      type: transport.type,
      url: string(transport.url, "MCP URL", 2_048),
      headers: validateMcpBindings(transport.headers ?? {}, "MCP headers"),
      query: validateMcpBindings(transport.query ?? {}, "MCP query parameters"),
      auth: transport.auth === "oauth" ? "oauth" : "none",
    };
  } else {
    throw new TypeError("MCP transport type is not supported.");
  }
  const risk = spec.risk;
  if (risk !== "low" && risk !== "medium" && risk !== "high") {
    throw new TypeError("MCP risk must be low, medium, or high.");
  }
  if (typeof policy.requireConfirmation !== "boolean" || typeof policy.progressEvents !== "boolean" || typeof policy.longTaskProxy !== "boolean") {
    throw new TypeError("MCP policy flags must be boolean values.");
  }
  const inlineBudgetMs = Number(policy.inlineBudgetMs);
  if (!Number.isInteger(inlineBudgetMs) || inlineBudgetMs < 100 || inlineBudgetMs > 60_000) {
    throw new TypeError("MCP inline budget must be between 100 and 60000 milliseconds.");
  }
  const resource: McpServerResource = {
    apiVersion: "openppx.io/v1alpha1",
    kind: "McpServer",
    metadata: {
      name: resourceName(metadata.name, "MCP id"),
      labels: stringMap(metadata.labels ?? {}, "MCP labels", 64, 128),
      annotations: stringMap(metadata.annotations ?? {}, "MCP annotations", 64, 2_048),
    },
    spec: {
      displayName: string(spec.displayName, "MCP display name", 80),
      description: string(spec.description, "MCP description", 2_048),
      presentation: { icon, brandColor },
      transport: safeTransport,
      policy: {
        toolFilter: stringList(policy.toolFilter ?? [], "MCP tool filter", 256),
        toolNamePrefix: optionalText(policy.toolNamePrefix, "MCP tool prefix", 128),
        requireConfirmation: policy.requireConfirmation,
        runtimeHeaders: stringMap(policy.runtimeHeaders ?? {}, "MCP runtime headers", 32, 256),
        progressEvents: policy.progressEvents,
        longTaskProxy: policy.longTaskProxy,
        inlineBudgetMs,
        jobProtocol: nullableBoundedJsonRecord(policy.jobProtocol, "MCP job protocol"),
      },
      risk,
      enabledAgentIds: stringList(spec.enabledAgentIds ?? [], "MCP enabled Agents", 256, 63).map((item) => resourceName(item, "MCP Agent id")),
      managedBy: null,
    },
  };
  return {
    resource,
    secretValues: stringMap(input.secretValues ?? {}, "MCP secret values", 64, 16_384),
    expectedRevision: revision(input.expectedRevision, "Expected MCP revision"),
  };
}

/** Validate App Connection policy plus write-only credential values. */
export function validateAppConnectionSaveRequest(value: unknown): AppConnectionSaveRequest {
  const input = record(value, "App Connection save request");
  if (typeof input.requireConfirmation !== "boolean") {
    throw new TypeError("App Connection confirmation policy must be a boolean.");
  }
  const enabledTools = input.enabledTools === null
    ? null
    : stringList(input.enabledTools, "App Connection tools", 256).map((item) => string(item, "App tool", 128));
  return {
    appId: resourceName(input.appId, "App id"),
    connectionId: resourceName(input.connectionId, "App Connection id"),
    displayName: string(input.displayName, "App Connection display name", 80),
    enabledTools,
    requireConfirmation: input.requireConfirmation,
    credentialValues: stringMap(input.credentialValues ?? {}, "App credential values", 64, 16_384),
    expectedRevision: revision(input.expectedRevision, "Expected App Connection revision"),
  };
}

/** Validate App Connection Agent enablement. */
export function validateAppConnectionEnablementRequest(value: unknown): AppConnectionEnablementRequest {
  const input = record(value, "App Connection enablement request");
  if (typeof input.enabled !== "boolean") {
    throw new TypeError("App Connection enabled must be a boolean.");
  }
  return {
    connectionId: resourceName(input.connectionId, "App Connection id"),
    agentId: resourceName(input.agentId, "Agent id"),
    expectedRevision: string(input.expectedRevision, "Expected App Connection revision", 512),
    enabled: input.enabled,
  };
}

/** Validate App Connection removal. */
export function validateAppConnectionRemoveRequest(value: unknown): AppConnectionRemoveRequest {
  const input = record(value, "App Connection removal request");
  return {
    connectionId: resourceName(input.connectionId, "App Connection id"),
    expectedRevision: string(input.expectedRevision, "Expected App Connection revision", 512),
  };
}

/** Validate one complete setup baseline crossing the isolated Renderer boundary. */
export function validateSetupApplyRequest(value: unknown): SetupApplyRequest {
  const input = record(value, "Setup request");
  const node = record(input.node, "Setup Node");
  const nodeMetadata = record(node.metadata, "Setup Node metadata");
  const nodeSpec = record(node.spec, "Setup Node spec");
  const clientApi = record(nodeSpec.clientApi, "Setup Client API");
  const agent = record(input.agent, "Setup Agent");
  const agentMetadata = record(agent.metadata, "Setup Agent metadata");
  const agentSpec = record(agent.spec, "Setup Agent spec");
  const modelPolicy = record(agentSpec.modelPolicy, "Setup Agent model policy");
  const profile = record(input.profile, "Setup Model Profile");
  const profileMetadata = record(profile.metadata, "Setup Model Profile metadata");
  const profileSpec = record(profile.spec, "Setup Model Profile spec");
  const expected = record(input.expectedRevisions, "Setup expected revisions");
  const port = clientApi.port;
  if (!Number.isInteger(port) || Number(port) < 1 || Number(port) > 65_535) {
    throw new TypeError("Setup Client API port must be an integer from 1 to 65535.");
  }
  if (clientApi.authentication !== "required" && clientApi.authentication !== "disabled") {
    throw new TypeError("Setup authentication must be required or disabled.");
  }
  if (agentSpec.privilegeLevel !== "low" && agentSpec.privilegeLevel !== "medium" && agentSpec.privilegeLevel !== "high" && agentSpec.privilegeLevel !== "root") {
    throw new TypeError("Setup privilege level is not supported.");
  }
  if (profileSpec.executionLocation !== "local" && profileSpec.executionLocation !== "remote") {
    throw new TypeError("Setup execution location must be local or remote.");
  }
  if (!Array.isArray(profileSpec.capabilities)) {
    throw new TypeError("Setup Model capabilities must be an array.");
  }
  const credential = profileSpec.credential === undefined
    ? undefined
    : record(profileSpec.credential, "Setup credential reference");
  const secret = input.secret === null
    ? null
    : record(input.secret, "Setup Secret");
  const secretRef = secret ? record(secret.ref, "Setup Secret reference") : null;
  const agentId = resourceName(agentMetadata.name, "Agent id");
  const profileId = resourceName(profileMetadata.name, "Model Profile id");
  return {
    node: {
      apiVersion: "openppx.io/v1alpha1",
      kind: "NodeConfig",
      metadata: { name: resourceName(nodeMetadata.name, "Node id") },
      spec: {
        displayName: string(nodeSpec.displayName, "Node display name", 80),
        enabledAgents: [agentId],
        clientApi: {
          listenHost: string(clientApi.listenHost, "Client API host", 253),
          port: Number(port),
          authentication: clientApi.authentication,
        },
      },
    },
    agent: {
      apiVersion: "openppx.io/v1alpha1",
      kind: "AgentConfig",
      metadata: { name: agentId },
      spec: {
        displayName: string(agentSpec.displayName, "Agent display name", 80),
        workspace: string(agentSpec.workspace, "Agent workspace", 1_024),
        ownerPrincipalId: string(agentSpec.ownerPrincipalId, "Agent owner", 128),
        privilegeLevel: agentSpec.privilegeLevel,
        modelPolicy: { defaultProfile: resourceName(modelPolicy.defaultProfile, "Default Model Profile") },
      },
    },
    profile: {
      apiVersion: "openppx.io/v1alpha1",
      kind: "ModelProfile",
      metadata: { name: profileId },
      spec: {
        displayName: string(profileSpec.displayName, "Model Profile name", 80).trim(),
        provider: string(profileSpec.provider, "Model provider", 63),
        model: string(profileSpec.model, "Model", 256),
        ...(credential
          ? { credential: { store: "system" as const, name: resourceName(credential.name, "Credential name") } }
          : {}),
        executionLocation: profileSpec.executionLocation,
        capabilities: profileSpec.capabilities.map((item) => string(item, "Model capability", 63)),
      },
    },
    secret: secret && secretRef
      ? {
          ref: { store: "system", name: resourceName(secretRef.name, "Secret name") },
          value: string(secret.value, "Secret value", 65_536),
        }
      : null,
    expectedRevisions: {
      node: revision(expected.node, "Expected Node revision"),
      agent: revision(expected.agent, "Expected Agent revision"),
      profile: revision(expected.profile, "Expected Model Profile revision"),
    },
  };
}

/** Validate a message request before passing it to the local or remote Node. */
export function validateSendMessageInput(value: unknown): SendMessageInput {
  const input = record(value, "Send message input");
  const rawReferences = input.artifactRefs;
  if (rawReferences === undefined) {
    return {
      agentId: validateIdentifier(input.agentId, "Agent id"),
      sessionId: validateIdentifier(input.sessionId, "Session id"),
      text: string(input.text, "Message text", 1_000_000, true),
    };
  }
  if (!Array.isArray(rawReferences) || rawReferences.length > 10) {
    throw new TypeError("Artifact references must be an array with at most 10 items.");
  }
  return {
    agentId: validateIdentifier(input.agentId, "Agent id"),
    sessionId: validateIdentifier(input.sessionId, "Session id"),
    text: string(input.text, "Message text", 1_000_000, true),
    artifactRefs: rawReferences.map((value) => {
      const reference = record(value, "Artifact reference");
      const version = integer(reference.version, "Artifact version", 0, Number.MAX_SAFE_INTEGER);
      return { key: string(reference.key, "Artifact key", 512), version };
    }),
  };
}

/** Validate a renderer-provided file payload before the main process uploads it. */
export function validateArtifactUploadInput(value: unknown): ArtifactUploadInput {
  const input = record(value, "Artifact upload");
  return {
    agentId: validateIdentifier(input.agentId, "Agent id"),
    sessionId: validateIdentifier(input.sessionId, "Session id"),
    fileName: string(input.fileName, "Artifact filename", 255),
    mimeType: string(input.mimeType, "Artifact mime type", 127),
    dataBase64: string(input.dataBase64, "Artifact content", 28_000_000),
  };
}

/** Validate the opaque Artifact reference used for an authorized download. */
export function validateArtifactSummaryInput(value: unknown): ArtifactSummary {
  const input = record(value, "Artifact");
  return {
    id: string(input.id, "Artifact id", 512),
    key: string(input.key, "Artifact key", 512),
    fileName: string(input.fileName, "Artifact filename", 255),
    mimeType: string(input.mimeType, "Artifact mime type", 127),
    sizeBytes: integer(input.sizeBytes, "Artifact size", 0, 20 * 1024 * 1024),
    version: integer(input.version, "Artifact version", 0, Number.MAX_SAFE_INTEGER),
    source: string(input.source, "Artifact source", 63),
    createdAt: string(input.createdAt, "Artifact created time", 128),
  };
}

/** Validate one command plus optional resource context crossing the IPC boundary. */
export function validateSlashCommandRequest(value: unknown): SlashCommandRequest {
  const input = record(value, "Slash command input");
  const optionalIdentifier = (item: unknown, label: string): string | null => {
    if (item === undefined || item === null || item === "") {
      return null;
    }
    return validateIdentifier(item, label);
  };
  const rawCommand = string(input.rawCommand, "Slash command", 512);
  if (!rawCommand.trim().startsWith("/")) {
    throw new TypeError("Slash command must start with '/'.");
  }
  return {
    rawCommand,
    agentId: optionalIdentifier(input.agentId, "Agent id"),
    sessionId: optionalIdentifier(input.sessionId, "Session id"),
    runId: optionalIdentifier(input.runId, "Run id"),
  };
}

/** Validate one Extension enablement request crossing the IPC boundary. */
export function validateExtensionEnablement(value: unknown): ExtensionEnablementRequest {
  const input = record(value, "Extension enablement");
  if (input.kind !== "plugin" && input.kind !== "mcp" && input.kind !== "skill") {
    throw new TypeError("Extension kind must be plugin, mcp, or skill.");
  }
  if (typeof input.enabled !== "boolean") {
    throw new TypeError("Extension enabled must be a boolean.");
  }
  return {
    kind: input.kind,
    extensionId: validateIdentifier(input.extensionId, "Extension id"),
    agentId: validateIdentifier(input.agentId, "Agent id"),
    expectedRevision: string(input.expectedRevision, "Expected revision", 512),
    enabled: input.enabled,
  };
}

/** Validate one of the four stable Extension kinds. */
export function validateExtensionKind(value: unknown): "plugin" | "app" | "mcp" | "skill" {
  if (value !== "plugin" && value !== "app" && value !== "mcp" && value !== "skill") {
    throw new TypeError("Extension kind is not supported.");
  }
  return value;
}

/** Validate one Extension resource kind that supports durable live-probe history. */
export function validateExtensionHealthKind(value: unknown): "mcp" | "app_connection" {
  if (value !== "mcp" && value !== "app_connection") {
    throw new TypeError("Extension health kind is not supported.");
  }
  return value;
}

/** Validate the bounded number of credential-free probe observations returned to Renderer. */
export function validateExtensionHealthLimit(value: unknown): number {
  return integer(value, "Extension health history limit", 1, 50);
}

/** Validate one bounded catalog search without accepting structured input. */
export function validateSearchQuery(value: unknown): string {
  return string(value, "Search query", 256, true);
}
