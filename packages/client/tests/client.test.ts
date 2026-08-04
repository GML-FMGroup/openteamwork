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
import actionInvokeOperationsOverview from "../../../contracts/client-api/v1/fixtures/action-invoke-operations-overview.json";
import actionInvokeOperationsAudit from "../../../contracts/client-api/v1/fixtures/action-invoke-operations-audit.json";
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
import operationsOverviewSuccess from "../../../contracts/client-api/v1/fixtures/envelope-operations-overview.json";
import operationsAuditSuccess from "../../../contracts/client-api/v1/fixtures/envelope-operations-audit.json";
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

  it("routes provider catalog and authentication through the model Action facade", async () => {
    const responses = [
      { providerId: "openai_codex", source: "codex_cli", authoritative: true, defaultModel: "openai-codex/gpt-5.5", items: [] },
      { providerId: "openai_codex", state: "authenticated", source: "codex_cli", expiresAt: null, loginMode: "device_code", session: null },
    ];
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      protocolVersion: 1,
      requestId: "request-provider",
      correlationId: "request-provider",
      ok: true,
      result: responses.shift(),
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const client = new OpenPpxClient({
      baseUrl: "http://127.0.0.1:18765",
      fetch: fetchMock as unknown as typeof fetch,
      idFactory: () => "request-provider",
    });

    await client.model.catalog("openai_codex");
    await client.model.authStatus("openai_codex");

    const calls = fetchMock.mock.calls as unknown as Array<[string, RequestInit]>;
    const bodies = calls.map((call) => JSON.parse(String(call[1].body)));
    expect(bodies.map((body) => body.actionId)).toEqual(["model.catalog.list", "model.auth.status"]);
    expect(bodies.every((body) => body.input.providerId === "openai_codex")).toBe(true);
  });

  it("reads, creates, and updates Model Profiles through distinct typed Node Actions", async () => {
    const document = {
      apiVersion: "openppx.io/v1alpha1" as const,
      kind: "ModelProfile" as const,
      metadata: { name: "local-vllm" },
      spec: {
        displayName: "Local VLLM",
        provider: "vllm",
        model: "meta-llama/Llama-3.1-8B-Instruct",
        credential: { store: "system" as const, name: "model-local-vllm-fixture" },
        executionLocation: "local" as const,
        apiBase: "http://127.0.0.1:8000/v1",
        capabilities: ["text" as const, "tool_calling" as const],
        contextWindowTokens: 128000,
        inputCostPerMillionUsd: null,
        outputCostPerMillionUsd: null,
        fallbackProfiles: [],
        enabled: true,
      },
    };
    const fetchMock = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      return new Response(JSON.stringify({
        protocolVersion: 1,
        requestId: body.requestId,
        correlationId: body.correlationId,
        ok: true,
        result: { resourceId: "model-profile/local-vllm", revision: "sha256:model", document },
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const client = new OpenPpxClient({
      baseUrl: "http://127.0.0.1:18765",
      fetch: fetchMock as unknown as typeof fetch,
      idFactory: () => "request-model-profile",
    });

    await client.model.readProfile("local-vllm");
    await client.model.createProfile({
      displayName: "Local VLLM",
      providerId: "vllm",
      model: document.spec.model,
      executionLocation: "local",
      apiBase: document.spec.apiBase,
      capabilities: document.spec.capabilities,
      contextWindowTokens: 128000,
      inputCostPerMillionUsd: null,
      outputCostPerMillionUsd: null,
      fallbackProfileIds: [],
      enabled: true,
      apiKey: "write-only-secret",
    });
    await client.model.updateProfile({
      displayName: "Local VLLM 8B",
      profileId: "local-vllm",
      providerId: "vllm",
      model: document.spec.model,
      executionLocation: "local",
      apiBase: document.spec.apiBase,
      capabilities: document.spec.capabilities,
      contextWindowTokens: 128000,
      inputCostPerMillionUsd: null,
      outputCostPerMillionUsd: null,
      fallbackProfileIds: [],
      enabled: true,
      apiKey: null,
      expectedRevision: "sha256:model",
    });

    const calls = fetchMock.mock.calls as unknown as Array<[string, RequestInit]>;
    const bodies = calls.map((call) => JSON.parse(String(call[1].body)));
    expect(bodies.map((body) => body.actionId)).toEqual(["model.profile.read", "model.profile.create", "model.profile.update"]);
    expect(bodies[1].input).toMatchObject({
      displayName: "Local VLLM",
      apiBase: "http://127.0.0.1:8000/v1",
      apiKey: "write-only-secret",
    });
    expect(bodies[1].input).not.toHaveProperty("profileId");
    expect(bodies[2].input).toMatchObject({
      profileId: "local-vllm",
      displayName: "Local VLLM 8B",
      expectedRevision: "sha256:model",
    });
  });

  it("creates Agents through the typed Node Action facade", async () => {
    const result = {
      agent: {
        id: "research",
        name: "Research",
        description: "Workspace: /node/workspaces/research",
        enabled: true,
        status: "healthy",
        workspace: "/node/workspaces/research",
        avatar: null,
        tags: ["local", "openppx"],
        revision: "sha256:agent",
      },
      nodeRevision: "sha256:node",
      effect: "next_run",
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      protocolVersion: 1,
      requestId: "request-agent-create",
      correlationId: "request-agent-create",
      ok: true,
      result,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const client = new OpenPpxClient({
      baseUrl: "http://127.0.0.1:18765",
      fetch: fetchMock as unknown as typeof fetch,
      idFactory: () => "request-agent-create",
    });

    await expect(client.agent.create({
      agentId: "research",
      displayName: "Research",
      workspace: null,
      ownerPrincipalId: "ppx-client-user",
      privilegeLevel: "medium",
      modelProfileId: "primary",
    })).resolves.toMatchObject({ result });

    const body = JSON.parse(String((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body));
    expect(body).toMatchObject({
      actionId: "agent.create",
      input: {
        agentId: "research",
        displayName: "Research",
        workspace: null,
        ownerPrincipalId: "ppx-client-user",
        privilegeLevel: "medium",
        modelProfileId: "primary",
      },
    });
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

  it("routes Node Operations through the shared Action boundary", async () => {
    const requests = [actionInvokeOperationsOverview, actionInvokeOperationsAudit];
    const responses = [operationsOverviewSuccess, operationsAuditSuccess];
    let responseIndex = 0;
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify(responses[responseIndex++]),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    const client = new OpenPpxClient({
      baseUrl: "http://127.0.0.1:18765",
      fetch: fetchMock as unknown as typeof fetch,
      idFactory: () => requests[responseIndex].requestId,
    });

    await expect(client.operations.overview()).resolves.toEqual(responses[0]);
    await expect(client.operations.audit()).resolves.toEqual(responses[1]);

    const firstBody = JSON.parse(String((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body));
    const secondBody = JSON.parse(String((fetchMock.mock.calls[1] as unknown as [string, RequestInit])[1].body));
    expect(firstBody).toEqual(actionInvokeOperationsOverview);
    expect(secondBody).toEqual(actionInvokeOperationsAudit);
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
