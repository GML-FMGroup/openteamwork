import type { ConnectionSettings } from "../types";
import { productProfile } from "../../../product";

const DEFAULT_LOCAL_CLIENT_API_URL = `http://127.0.0.1:${productProfile.defaultClientApiPort}`;

export interface StoredConnectionSettings {
  schemaVersion: 1;
  targetType: "local" | "lan";
  targetId: string;
  targetName: string;
  clientApiBaseUrl: string;
  secretRef?: string;
  userId?: string;
  userEmail?: string;
  userPrivilegeLevel?: "low" | "medium" | "high" | "root";
}

export interface StoredConnectionProfileCollection {
  schemaVersion: 2;
  activeTargetId: string;
  items: StoredConnectionSettings[];
}

interface BoundConnectionCredential {
  schemaVersion: 2;
  clientApiBaseUrl: string;
  userId: string;
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
    throw new Error("Client API Token cannot contain whitespace.");
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

/** Normalize a login connection while deriving its internal location from the Node URL. */
export function normalizeLoginConnectionSettings(settings: ConnectionSettings): ConnectionSettings {
  const rawBaseUrl = settings.clientApiBaseUrl.trim() || DEFAULT_LOCAL_CLIENT_API_URL;
  const targetType = isLoopbackClientApiHostname(new URL(rawBaseUrl).hostname) ? "local" : "lan";
  const locationChanged = targetType !== settings.targetType;
  return normalizeConnectionSettings({
    ...settings,
    targetType,
    targetId: locationChanged ? `${targetType}-default` : settings.targetId,
    targetName: locationChanged ? (targetType === "local" ? "This Mac" : "Team Node") : settings.targetName,
    clientApiBaseUrl: rawBaseUrl,
    accessToken: "",
  });
}

/** Validate a Client API origin without allowing credentials or URL suffixes. */
export function normalizeClientApiBaseUrl(rawValue: string, targetType: "local" | "lan"): string {
  const rawBaseUrl = rawValue.trim() || DEFAULT_LOCAL_CLIENT_API_URL;
  const parsedBaseUrl = new URL(rawBaseUrl);
  if (!["http:", "https:"].includes(parsedBaseUrl.protocol)) {
    throw new Error("Node URL must use http:// or https://.");
  }
  if (parsedBaseUrl.username || parsedBaseUrl.password || parsedBaseUrl.search || parsedBaseUrl.hash) {
    throw new Error("Node URL cannot contain credentials, query parameters, or fragments.");
  }
  if (parsedBaseUrl.pathname !== "/") {
    throw new Error("Node URL can only contain the protocol, host, and port; paths are not allowed.");
  }
  if (targetType === "lan" && parsedBaseUrl.protocol === "http:" && !parsedBaseUrl.port) {
    throw new Error("A plaintext LAN Node URL must include an explicit port.");
  }
  if (targetType === "local" && !isLoopbackClientApiHostname(parsedBaseUrl.hostname)) {
    throw new Error("Local mode can only connect to localhost or a loopback IP. Use LAN mode for another machine.");
  }
  return parsedBaseUrl.origin;
}

/** Validate and normalize settings at both the Renderer and Main trust boundaries. */
export function normalizeConnectionSettings(settings: ConnectionSettings): ConnectionSettings {
  if (settings.targetType !== "local" && settings.targetType !== "lan") {
    throw new Error("Run location must be this computer or a LAN node.");
  }
  const targetType = settings.targetType;
  const targetName = settings.targetName.trim() || (targetType === "lan" ? `LAN ${productProfile.displayName} Node` : "This Mac");
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
    ...(settings.userId?.trim() ? { userId: settings.userId.trim() } : {}),
    ...(settings.userEmail?.trim() ? { userEmail: settings.userEmail.trim() } : {}),
    ...(settings.userPrivilegeLevel ? { userPrivilegeLevel: settings.userPrivilegeLevel } : {}),
  };
}

