import {
  CLIENT_API_PROTOCOL_VERSION,
  ClientApiHttpTransport,
  ClientApiRequestError,
  parseClientApiHandshake,
  parseClientApiNodeInfo,
  type ClientApiHandshake,
  type ClientApiNodeInfo,
} from "@openppx/client";
import { productProfile } from "../../product";
import type { ClientApiConnectionFailure } from "./node-start-policy";

export type ClientApiAuthState = "authenticated" | "not-required" | "missing" | "unauthorized" | "unknown";

export interface ClientApiConnectionSnapshot {
  baseUrl: string;
  reachable: boolean;
  handshake: ClientApiHandshake | null;
  nodeInfo: ClientApiNodeInfo | null;
  authState: ClientApiAuthState;
  credentialConfigured: boolean;
  lastError: string;
  lastFailure: ClientApiConnectionFailure;
}

export interface ClientApiConnectionOptions {
  baseUrl: string;
  accessToken?: string;
  fetch?: typeof globalThis.fetch;
  healthCacheTtlMs?: number;
  now?: () => number;
}

export interface ClientApiHealthOptions {
  timeoutMs: number;
}

const TLS_CERTIFICATE_ERROR =
  "The Node HTTPS certificate could not be verified. Make sure it is trusted, valid, and issued for this Node URL.";

function errorChainText(error: unknown): string {
  const parts: string[] = [];
  const visited = new Set<unknown>();
  let current = error;
  for (let depth = 0; depth < 5 && current && !visited.has(current); depth += 1) {
    visited.add(current);
    if (current instanceof Error) {
      parts.push(current.name, current.message);
      const code = (current as Error & { code?: unknown }).code;
      if (code !== undefined) parts.push(String(code));
      current = (current as Error & { cause?: unknown }).cause;
      continue;
    }
    if (typeof current === "object") {
      const record = current as Record<string, unknown>;
      if (record.message !== undefined) parts.push(String(record.message));
      if (record.code !== undefined) parts.push(String(record.code));
      current = record.cause;
      continue;
    }
    parts.push(String(current));
    break;
  }
  return parts.join(" ");
}

/** Classify only transport evidence strong enough to drive process lifecycle. */
export function classifyClientApiConnectionFailure(error: unknown): ClientApiConnectionFailure {
  const detail = errorChainText(error);
  if (/\bECONNREFUSED\b|\bERR_CONNECTION_REFUSED\b|connection refused/i.test(detail)) {
    return "connection-refused";
  }
  if (/\bAbortError\b|operation was aborted|timed? out|\bETIMEDOUT\b/i.test(detail)) {
    return "timeout";
  }
  return "other";
}

/** Preserve ordinary fetch failures while projecting nested TLS causes into safe guidance. */
function projectFetchError(error: unknown): Error {
  if (
    /UNABLE_TO_VERIFY_LEAF_SIGNATURE|ERR_CERT_|CERT_(?:AUTHORITY|COMMON_NAME|DATE)|unable to verify|self[- ]signed certificate|certificate.*(?:invalid|trust|verif)/i
      .test(errorChainText(error))
  ) {
    const projected = new Error(TLS_CERTIFICATE_ERROR);
    (projected as Error & { cause?: unknown }).cause = error;
    return projected;
  }
  return error instanceof Error ? error : new Error(String(error));
}

export type ClientApiUserPrivilege = "low" | "medium" | "high" | "root";

export interface ClientApiAuthenticatedUser {
  userId: string;
  email: string;
  privilegeLevel: ClientApiUserPrivilege;
  status: "active";
}

export interface ClientApiLoginResult {
  accessToken: string;
  expiresAtMs: number;
  user: ClientApiAuthenticatedUser;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function authenticatedUser(value: unknown): ClientApiAuthenticatedUser {
  const user = record(value);
  const privilegeLevel = String(user?.privilegeLevel ?? "");
  if (
    !user
    || typeof user.userId !== "string"
    || !user.userId
    || typeof user.email !== "string"
    || !user.email
    || !["low", "medium", "high", "root"].includes(privilegeLevel)
    || user.status !== "active"
  ) {
    throw new Error("Node returned an invalid authenticated user.");
  }
  return {
    userId: user.userId,
    email: user.email,
    privilegeLevel: privilegeLevel as ClientApiUserPrivilege,
    status: "active",
  };
}

/**
 * Owns the mutable HTTP/protocol health state for one product Client API endpoint.
 *
 * The service never persists credentials and never starts local processes. Those
 * responsibilities stay in the Electron host so this boundary can later be reused
 * by a browser transport with a different credential policy.
 */
export class ClientApiConnection {
  private baseUrlValue: string;

