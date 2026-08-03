export const CLIENT_API_SERVICE = "openppx-client-api";
export const CLIENT_API_PROTOCOL_VERSION = 1;

export type ClientApiCompatibility = "compatible" | "incompatible" | "unknown";

export interface ClientApiHandshake {
  service: string;
  productVersion: string;
  protocolVersion: number;
  ready: boolean;
  compatibility: Exclude<ClientApiCompatibility, "unknown">;
}

export interface ClientApiNodeInfo {
  nodeId: string;
  displayName: string;
  productVersion: string;
  protocolMin: number;
  protocolMax: number;
  capabilities: string[];
  agents: number;
  authenticationRequired: boolean;
  compatibility: Exclude<ClientApiCompatibility, "unknown">;
}

export class ClientApiProtocolError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "ClientApiProtocolError";
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

/** Parse a health response without treating a future protocol as compatible. */
export function parseClientApiHandshake(payload: unknown): ClientApiHandshake {
  const envelope = asRecord(payload);
  const data = asRecord(envelope?.data);
  if (envelope?.ok !== true || !data) {
    throw new ClientApiProtocolError("Client API health response is not a successful v1 envelope.");
  }

  const service = typeof data.service === "string" ? data.service.trim() : "";
  const productVersion = typeof data.product_version === "string" ? data.product_version.trim() : "";
  const protocolVersion = data.protocol_version;
  if (service !== CLIENT_API_SERVICE) {
    throw new ClientApiProtocolError(`Unexpected Client API service: ${service || "missing"}.`);
  }
  if (!productVersion) {
    throw new ClientApiProtocolError("Client API health response is missing product_version.");
  }
  if (!Number.isInteger(protocolVersion)) {
    throw new ClientApiProtocolError("Client API health response is missing an integer protocol_version.");
  }

  return {
    service,
    productVersion,
    protocolVersion: protocolVersion as number,
    ready: data.ready === true && data.state === "healthy",
    compatibility: protocolVersion === CLIENT_API_PROTOCOL_VERSION ? "compatible" : "incompatible",
  };
}

/** Parse authenticated Node metadata and evaluate its protocol range. */
export function parseClientApiNodeInfo(payload: unknown): ClientApiNodeInfo {
  const envelope = asRecord(payload);
  const data = asRecord(envelope?.data);
  const protocol = asRecord(data?.protocol);
  if (envelope?.ok !== true || !data || !protocol) {
    throw new ClientApiProtocolError("Client API Node response is not a successful v1 envelope.");
  }

  const nodeId = typeof data.node_id === "string" ? data.node_id.trim() : "";
  const displayName = typeof data.display_name === "string" ? data.display_name.trim() : "";
  const productVersion = typeof data.product_version === "string" ? data.product_version.trim() : "";
  const protocolMin = protocol.min;
  const protocolMax = protocol.max;
  if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(nodeId) || !displayName || !productVersion) {
    throw new ClientApiProtocolError("Client API Node response is missing identity metadata.");
  }
  if (!Number.isInteger(protocolMin) || !Number.isInteger(protocolMax)) {
    throw new ClientApiProtocolError("Client API Node response has an invalid protocol range.");
  }
  const protocolMinNumber = protocolMin as number;
  const protocolMaxNumber = protocolMax as number;

  return {
    nodeId,
    displayName,
    productVersion,
    protocolMin: protocolMinNumber,
    protocolMax: protocolMaxNumber,
    capabilities: Array.isArray(data.capabilities) ? data.capabilities.map((item) => String(item)) : [],
    agents: typeof data.agents === "number" ? data.agents : 0,
    authenticationRequired: data.authentication_required === true,
    compatibility:
      protocolMinNumber <= CLIENT_API_PROTOCOL_VERSION && protocolMaxNumber >= CLIENT_API_PROTOCOL_VERSION
        ? "compatible"
        : "incompatible",
  };
}