/** Serialize a credential payload before Electron safeStorage encryption. */
export function serializeBoundConnectionCredential(
  clientApiBaseUrl: string,
  accessToken: string,
  userId: string,
): string {
  const normalizedToken = normalizeAccessToken(accessToken);
  if (!normalizedToken) {
    throw new Error("An authenticated user session is required.");
  }
  const normalizedUserId = userId.trim();
  if (!normalizedUserId) {
    throw new Error("An authenticated user ID is required for a saved session.");
  }
  const payload: BoundConnectionCredential = {
    schemaVersion: 2,
    clientApiBaseUrl: new URL(clientApiBaseUrl).origin,
    userId: normalizedUserId,
    accessToken: normalizedToken,
  };
  return JSON.stringify(payload);
}

/** Read an encrypted credential only when it is bound to the expected LAN endpoint. */
export function parseBoundConnectionCredential(
  payload: string,
  expectedBaseUrl: string,
  expectedUserId: string,
): string {
  try {
    const record = asRecord(JSON.parse(payload));
    if (
      !record
      || record.schemaVersion !== 2
      || typeof record.clientApiBaseUrl !== "string"
      || record.userId !== expectedUserId
    ) {
      return "";
    }
    const accessToken = normalizeAccessToken(typeof record.accessToken === "string" ? record.accessToken : "");
    if (!accessToken) {
      return "";
    }
    const boundOrigin = new URL(record.clientApiBaseUrl).origin;
    const expectedOrigin = new URL(expectedBaseUrl).origin;
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
  if (!stored?.secretRef || stored.targetType !== candidate.targetType) {
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
    targetName: String(record.targetName ?? (targetType === "lan" ? `LAN ${productProfile.displayName} Node` : "This Mac")),
    clientApiBaseUrl: String(record.clientApiBaseUrl ?? DEFAULT_LOCAL_CLIENT_API_URL),
    secretRef: typeof record.secretRef === "string" && record.secretRef.trim() ? record.secretRef.trim() : undefined,
    userId: typeof record.userId === "string" && record.userId.trim() ? record.userId.trim() : undefined,
    userEmail: typeof record.userEmail === "string" && record.userEmail.trim() ? record.userEmail.trim() : undefined,
    userPrivilegeLevel: ["low", "medium", "high", "root"].includes(String(record.userPrivilegeLevel))
      ? record.userPrivilegeLevel as StoredConnectionSettings["userPrivilegeLevel"]
      : undefined,
  };
}

/** Parse the multi-target store while migrating the former single-profile file shape. */
export function parseStoredConnectionProfiles(payload: unknown): StoredConnectionProfileCollection | null {
  const record = asRecord(payload);
  if (!record) return null;
  if (record.schemaVersion === 2 && Array.isArray(record.items)) {
    const parsedItems = record.items
      .map((item) => parseStoredConnectionSettings(item))
      .filter((item): item is StoredConnectionSettings => item !== null);
    const items = parsedItems.filter(
      (item, index) => parsedItems.findIndex((candidate) => candidate.targetId === item.targetId) === index,
    );
    if (!items.length) return null;
    const requestedActiveId = String(record.activeTargetId ?? "");
    const activeTargetId = items.some((item) => item.targetId === requestedActiveId)
      ? requestedActiveId
      : items[0].targetId;
    return { schemaVersion: 2, activeTargetId, items };
  }
  const legacy = parseStoredConnectionSettings(record);
  return legacy
    ? { schemaVersion: 2, activeTargetId: legacy.targetId, items: [legacy] }
    : null;
}

/** Upsert one normalized profile and make it the active Desktop target. */
export function upsertStoredConnectionProfile(
  collection: StoredConnectionProfileCollection | null,
  profile: StoredConnectionSettings,
): StoredConnectionProfileCollection {
  const items = collection?.items ?? [];
  return {
    schemaVersion: 2,
    activeTargetId: profile.targetId,
    items: [profile, ...items.filter((item) => item.targetId !== profile.targetId)],
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
    secretRef,
    userId: settings.userId,
    userEmail: settings.userEmail,
    userPrivilegeLevel: settings.userPrivilegeLevel,
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
    userId: stored.userId,
    userEmail: stored.userEmail,
    userPrivilegeLevel: stored.userPrivilegeLevel,
  };
}