  private accessTokenValue: string;

  private readonly fetcher: typeof globalThis.fetch;

  private readonly healthCacheTtlMs: number;

  private readonly now: () => number;

  private healthyUntil = 0;

  private inflightHealthCheck: Promise<boolean> | null = null;

  private handshake: ClientApiHandshake | null = null;

  private nodeInfo: ClientApiNodeInfo | null = null;

  private authState: ClientApiAuthState = "unknown";

  private lastError = "";

  private lastFailure: ClientApiConnectionFailure = "other";

  private reachable = false;

  private configurationGeneration = 0;

  public constructor(options: ClientApiConnectionOptions) {
    this.baseUrlValue = options.baseUrl.trim();
    this.accessTokenValue = options.accessToken?.trim() ?? "";
    const hostFetch = options.fetch ?? globalThis.fetch;
    this.fetcher = async (input, init) => {
      try {
        return await hostFetch(input, init);
      } catch (error) {
        throw projectFetchError(error);
      }
    };
    this.healthCacheTtlMs = options.healthCacheTtlMs ?? 1_500;
    this.now = options.now ?? Date.now;
  }

  public get baseUrl(): string {
    return this.baseUrlValue;
  }

  /** Main-process-only credential access for spawning the managed local Node. */
  public get accessToken(): string {
    return this.accessTokenValue;
  }

  public configure(options: { baseUrl: string; accessToken?: string }): void {
    this.baseUrlValue = options.baseUrl.trim();
    this.accessTokenValue = options.accessToken?.trim() ?? "";
    this.configurationGeneration += 1;
    this.healthyUntil = 0;
    this.inflightHealthCheck = null;
    this.handshake = null;
    this.nodeInfo = null;
    this.authState = "unknown";
    this.lastError = "";
    this.lastFailure = "other";
    this.reachable = false;
  }

  public getSnapshot(): ClientApiConnectionSnapshot {
    return {
      baseUrl: this.baseUrlValue,
      reachable: this.reachable,
      handshake: this.handshake ? { ...this.handshake } : null,
      nodeInfo: this.nodeInfo
        ? {
            ...this.nodeInfo,
            capabilities: [...this.nodeInfo.capabilities],
          }
        : null,
      authState: this.authState,
      credentialConfigured: Boolean(this.accessTokenValue),
      lastError: this.lastError,
      lastFailure: this.lastFailure,
    };
  }

  public invalidateHealth(): void {
    this.healthyUntil = 0;
  }

  public isHealthCached(): boolean {
    return this.now() < this.healthyUntil;
  }

  public rememberError(error: unknown): void {
    this.lastError = error instanceof Error ? error.message : String(error);
    this.lastFailure = classifyClientApiConnectionFailure(error);
  }

  public unavailableError(operation: string): Error {
    const reason = this.lastError || `No compatible protocol v${CLIENT_API_PROTOCOL_VERSION} Node is ready.`;
    return new Error(`${operation} requires the ${productProfile.displayName} Client API. ${reason}`);
  }

  public request(pathname: string, init?: RequestInit): Promise<Response> {
    return this.transport().request(pathname, init);
  }

  public requestJson(pathname: string, init?: RequestInit): Promise<Record<string, unknown>> {
    return this.transport().requestJson(pathname, init);
  }

  /** Exchange a user secret once for a revocable opaque session token. */
  public async login(email: string, secret: string): Promise<ClientApiLoginResult> {
    const payload = await new ClientApiHttpTransport({
      baseUrl: this.baseUrlValue,
      fetch: this.fetcher,
    }).requestJson("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, secret }),
    });
    const data = record(payload.data);
    const accessToken = typeof data?.accessToken === "string" ? data.accessToken.trim() : "";
    const expiresAtMs = Number(data?.expiresAtMs);
    if (!accessToken || !Number.isSafeInteger(expiresAtMs) || expiresAtMs <= 0) {
      throw new Error("Node returned an invalid user session.");
    }
    const result = {
      accessToken,
      expiresAtMs,
      user: authenticatedUser(data?.user),
    };
    this.configure({ baseUrl: this.baseUrlValue, accessToken });
    return result;
  }

