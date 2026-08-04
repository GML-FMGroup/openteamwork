import { describe, expect, it } from "vitest";
import {
  validateConnectionSettings,
  validateAgentCreateRequest,
  validateExternalUrl,
  validateIdentifier,
  validateRuntimeCommand,
  validateProviderId,
  validateSendMessageInput,
  validateSetupApplyRequest,
  validateSetupHelloText,
  validateSlashCommandRequest,
} from "../electron/main/ipc-validation";

function setupRequest() {
  return {
    node: {
      apiVersion: "openppx.io/v1alpha1",
      kind: "NodeConfig",
      metadata: { name: "local-node" },
      spec: {
        displayName: "This Mac",
        enabledAgents: ["main"],
        clientApi: { listenHost: "127.0.0.1", port: 18765, authentication: "disabled" },
      },
    },
    agent: {
      apiVersion: "openppx.io/v1alpha1",
      kind: "AgentConfig",
      metadata: { name: "main" },
      spec: {
        displayName: "Main",
        workspace: "/workspace",
        ownerPrincipalId: "desktop-user",
        privilegeLevel: "medium",
        modelPolicy: { defaultProfile: "primary" },
      },
    },
    profile: {
      apiVersion: "openppx.io/v1alpha1",
      kind: "ModelProfile",
      metadata: { name: "primary" },
      spec: {
        provider: "google",
        model: "gemini-2.5-flash",
        credential: { store: "system", name: "primary-api-key" },
        executionLocation: "remote",
        capabilities: ["text", "tool_calling"],
      },
    },
    secret: { ref: { store: "system", name: "primary-api-key" }, value: "secret-value" },
    expectedRevisions: { node: null, agent: null, profile: null },
  };
}

describe("Electron IPC validation", () => {
  it("accepts well-formed renderer requests", () => {
    expect(validateRuntimeCommand("restart")).toBe("restart");
    expect(validateIdentifier("run-1", "Run id")).toBe("run-1");
    expect(validateProviderId("openai_codex")).toBe("openai_codex");
    expect(validateExternalUrl("https://auth.openai.com/codex/device")).toBe("https://auth.openai.com/codex/device");
    expect(validateSendMessageInput({ agentId: "writer", sessionId: "session-1", text: "hello" })).toEqual({
      agentId: "writer",
      sessionId: "session-1",
      text: "hello",
    });
    expect(
      validateSlashCommandRequest({
        rawCommand: "/history 5",
        agentId: "writer",
        sessionId: "session-1",
        runId: null,
      }),
    ).toEqual({
      rawCommand: "/history 5",
      agentId: "writer",
      sessionId: "session-1",
      runId: null,
    });
    expect(
      validateConnectionSettings({
        targetType: "lan",
        targetId: "studio-node",
        targetName: "Studio Node",
        clientApiBaseUrl: "http://192.168.1.8:8765",
        accessToken: "secret",
      }),
    ).toEqual({
      targetType: "lan",
      targetId: "studio-node",
      targetName: "Studio Node",
      clientApiBaseUrl: "http://192.168.1.8:8765",
      accessToken: "secret",
    });
    expect(validateSetupHelloText("Hello OpenPPX")).toBe("Hello OpenPPX");
    expect(validateSetupApplyRequest(setupRequest())).toEqual(setupRequest());
    expect(validateAgentCreateRequest({
      agentId: "research",
      displayName: "Research",
      workspace: "",
      ownerPrincipalId: "renderer-must-not-control-owner",
      privilegeLevel: "medium",
      modelProfileId: "primary",
    })).toEqual({
      agentId: "research",
      displayName: "Research",
      workspace: null,
      privilegeLevel: "medium",
      modelProfileId: "primary",
    });
  });

  it("rejects malformed renderer requests before they reach services", () => {
    expect(() => validateRuntimeCommand("delete")).toThrow("Runtime command");
    expect(() => validateIdentifier("", "Run id")).toThrow("Run id is required");
    expect(() => validateProviderId("OpenAI Codex")).toThrow("Provider id");
    expect(() => validateExternalUrl("file:///tmp/token")).toThrow("must use HTTPS");
    expect(() => validateSendMessageInput({ agentId: "writer", sessionId: [], text: "hello" })).toThrow(
      "Session id must be a string",
    );
    expect(() => validateConnectionSettings({ targetType: "internet" })).toThrow("targetType");
    expect(() => validateSlashCommandRequest({ rawCommand: "status" })).toThrow("start with '/'");
    expect(() => validateSetupHelloText("")).toThrow("Setup Hello is required");
    expect(() => validateSetupApplyRequest({ ...setupRequest(), secret: { ref: { store: "system", name: "Primary Key" }, value: "secret" } })).toThrow("lowercase resource name");
    expect(() => validateAgentCreateRequest({ agentId: "Research Agent", displayName: "Research", privilegeLevel: "medium", modelProfileId: "primary" })).toThrow("lowercase resource name");
  });
});
