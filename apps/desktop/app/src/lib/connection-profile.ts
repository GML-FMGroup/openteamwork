import type { ConnectionSettings } from "../types";

export interface StoredConnectionSettings {
  schemaVersion: 1;
  targetType: "local" | "lan";
  targetId: string;
  targetName: string;
  clientApiBaseUrl: string;
  secretRef?: string;
}

interface BoundConnectionCredential {
  schemaVersion: 1;
  clientApiBaseUrl: string;
  accessToken: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function normalizeAccessToken(value: string | undefined): string {
  const accessToken = value?.trim() || "";
  if (/\s/.test(accessToken)) {
    throw new Error("Client API Token 不能包含空白字符。");
  }
  return accessToken;
}

/** Return whether a URL hostname is restricted to this machine. */
export function isLoopbackClientApiHostname(hostname: string): boolean {
  const normalized = hostname.trim().toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (normalized === "localhost" || normalized === "::1") {
    return true;
  }
  const octets = normalized.split(".");
  return (
    octets.length === 4 &&
    octets[0] === "127" &&
    octets.every((octet) => /^\d+$/.test(octet) && Number(octet) >= 0 && Number(octet) <= 255)
  );
}

/** Validate a Client API origin without allowing credentials or URL suffixes. */
export function normalizeClientApiBaseUrl(rawValue: string, targetType: "local" | "lan"): string {
  const rawBaseUrl = rawValue.trim() || "http://127.0.0.1:8765";
  const parsedBaseUrl = new URL(rawBaseUrl);
  if (!["http:", "https:"].includes(parsedBaseUrl.protocol)) {
    throw new Error("Gateway URL 必须使用 http:// 或 https://。");
  }
  if (parsedBaseUrl.username || parsedBaseUrl.password || parsedBaseUrl.search || parsedBaseUrl.hash) {
    throw new Error("Gateway URL 不能包含账号、密码、查询参数或锚点。");
  }
  if (parsedBaseUrl.pathname !== "/") {
    throw new Error("Gateway URL 只能填写协议、主机和端口，不能包含路径。");
  }
  if (targetType === "lan" && !parsedBaseUrl.port) {
    throw new Error("局域网 Gateway URL 必须显式填写端口。");
  }
  if (targetType === "local" && !isLoopbackClientApiHostname(parsedBaseUrl.hostname)) {
    throw new Error("本机模式只能连接 localhost 或回环 IP；其他机器请使用局域网模式。");
  }
  return parsedBaseUrl.origin;
}

/** Validate and normalize settings at both the Renderer and Main trust boundaries. */
export function normalizeConnectionSettings(settings: ConnectionSettings): ConnectionSettings {
  if (settings.targetType !== "local" && settings.targetType !== "lan") {
    throw new Error("运行位置必须是本机或局域网节点。");
  }
  const targetType = settings.targetType;
  const targetName = settings.targetName.trim() || (targetType === "lan" ? "LAN OpenPPX Node" : "This Mac");
  const normalizedName =
    targetName
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "default";
  const existingId = settings.targetId.trim();
  const targetId =
    !existingId ||
    existingId === "local-default" ||
    existingId === "remote-default" ||
    existingId === "lan-default" ||
    !existingId.startsWith(`${targetType}-`)
      ? `${targetType}-${normalizedName}`
      : existingId;
  return {
    targetType,
    targetId,
    targetName,
    clientApiBaseUrl: normalizeClientApiBaseUrl(settings.clientApiBaseUrl, targetType),
    accessToken: normalizeAccessToken(settings.accessToken),
  };
}

/** Serialize a credential payload before Electron safeStorage encryption. */
export function serializeBoundConnectionCredential(clientApiBaseUrl: string, accessToken: string): string {
  const normalizedToken = normalizeAccessToken(accessToken);
  if (!normalizedToken) {
    throw new Error("A Client API token is required for a LAN connection.");
  }
  const payload: BoundConnectionCredential = {
    schemaVersion: 1,
    clientApiBaseUrl: normalizeClientApiBaseUrl(clientApiBaseUrl, "lan"),
    accessToken: normalizedToken,
  };
  return JSON.stringify(payload);
}

/** Read an encrypted credential only when it is bound to the expected LAN endpoint. */
export function parseBoundConnectionCredential(payload: string, expectedBaseUrl: string): string {
  try {
    const record = asRecord(JSON.parse(payload));
    if (!record || record.schemaVersion !== 1 || typeof record.clientApiBaseUrl !== "string") {
      return "";
    }
    const accessToken = normalizeAccessToken(typeof record.accessToken === "string" ? record.accessToken : "");
    if (!accessToken) {
      return "";
    }
    const boundOrigin = normalizeClientApiBaseUrl(record.clientApiBaseUrl, "lan");
    const expectedOrigin = normalizeClientApiBaseUrl(expectedBaseUrl, "lan");
    return boundOrigin === expectedOrigin ? accessToken : "";
  } catch {
    return "";
  }
}

/** Decide whether a blank candidate may reuse the credential for the persisted LAN endpoint. */
export function canReuseStoredCredential(
  stored: StoredConnectionSettings | null,
  candidate: ConnectionSettings,
): boolean {
  if (!stored?.secretRef || stored.targetType !== "lan" || candidate.targetType !== "lan") {
    return false;
  }
  try {
    const storedOrigin = normalizeConnectionSettings(hydrateConnectionSettings(stored)).clientApiBaseUrl;
    const candidateOrigin = normalizeConnectionSettings(candidate).clientApiBaseUrl;
    return storedOrigin === candidateOrigin;
  } catch {
    return false;
  }
}

/** Normalize persisted settings and migrate the former `remote` mode to `lan`. */
export function parseStoredConnectionSettings(payload: unknown): StoredConnectionSettings | null {
  const record = asRecord(payload);
  if (!record) {
    return null;
  }
  const rawTargetType = String(record.targetType ?? "local").trim().toLowerCase();
  const targetType = rawTargetType === "lan" || rawTargetType === "remote" ? "lan" : "local";
  return {
    schemaVersion: 1,
    targetType,
    targetId: String(record.targetId ?? (targetType === "lan" ? "lan-default" : "local-default")),
    targetName: String(record.targetName ?? (targetType === "lan" ? "LAN OpenPPX Node" : "This Mac")),
    clientApiBaseUrl: String(record.clientApiBaseUrl ?? "http://127.0.0.1:8765"),
    secretRef: typeof record.secretRef === "string" && record.secretRef.trim() ? record.secretRef.trim() : undefined,
  };
}

/** Build the non-secret JSON representation of one connection profile. */
export function toStoredConnectionSettings(
  settings: ConnectionSettings,
  secretRef?: string,
): StoredConnectionSettings {
  return {
    schemaVersion: 1,
    targetType: settings.targetType,
    targetId: settings.targetId,
    targetName: settings.targetName,
    clientApiBaseUrl: settings.clientApiBaseUrl,
    secretRef: settings.targetType === "lan" ? secretRef : undefined,
  };
}

/** Hydrate Main-only settings with a decrypted credential for the adapter. */
export function hydrateConnectionSettings(
  stored: StoredConnectionSettings,
  accessToken = "",
): ConnectionSettings {
  return {
    targetType: stored.targetType,
    targetId: stored.targetId,
    targetName: stored.targetName,
    clientApiBaseUrl: stored.clientApiBaseUrl,
    accessToken,
  };
}
