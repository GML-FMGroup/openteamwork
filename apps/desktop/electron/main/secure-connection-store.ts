import { app, safeStorage } from "electron";
import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import {
  canReuseStoredCredential,
  hydrateConnectionSettings,
  normalizeConnectionSettings,
  parseBoundConnectionCredential,
  parseStoredConnectionSettings,
  parseStoredConnectionProfiles,
  serializeBoundConnectionCredential,
  toStoredConnectionSettings,
  upsertStoredConnectionProfile,
  type StoredConnectionProfileCollection,
  type StoredConnectionSettings,
} from "../../app/src/lib/connection-profile";
import type { ConnectionSettings } from "../../app/src/types";

const LEGACY_CLIENT_API_SECRET_REF = "client-api-token-v1";

function settingsPath(): string {
  return path.join(app.getPath("userData"), "connection-settings.json");
}

function secretPath(secretRef: string): string {
  if (secretRef === LEGACY_CLIENT_API_SECRET_REF) {
    return path.join(app.getPath("userData"), "connection-secret.bin");
  }
  if (!/^client-api-token-v2-[a-f0-9]{20}$/.test(secretRef)) {
    throw new Error("Connection credential reference is invalid.");
  }
  return path.join(app.getPath("userData"), `${secretRef}.bin`);
}

function secureStorageAvailable(): boolean {
  if (!safeStorage.isEncryptionAvailable()) {
    return false;
  }
  return process.platform !== "linux" || safeStorage.getSelectedStorageBackend() !== "basic_text";
}

function atomicWrite(filePath: string, data: string | Buffer): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporaryPath = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  try {
    fs.writeFileSync(temporaryPath, data, { mode: 0o600 });
    fs.renameSync(temporaryPath, filePath);
  } finally {
    try {
      if (fs.existsSync(temporaryPath)) {
        fs.unlinkSync(temporaryPath);
      }
    } catch {
      // A stale temporary file contains either public JSON or safeStorage-encrypted bytes.
    }
  }
  try {
    fs.chmodSync(filePath, 0o600);
  } catch {
    // Some filesystems do not expose POSIX permissions; safeStorage still encrypts the secret.
  }
}

function readStoredProfiles(): StoredConnectionProfileCollection | null {
  try {
    const filePath = settingsPath();
    if (!fs.existsSync(filePath)) {
      return null;
    }
    return parseStoredConnectionProfiles(JSON.parse(fs.readFileSync(filePath, "utf-8")));
  } catch {
    return null;
  }
}

function readSecret(secretRef: string | undefined, clientApiBaseUrl: string, userId: string | undefined): string {
  if (!secretRef || !secureStorageAvailable()) {
    return "";
  }
  try {
    const filePath = secretPath(secretRef);
    if (!fs.existsSync(filePath)) {
      return "";
    }
    return parseBoundConnectionCredential(
      safeStorage.decryptString(fs.readFileSync(filePath)),
      clientApiBaseUrl,
      userId || "",
    );
  } catch {
    return "";
  }
}

function writeSecret(
  accessToken: string,
  clientApiBaseUrl: string,
  targetId: string,
  userId: string,
): string {
  if (!secureStorageAvailable()) {
    throw new Error("Secure credential storage is unavailable on this system.");
  }
  const secretRef = `client-api-token-v2-${createHash("sha256").update(`${targetId}:${randomUUID()}`).digest("hex").slice(0, 20)}`;
  atomicWrite(
    secretPath(secretRef),
    safeStorage.encryptString(serializeBoundConnectionCredential(clientApiBaseUrl, accessToken, userId)),
  );
  return secretRef;
}

function deleteSecret(secretRef: string | undefined): void {
  if (!secretRef) return;
  try {
    const filePath = secretPath(secretRef);
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
  } catch {
    // A stale encrypted blob is not referenced by local settings and cannot be returned to Renderer.
  }
}

/** Read public settings and hydrate the credential only inside Electron Main. */
export function readSecureConnectionSettings(): ConnectionSettings | null {
  const collection = readStoredProfiles();
  const stored = collection?.items.find((item) => item.targetId === collection.activeTargetId) ?? null;
  if (!stored) {
    return null;
  }
  try {
    const normalized = normalizeConnectionSettings(hydrateConnectionSettings(stored));
    return {
      ...normalized,
      accessToken: readSecret(stored.secretRef, normalized.clientApiBaseUrl, stored.userId),
    };
  } catch {
    return null;
  }
}

