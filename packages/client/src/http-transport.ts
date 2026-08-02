import { buildClientApiAuthorizationHeaders } from "./auth";
import { ClientApiRequestError } from "./errors";

export interface ClientApiHttpTransportOptions {
  baseUrl: string;
  accessToken?: string;
  fetch?: typeof globalThis.fetch;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

/**
 * HTTP transport for the OpenPPX Client API.
 *
 * The transport owns URL/header/error normalization only. Credential storage and
 * runtime lifecycle stay with the host application so Desktop can keep secrets
 * outside the sandboxed renderer.
 */
export class ClientApiHttpTransport {
  private readonly baseUrl: string;

  private readonly accessToken: string;

  private readonly fetcher: typeof globalThis.fetch;

  public constructor(options: ClientApiHttpTransportOptions) {
    const baseUrl = options.baseUrl.trim().replace(/\/+$/, "");
    if (!baseUrl) {
      throw new TypeError("Client API baseUrl is required.");
    }
    this.baseUrl = baseUrl;
    this.accessToken = options.accessToken?.trim() ?? "";
    this.fetcher = options.fetch ?? globalThis.fetch;
    if (typeof this.fetcher !== "function") {
      throw new TypeError("A Fetch API implementation is required.");
    }
  }

  /** Open a raw Client API request, including authenticated SSE responses. */
  public request(pathname: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    for (const [name, value] of Object.entries(buildClientApiAuthorizationHeaders(this.accessToken))) {
      headers.set(name, value);
    }
    return this.fetcher(`${this.baseUrl}${pathname.startsWith("/") ? pathname : `/${pathname}`}`, {
      ...init,
      headers,
    });
  }

  /** Request and validate a JSON Client API envelope. */
  public async requestJson(pathname: string, init: RequestInit = {}): Promise<Record<string, unknown>> {
    const headers = new Headers(init.headers);
    if (init.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const response = await this.request(pathname, { ...init, headers });
    let payload: Record<string, unknown> | null = null;
    try {
      payload = asRecord(await response.json());
    } catch {
      // The structured error below keeps callers independent of Fetch JSON exceptions.
    }
    if (!payload) {
      throw new ClientApiRequestError(
        `Client API returned a non-JSON response: ${response.status}`,
        response.status,
        "CLIENT_API_INVALID_RESPONSE",
      );
    }
    if (!response.ok || payload.ok === false) {
      const error = asRecord(payload.error);
      throw new ClientApiRequestError(
        String(error?.message ?? `Client API request failed: ${response.status}`),
        response.status,
        String(error?.code ?? "CLIENT_API_REQUEST_FAILED"),
      );
    }
    return payload;
  }
}
