import { vi } from "vitest";
import { ClientApiConnection } from "../electron/main/client-api-connection";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function healthPayload(): Record<string, unknown> {
  return {
    ok: true,
    data: {
      service: "openppx-client-api",
      product_version: "0.5.3",
      protocol_version: 1,
      ready: true,
      state: "healthy",
    },
  };
}

function setupHealthPayload(): Record<string, unknown> {
  const payload = healthPayload();
  (payload.data as Record<string, unknown>).ready = false;
  (payload.data as Record<string, unknown>).state = "needs_configuration";
  return payload;
}

function nodePayload(): Record<string, unknown> {
  return {
    ok: true,
    data: {
      node_id: "test-node",
      display_name: "Studio Mac",
      product_version: "0.5.3",
      protocol: { min: 1, max: 1 },
      capabilities: ["sessions", "runs"],
      agents: 4,
      authentication_required: true,
    },
  };
}

describe("ClientApiConnection", () => {
  it("caches compatible health and exposes a stable snapshot", async () => {
    let currentTime = 1_000;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      jsonResponse(String(input).endsWith("/api/v1/health") ? healthPayload() : nodePayload()),
    );
    const connection = new ClientApiConnection({
      baseUrl: "http://127.0.0.1:8765",
      accessToken: "secret",
      fetch: fetchMock as typeof fetch,
      now: () => currentTime,
      healthCacheTtlMs: 1_500,
    });

    expect(await connection.checkHealth({ timeoutMs: 100 })).toBe(true);
    expect(await connection.checkHealth({ timeoutMs: 100 })).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const snapshot = connection.getSnapshot();
    expect(snapshot).toMatchObject({
      reachable: true,
      authState: "authenticated",
      credentialConfigured: true,
      lastError: "",
      handshake: { compatibility: "compatible" },
      nodeInfo: { displayName: "Studio Mac", agents: 4 },
    });
    snapshot.nodeInfo?.capabilities.push("mutated");
    expect(connection.getSnapshot().nodeInfo?.capabilities).toEqual(["sessions", "runs"]);

    currentTime += 1_501;
    expect(await connection.checkHealth({ timeoutMs: 100 })).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("deduplicates concurrent health requests", async () => {
    let releaseHealth!: (response: Response) => void;
    const pendingHealth = new Promise<Response>((resolve) => {
      releaseHealth = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) =>
      String(input).endsWith("/api/v1/health") ? pendingHealth : Promise.resolve(jsonResponse(nodePayload())),
    );
    const connection = new ClientApiConnection({
      baseUrl: "http://127.0.0.1:8765",
      fetch: fetchMock as typeof fetch,
    });

    const first = connection.checkHealth({ timeoutMs: 500 });
    const second = connection.checkHealth({ timeoutMs: 500 });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    releaseHealth(jsonResponse(healthPayload()));
    await expect(Promise.all([first, second])).resolves.toEqual([true, true]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("treats a compatible needs-configuration Node as available for setup Actions", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/health")) return jsonResponse(setupHealthPayload());
      if (url.endsWith("/api/v1/actions?namespace=setup")) {
        return jsonResponse({
          protocolVersion: 1,
          requestId: "req-setup",
          correlationId: "req-setup",
          ok: true,
          result: { items: [] },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    const connection = new ClientApiConnection({
      baseUrl: "http://127.0.0.1:8765",
      accessToken: "bootstrap-token",
      fetch: fetchMock as typeof fetch,
    });

    await expect(connection.checkHealth({ timeoutMs: 100 })).resolves.toBe(true);
    expect(connection.getSnapshot()).toMatchObject({
      reachable: true,
      authState: "authenticated",
      handshake: { ready: false, compatibility: "compatible" },
      nodeInfo: null,
      lastError: "",
    });
  });

  it("rejects an incompatible protocol while preserving reachability diagnostics", async () => {
    const incompatible = healthPayload();
    (incompatible.data as Record<string, unknown>).protocol_version = 2;
    const fetchMock = vi.fn(async () => jsonResponse(incompatible));
    const connection = new ClientApiConnection({
      baseUrl: "http://127.0.0.1:8765",
      fetch: fetchMock as typeof fetch,
    });

    expect(await connection.checkHealth({ timeoutMs: 100 })).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(connection.getSnapshot()).toMatchObject({
      reachable: true,
      handshake: { protocolVersion: 2, compatibility: "incompatible" },
      nodeInfo: null,
      authState: "unknown",
      lastError: "Client API protocol 2 is incompatible; Desktop supports 1.",
    });
  });

  it("records structured authorization failures and resets on reconfiguration", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ ok: false, error: { code: "UNAUTHORIZED", message: "Invalid token" } }, 401),
    );
    const connection = new ClientApiConnection({
      baseUrl: "http://127.0.0.1:8765",
      accessToken: "expired",
      fetch: fetchMock as typeof fetch,
    });

    expect(await connection.checkHealth({ timeoutMs: 100 })).toBe(false);
    expect(connection.getSnapshot()).toMatchObject({
      reachable: true,
      authState: "unauthorized",
      lastError: "Invalid token",
    });

    connection.configure({ baseUrl: "http://192.168.1.50:8765", accessToken: "" });
    expect(connection.getSnapshot()).toMatchObject({
      baseUrl: "http://192.168.1.50:8765",
      reachable: false,
      authState: "unknown",
      credentialConfigured: false,
      lastError: "",
      handshake: null,
      nodeInfo: null,
    });
  });

  it("ignores an old endpoint probe after reconfiguration", async () => {
    let releaseHealth!: (response: Response) => void;
    const pendingHealth = new Promise<Response>((resolve) => {
      releaseHealth = resolve;
    });
    const fetchMock = vi.fn(() => pendingHealth);
    const connection = new ClientApiConnection({
      baseUrl: "http://127.0.0.1:8765",
      fetch: fetchMock as typeof fetch,
    });

    const oldProbe = connection.checkHealth({ timeoutMs: 500 });
    connection.configure({ baseUrl: "http://192.168.1.50:8765", accessToken: "new-secret" });
    releaseHealth(jsonResponse(healthPayload()));

    await expect(oldProbe).resolves.toBe(false);
    expect(connection.getSnapshot()).toMatchObject({
      baseUrl: "http://192.168.1.50:8765",
      reachable: false,
      authState: "unknown",
      credentialConfigured: true,
      lastError: "",
      handshake: null,
      nodeInfo: null,
    });
  });
});