  /** Resolve the current opaque session to its server-authenticated user. */
  public async getAuthenticatedUser(): Promise<ClientApiAuthenticatedUser> {
    const payload = await this.requestJson("/api/v1/auth/me");
    return authenticatedUser(record(payload.data)?.user);
  }

  /** Revoke the current user session and clear it from this connection. */
  public async logout(): Promise<boolean> {
    try {
      const payload = await this.requestJson("/api/v1/auth/logout", { method: "POST", body: "{}" });
      return record(payload.data)?.loggedOut === true;
    } finally {
      this.configure({ baseUrl: this.baseUrlValue, accessToken: "" });
    }
  }

  /** Check health once per TTL and share an in-flight probe between concurrent callers. */
  public checkHealth(options: ClientApiHealthOptions): Promise<boolean> {
    if (this.isHealthCached()) {
      return Promise.resolve(true);
    }
    if (this.inflightHealthCheck) {
      return this.inflightHealthCheck;
    }
    const generation = this.configurationGeneration;
    const healthCheck = this.checkHealthImpl(options, generation);
    this.inflightHealthCheck = healthCheck;
    void healthCheck.finally(() => {
      if (this.inflightHealthCheck === healthCheck) {
        this.inflightHealthCheck = null;
      }
    });
    return healthCheck;
  }

  private transport(): ClientApiHttpTransport {
    return new ClientApiHttpTransport({
      baseUrl: this.baseUrlValue,
      accessToken: this.accessTokenValue,
      fetch: this.fetcher,
    });
  }

  private async checkHealthImpl(options: ClientApiHealthOptions, generation: number): Promise<boolean> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeoutMs);
    this.reachable = false;
    this.nodeInfo = null;
    this.authState = "unknown";
    try {
      const healthPayload = await this.requestJson("/api/v1/health", { signal: controller.signal });
      if (generation !== this.configurationGeneration) {
        return false;
      }
      this.reachable = true;
      const handshake = parseClientApiHandshake(healthPayload);
      this.handshake = handshake;
      if (handshake.compatibility !== "compatible") {
        throw new Error(
          `Client API protocol ${handshake.protocolVersion} is incompatible; Desktop supports ${CLIENT_API_PROTOCOL_VERSION}.`,
        );
      }
      if (!handshake.ready) {
        // A protocol-compatible unconfigured Node is intentionally usable for setup Actions.
        await this.requestJson("/api/v1/actions?namespace=setup", { signal: controller.signal });
        if (generation !== this.configurationGeneration) {
          return false;
        }
        this.authState = this.accessTokenValue ? "authenticated" : "not-required";
        this.lastError = "";
        this.lastFailure = "other";
        this.healthyUntil = this.now() + this.healthCacheTtlMs;
        return true;
      }

      const nodePayload = await this.requestJson("/api/v1/node", { signal: controller.signal });
      if (generation !== this.configurationGeneration) {
        return false;
      }
      const nodeInfo = parseClientApiNodeInfo(nodePayload);
      this.nodeInfo = nodeInfo;
      if (nodeInfo.compatibility !== "compatible") {
        throw new Error(
          `Node protocol range ${nodeInfo.protocolMin}-${nodeInfo.protocolMax} does not include ${CLIENT_API_PROTOCOL_VERSION}.`,
        );
      }
      this.authState = nodeInfo.authenticationRequired ? "authenticated" : "not-required";
      this.lastError = "";
      this.lastFailure = "other";
      this.healthyUntil = this.now() + this.healthCacheTtlMs;
      return true;
    } catch (error) {
      if (generation !== this.configurationGeneration) {
        return false;
      }
      if (error instanceof ClientApiRequestError) {
        this.reachable = true;
        if (error.code === "UNAUTHORIZED") {
          this.authState = this.accessTokenValue ? "unauthorized" : "missing";
        }
      }
      this.rememberError(error);
      return false;
    } finally {
      clearTimeout(timeout);
    }
  }
}
