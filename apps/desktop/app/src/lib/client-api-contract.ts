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
