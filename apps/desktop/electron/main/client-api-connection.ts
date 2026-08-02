import {
  CLIENT_API_PROTOCOL_VERSION,
  ClientApiHttpTransport,
  ClientApiRequestError,
  parseClientApiHandshake,
  parseClientApiNodeInfo,
  type ClientApiHandshake,
  type ClientApiNodeInfo,
} from "@openppx/client";

export type ClientApiAuthState = "authenticated" | "not-required" | "missing" | "unauthorized" | "unknown";

export interface ClientApiConnectionSnapshot {
  baseUrl: string;
  reachable: boolean;
  handshake: ClientApiHandshake | null;
  nodeInfo: ClientApiNodeInfo | null;
  authState: ClientApiAuthState;
  credentialConfigured: boolean;
  lastError: string;
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

/**
 * Owns the mutable HTTP/protocol health state for one OpenPPX Client API endpoint.
 *
 * The service never persists credentials and never starts local processes. Those
 * responsibilities stay in the Electron host so this boundary can later be reused
 * by a browser transport with a different credential policy.
 */
export class ClientApiConnection {
  private baseUrlValue: string;

  private accessTokenValue: string;

  private readonly fetcher?: typeof globalThis.fetch;

  private readonly healthCacheTtlMs: number;

  private readonly now: () => number;

  private healthyUntil = 0;

  private inflightHealthCheck: Promise<boolean> | null = null;

  private handshake: ClientApiHandshake | null = null;

  private nodeInfo: ClientApiNodeInfo | null = null;

  private authState: ClientApiAuthState = "unknown";

  private lastError = "";

  private reachable = false;

  private configurationGeneration = 0;

  public constructor(options: ClientApiConnectionOptions) {
    this.baseUrlValue = options.baseUrl.trim();
    this.accessTokenValue = options.accessToken?.trim() ?? "";
    this.fetcher = options.fetch;
    this.healthCacheTtlMs = options.healthCacheTtlMs ?? 1_500;
    this.now = options.now ?? Date.now;
  }

  public get baseUrl(): string {
    return this.baseUrlValue;
  }

  /** Main-process-only credential access for spawning the managed local gateway. */
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
  }

  public unavailableError(operation: string): Error {
    const reason = this.lastError || `No compatible protocol v${CLIENT_API_PROTOCOL_VERSION} gateway is ready.`;
    return new Error(`${operation} requires the OpenPPX Client API. ${reason}`);
  }

  public request(pathname: string, init?: RequestInit): Promise<Response> {
    return this.transport().request(pathname, init);
  }

  public requestJson(pathname: string, init?: RequestInit): Promise<Record<string, unknown>> {
    return this.transport().requestJson(pathname, init);
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
        throw new Error("Client API reported that it is not ready.");
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
