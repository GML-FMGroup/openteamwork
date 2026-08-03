import type { ConnectionSettings, ExtensionEnablementRequest, RuntimeCommand, SendMessageInput, SlashCommandRequest } from "../../app/src/types";

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

/** Validate a message request before passing it to the local or remote Node. */
export function validateSendMessageInput(value: unknown): SendMessageInput {
  const input = record(value, "Send message input");
  return {
    agentId: validateIdentifier(input.agentId, "Agent id"),
    sessionId: validateIdentifier(input.sessionId, "Session id"),
    text: string(input.text, "Message text", 1_000_000),
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