/** Reuse an encrypted LAN credential when the user leaves the password field blank. */
export function resolveCandidateConnectionSettings(settings: ConnectionSettings): ConnectionSettings {
  const candidate = normalizeConnectionSettings(settings);
  if (candidate.accessToken) {
    return candidate;
  }
  const collection = readStoredProfiles();
  const stored = collection?.items.find((item) =>
    item.targetId === candidate.targetId || canReuseStoredCredential(item, candidate),
  ) ?? null;
  const accessToken =
    stored && canReuseStoredCredential(stored, candidate)
      ? readSecret(stored.secretRef, candidate.clientApiBaseUrl, stored.userId)
      : "";
  if (!accessToken) {
    throw new Error("Sign in before using a new Node endpoint.");
  }
  return {
    ...candidate,
    accessToken,
  };
}

/** Persist public profile JSON and an encrypted Main-only LAN credential. */
export function writeSecureConnectionSettings(settings: ConnectionSettings): void {
  const normalizedSettings = normalizeConnectionSettings(settings);
  const collection = readStoredProfiles();
  const previous = collection?.items.find((item) => item.targetId === normalizedSettings.targetId);
  let secretRef: string | undefined;
  const accessToken = normalizedSettings.accessToken || "";
  if (accessToken) {
    secretRef = writeSecret(
      accessToken,
      normalizedSettings.clientApiBaseUrl,
      normalizedSettings.targetId,
      normalizedSettings.userId || "",
    );
  }
  const next = upsertStoredConnectionProfile(
    collection,
    toStoredConnectionSettings(normalizedSettings, secretRef),
  );
  atomicWrite(settingsPath(), JSON.stringify(next, null, 2));
  if (previous?.secretRef && previous.secretRef !== secretRef) {
    deleteSecret(previous.secretRef);
  }
}

export interface SecureConnectionProfileSummary {
  targetType: "local" | "lan";
  targetId: string;
  targetName: string;
  clientApiBaseUrl: string;
  active: boolean;
  credentialConfigured: boolean;
}

/** List public target metadata without decrypting or returning credentials. */
export function listSecureConnectionProfiles(): SecureConnectionProfileSummary[] {
  const collection = readStoredProfiles();
  if (!collection) return [];
  return collection.items.map((item) => ({
    targetType: item.targetType,
    targetId: item.targetId,
    targetName: item.targetName,
    clientApiBaseUrl: item.clientApiBaseUrl,
    active: item.targetId === collection.activeTargetId,
    credentialConfigured: Boolean(item.secretRef),
  }));
}

/** Hydrate one selected target only inside Electron Main. */
export function readSecureConnectionProfile(targetId: string): ConnectionSettings {
  const collection = readStoredProfiles();
  const stored = collection?.items.find((item) => item.targetId === targetId);
  if (!stored) throw new Error("The saved Node target was not found.");
  return {
    ...hydrateConnectionSettings(stored),
    accessToken: readSecret(stored.secretRef, stored.clientApiBaseUrl, stored.userId),
  };
}

/** Remove the active encrypted user session while retaining public Node metadata. */
export function clearActiveSecureConnectionCredential(): void {
  const collection = readStoredProfiles();
  if (!collection) return;
  const active = collection.items.find((item) => item.targetId === collection.activeTargetId);
  if (!active) return;
  const next = {
    ...collection,
    items: collection.items.map((item) => item.targetId === active.targetId
      ? { ...item, secretRef: undefined, userId: undefined, userEmail: undefined, userPrivilegeLevel: undefined }
      : item),
  };
  atomicWrite(settingsPath(), JSON.stringify(next, null, 2));
  deleteSecret(active.secretRef);
}

/** Persist only the active target pointer after a successful connection test. */
export function setActiveSecureConnectionProfile(targetId: string): void {
  const collection = readStoredProfiles();
  if (!collection?.items.some((item) => item.targetId === targetId)) {
    throw new Error("The saved Node target was not found.");
  }
  atomicWrite(settingsPath(), JSON.stringify({ ...collection, activeTargetId: targetId }, null, 2));
}

/** Remove one inactive target and its encrypted credential. */
export function removeSecureConnectionProfile(targetId: string): void {
  const collection = readStoredProfiles();
  if (!collection) throw new Error("The saved Node target was not found.");
  if (collection.activeTargetId === targetId) {
    throw new Error("Switch to another Node before removing the active target.");
  }
  const stored = collection.items.find((item) => item.targetId === targetId);
  if (!stored) throw new Error("The saved Node target was not found.");
  atomicWrite(
    settingsPath(),
    JSON.stringify({ ...collection, items: collection.items.filter((item) => item.targetId !== targetId) }, null, 2),
  );
  deleteSecret(stored.secretRef);
}
