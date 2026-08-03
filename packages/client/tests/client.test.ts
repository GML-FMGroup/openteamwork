import { describe, expect, it, vi } from "vitest";
import {
  CLIENT_API_PROTOCOL_VERSION,
  ClientApiHttpTransport,
  ClientApiRequestError,
  OpenPpxClient,
  buildClientApiAuthorizationHeaders,
  normalizeClientApiMessage,
  normalizeClientApiSession,
  parseClientApiHandshake,
  parseClientApiNodeInfo,
  type SetupApplyRequest,
} from "../src";
import actionCatalog from "../../../contracts/client-api/v1/fixtures/action-catalog.json";
import actionInvokeModelList from "../../../contracts/client-api/v1/fixtures/action-invoke-model-list.json";
import actionInvokeRunStop from "../../../contracts/client-api/v1/fixtures/action-invoke-run-stop.json";
import actionInvokeSessionNew from "../../../contracts/client-api/v1/fixtures/action-invoke-session-new.json";
import actionInvokeStatus from "../../../contracts/client-api/v1/fixtures/action-invoke-status.json";
import actionInvokeExtensionList from "../../../contracts/client-api/v1/fixtures/action-invoke-extension-list.json";
import actionInvokeCommand from "../../../contracts/client-api/v1/fixtures/action-invoke-command.json";
import actionInvokeSetupApply from "../../../contracts/client-api/v1/fixtures/action-invoke-setup-apply.json";
import actionInvokeSetupHello from "../../../contracts/client-api/v1/fixtures/action-invoke-setup-hello.json";
import actionInvokeSetupStatus from "../../../contracts/client-api/v1/fixtures/action-invoke-setup-status.json";
import actionError from "../../../contracts/client-api/v1/fixtures/envelope-error.json";
import modelListSuccess from "../../../contracts/client-api/v1/fixtures/envelope-model-list.json";
import runStopSuccess from "../../../contracts/client-api/v1/fixtures/envelope-run-stop.json";
import sessionNewSuccess from "../../../contracts/client-api/v1/fixtures/envelope-session-new.json";
import actionSuccess from "../../../contracts/client-api/v1/fixtures/envelope-success.json";
import extensionListSuccess from "../../../contracts/client-api/v1/fixtures/envelope-extension-list.json";
import commandStatusSuccess from "../../../contracts/client-api/v1/fixtures/envelope-command-status.json";
import setupApplySuccess from "../../../contracts/client-api/v1/fixtures/envelope-setup-apply.json";
import setupHelloSuccess from "../../../contracts/client-api/v1/fixtures/envelope-setup-hello.json";
import setupStatusSuccess from "../../../contracts/client-api/v1/fixtures/envelope-setup-status.json";
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
      nodeId: "test-node",
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

  it("uses the shared Action envelope and sends stable invocation metadata", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            protocolVersion: 1,
            requestId: "req_catalog_fixture",
            correlationId: "corr_catalog_fixture",
            ok: true,
            result: actionCatalog,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(actionSuccess), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    const client = new OpenPpxClient({
      baseUrl: "http://127.0.0.1:8765",
      fetch: fetchMock as unknown as typeof fetch,
      idFactory: () => actionInvokeStatus.requestId,
    });

    await expect(client.actions.catalog("system")).resolves.toEqual(actionCatalog.items);
    await expect(client.system.status()).resolves.toEqual(actionSuccess);

    const [invokeUrl, invokeInit] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(invokeUrl).toBe("http://127.0.0.1:8765/api/v1/actions/invoke");
    expect(JSON.parse(String(invokeInit.body))).toEqual(actionInvokeStatus);
  });

  it("routes model, session, and run domain clients through shared Action fixtures", async () => {
    const cases = [
      {
        request: actionInvokeModelList,
        response: modelListSuccess,
        invoke: (client: OpenPpxClient) => client.model.list(),
      },
      {
        request: actionInvokeSessionNew,
        response: sessionNewSuccess,
        invoke: (client: OpenPpxClient) => client.session.create("writer", "user_fixture"),
      },
      {
        request: actionInvokeRunStop,
        response: runStopSuccess,
        invoke: (client: OpenPpxClient) => client.run.stop("run_fixture"),
      },
    ];

    for (const testCase of cases) {
      const fetchMock = vi.fn(async () =>
        new Response(JSON.stringify(testCase.response), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      const client = new OpenPpxClient({
        baseUrl: "http://127.0.0.1:8765",
        fetch: fetchMock as unknown as typeof fetch,
        idFactory: () => testCase.request.requestId,
      });

      await expect(testCase.invoke(client)).resolves.toEqual(testCase.response);
      const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
      expect(url).toBe("http://127.0.0.1:8765/api/v1/actions/invoke");
      expect(JSON.parse(String(init.body))).toEqual(testCase.request);
    }
  });

  it("lists typed Extensions through the shared Action contract", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(extensionListSuccess), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new OpenPpxClient({
      baseUrl: "http://127.0.0.1:8765",
      fetch: fetchMock as unknown as typeof fetch,
      idFactory: () => actionInvokeExtensionList.requestId,
    });

    const response = await client.extensions.list();

    expect(response.result.items[0]).toMatchObject({
      kind: "skill",
      id: "fixture-skill",
      readiness: { ready: true },
    });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8765/api/v1/actions/invoke");
    expect(JSON.parse(String(init.body))).toEqual(actionInvokeExtensionList);
  });

  it("discovers and invokes Action-backed slash commands", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            protocolVersion: 1,
            requestId: "req_commands_fixture",
            correlationId: "req_commands_fixture",
            ok: true,
            result: actionCatalog,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(commandStatusSuccess), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    const client = new OpenPpxClient({
      baseUrl: "http://127.0.0.1:8765",
      fetch: fetchMock as unknown as typeof fetch,
      idFactory: () => actionInvokeCommand.requestId,
    });

    await expect(client.commands.list()).resolves.toMatchObject([
      { command: "/status", actionId: "system.status", available: true },
    ]);
    await expect(
      client.commands.invoke("/status", {
        userId: "user_fixture",
        agentId: "writer",
        sessionId: "session_fixture",
      }),
    ).resolves.toEqual(commandStatusSuccess);

    const [catalogUrl] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(catalogUrl).toBe("http://127.0.0.1:8765/api/v1/actions?projection=slash");
    const [invokeUrl, invokeInit] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(invokeUrl).toBe("http://127.0.0.1:8765/api/v1/actions/invoke");
    expect(JSON.parse(String(invokeInit.body))).toEqual(actionInvokeCommand);
  });

  it("routes setup status, apply, and first Hello through shared Actions", async () => {
    const requests = [actionInvokeSetupStatus, actionInvokeSetupApply, actionInvokeSetupHello];
    const responses = [setupStatusSuccess, setupApplySuccess, setupHelloSuccess];
    let requestIndex = 0;
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify(responses[requestIndex++]),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    const client = new OpenPpxClient({
      baseUrl: "http://127.0.0.1:18765",
      fetch: fetchMock as unknown as typeof fetch,
      idFactory: () => requests[requestIndex].requestId,
    });

    await expect(client.setup.status()).resolves.toEqual(setupStatusSuccess);
    await expect(client.setup.apply(actionInvokeSetupApply.input.request as SetupApplyRequest)).resolves.toEqual(setupApplySuccess);
    await expect(client.setup.hello("main", "user_fixture", "Hello OpenPPX")).resolves.toEqual(setupHelloSuccess);

    for (const [index, expected] of requests.entries()) {
      const [url, init] = fetchMock.mock.calls[index] as unknown as [string, RequestInit];
      expect(url).toBe("http://127.0.0.1:18765/api/v1/actions/invoke");
      expect(JSON.parse(String(init.body))).toEqual(expected);
    }
  });

  it("preserves the common Action error metadata", async () => {
    const client = new OpenPpxClient({
      baseUrl: "http://127.0.0.1:8765",
      fetch: (async () =>
        new Response(JSON.stringify(actionError), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        })) as typeof fetch,
    });

    await expect(client.system.status()).rejects.toMatchObject({
      status: 409,
      code: actionError.error.code,
      details: actionError.error.details,
      retryable: true,
      requestId: actionError.requestId,
      correlationId: actionError.correlationId,
    });
  });
});
