import { describe, expect, it, vi } from "vitest";
import {
  CLIENT_API_PROTOCOL_VERSION,
  ClientApiHttpTransport,
  ClientApiRequestError,
  buildClientApiAuthorizationHeaders,
  normalizeClientApiMessage,
  normalizeClientApiSession,
  parseClientApiHandshake,
  parseClientApiNodeInfo,
} from "../src";
import healthIncompatible from "../../../contracts/client-api/fixtures/health-incompatible.json";
import healthV1 from "../../../contracts/client-api/fixtures/health-v1.json";
import nodeV1 from "../../../contracts/client-api/fixtures/node-v1.json";

describe("OpenPPX Client public contract", () => {
  it("builds bearer headers without placing credentials in URLs", () => {
    expect(buildClientApiAuthorizationHeaders("")).toEqual({});
    expect(buildClientApiAuthorizationHeaders("  secret-token  ")).toEqual({
      Authorization: "Bearer secret-token",
    });
  });

  it("parses compatible and incompatible protocol fixtures", () => {
    expect(parseClientApiHandshake(healthV1)).toMatchObject({
      protocolVersion: CLIENT_API_PROTOCOL_VERSION,
      productVersion: "0.4",
      ready: true,
      compatibility: "compatible",
    });
    expect(parseClientApiHandshake(healthIncompatible).compatibility).toBe("incompatible");
    expect(parseClientApiNodeInfo(nodeV1)).toMatchObject({
      nodeId: "node_0123456789abcdef0123456789abcdef",
      displayName: "Studio Mac",
      compatibility: "compatible",
      authenticationRequired: true,
    });
  });

  it("normalizes shared Session and Message models", () => {
    expect(
      normalizeClientApiSession({
        id: "session_1",
        agent_id: "writer",
        title: "Demo",
        updated_at: "2026-04-02T12:00:00+08:00",
        last_message_preview: "preview",
      }),
    ).toMatchObject({ id: "session_1", agentId: "writer", title: "Demo" });
    expect(
      normalizeClientApiMessage({
        id: "message_1",
        session_id: "session_1",
        role: "assistant",
        status: "completed",
        parts: [{ type: "markdown", text: "Hello" }],
      }),
    ).toMatchObject({ id: "message_1", sessionId: "session_1", role: "assistant" });
  });

  it("normalizes URLs and authenticated headers in the HTTP transport", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, data: { items: [] } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const transport = new ClientApiHttpTransport({
      baseUrl: "http://127.0.0.1:8765/",
      accessToken: " token ",
      fetch: fetchMock as unknown as typeof fetch,
    });

    await transport.requestJson("/api/v1/agents");

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8765/api/v1/agents");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer token");
  });

  it("exposes structured Client API errors", async () => {
    const transport = new ClientApiHttpTransport({
      baseUrl: "http://127.0.0.1:8765",
      fetch: (async () =>
        new Response(JSON.stringify({ ok: false, error: { code: "UNAUTHORIZED", message: "Invalid token" } }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        })) as typeof fetch,
    });

    const error = await transport.requestJson("/api/v1/node").catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ClientApiRequestError);
    expect(error).toMatchObject({
      name: "ClientApiRequestError",
      status: 401,
      code: "UNAUTHORIZED",
    });
  });
});
