import { app, safeStorage } from "electron";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import {
  canReuseStoredCredential,
  hydrateConnectionSettings,
  normalizeConnectionSettings,
  parseBoundConnectionCredential,
  parseStoredConnectionSettings,
  serializeBoundConnectionCredential,
  toStoredConnectionSettings,
  type StoredConnectionSettings,
} from "../../app/src/lib/connection-profile";
import type { ConnectionSettings } from "../../app/src/types";

const CLIENT_API_SECRET_REF = "client-api-token-v1";

function settingsPath(): string {
  return path.join(app.getPath("userData"), "connection-settings.json");
}

function secretPath(): string {
  return path.join(app.getPath("userData"), "connection-secret.bin");
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

function readStoredSettings(): StoredConnectionSettings | null {
  try {
    const filePath = settingsPath();
    if (!fs.existsSync(filePath)) {
      return null;
    }
    return parseStoredConnectionSettings(JSON.parse(fs.readFileSync(filePath, "utf-8")));
  } catch {
    return null;
  }
}

function readSecret(secretRef: string | undefined, clientApiBaseUrl: string): string {
  if (secretRef !== CLIENT_API_SECRET_REF || !secureStorageAvailable()) {
    return "";
  }
  try {
    const filePath = secretPath();
    if (!fs.existsSync(filePath)) {
      return "";
    }
    return parseBoundConnectionCredential(
      safeStorage.decryptString(fs.readFileSync(filePath)),
      clientApiBaseUrl,
    );
  } catch {
    return "";
  }
}

function writeSecret(accessToken: string, clientApiBaseUrl: string): string {
  if (!secureStorageAvailable()) {
    throw new Error("Secure credential storage is unavailable on this system.");
  }
  atomicWrite(
    secretPath(),
    safeStorage.encryptString(serializeBoundConnectionCredential(clientApiBaseUrl, accessToken)),
  );
  return CLIENT_API_SECRET_REF;
}

function deleteSecret(): void {
  try {
    const filePath = secretPath();
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
  } catch {
    // A stale encrypted blob is not referenced by local settings and cannot be returned to Renderer.
  }
}

/** Read public settings and hydrate the credential only inside Electron Main. */
export function readSecureConnectionSettings(): ConnectionSettings | null {
  const stored = readStoredSettings();
  if (!stored) {
    return null;
  }
  try {
    const normalized = normalizeConnectionSettings(hydrateConnectionSettings(stored));
    return {
      ...normalized,
      accessToken:
        normalized.targetType === "lan"
          ? readSecret(stored.secretRef, normalized.clientApiBaseUrl)
          : "",
    };
  } catch {
    return null;
  }
}

/** Reuse an encrypted LAN credential when the user leaves the password field blank. */
export function resolveCandidateConnectionSettings(settings: ConnectionSettings): ConnectionSettings {
  const candidate = normalizeConnectionSettings(settings);
  if (candidate.targetType === "local" || candidate.accessToken) {
    return candidate;
  }
  const stored = readStoredSettings();
  const accessToken =
    stored && canReuseStoredCredential(stored, candidate)
      ? readSecret(stored.secretRef, candidate.clientApiBaseUrl)
      : "";
  if (!accessToken) {
    throw new Error("A Client API token is required for a new LAN endpoint.");
  }
  return {
    ...candidate,
    accessToken,
  };
}

/** Persist public profile JSON and an encrypted Main-only LAN credential. */
export function writeSecureConnectionSettings(settings: ConnectionSettings): void {
  const normalizedSettings = normalizeConnectionSettings(settings);
  let secretRef: string | undefined;
  if (normalizedSettings.targetType === "lan") {
    const accessToken = normalizedSettings.accessToken || "";
    if (!accessToken) {
      throw new Error("A Client API token is required for a LAN connection.");
    }
    secretRef = writeSecret(accessToken, normalizedSettings.clientApiBaseUrl);
  } else {
    deleteSecret();
  }
  atomicWrite(settingsPath(), JSON.stringify(toStoredConnectionSettings(normalizedSettings, secretRef), null, 2));
}
