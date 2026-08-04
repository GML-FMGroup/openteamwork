import type { AgentCreateRequest, ConnectionSettings, ExtensionEnablementRequest, RuntimeCommand, SendMessageInput, SetupApplyRequest, SlashCommandRequest } from "../../app/src/types";

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
  };
}

function revision(value: unknown, label: string): string | null {
  if (value === null) return null;
  return string(value, label, 512);
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
