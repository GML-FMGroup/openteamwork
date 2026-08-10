import { describe, expect, it } from "vitest";
import {
  validateConnectionSettings,
  validateAgentCreateRequest,
  validateExternalUrl,
  validateGoalRevision,
  validateGoalTransitionOperation,
  validateGoalUpdateRequest,
  validateIdentifier,
  validateRuntimeCommand,
  validateProviderId,
  validateModelProfileId,
  validateModelProfileCreateInput,
  validateModelProfileUpdateInput,
  validateExtensionPreviewRequest,
  validateExtensionInstallRequest,
  validateMcpMutationRequest,
  validateAppConnectionSaveRequest,
  validateArtifactSummaryInput,
  validateArtifactUploadInput,
  validateSendMessageInput,
  validateSetupApplyRequest,
  validateSetupHelloText,
  validateSlashCommandRequest,
  validateUserLoginRequest,
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
        displayName: "Primary",
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
    expect(validateGoalUpdateRequest({
      goalId: "goal-1",
      expectedRevision: 3,
      objective: "Ship the Goal controls",
    })).toEqual({
      goalId: "goal-1",
      expectedRevision: 3,
      objective: "Ship the Goal controls",
    });
    expect(validateGoalTransitionOperation("pause")).toBe("pause");
    expect(validateGoalRevision(3)).toBe(3);
    expect(validateProviderId("openai_codex")).toBe("openai_codex");
    expect(validateModelProfileId("coding-primary")).toBe("coding-primary");
    expect(validateExternalUrl("https://auth.openai.com/codex/device")).toBe("https://auth.openai.com/codex/device");
    expect(validateSendMessageInput({ agentId: "writer", sessionId: "session-1", text: "hello" })).toEqual({
      agentId: "writer",
      sessionId: "session-1",
      text: "hello",
    });
    expect(validateSendMessageInput({
      agentId: "writer",
      sessionId: "session-1",
      text: "",
      artifactRefs: [{ key: "uploads/artifact-1/notes.txt", version: 0 }],
    })).toEqual({
      agentId: "writer",
      sessionId: "session-1",
      text: "",
      artifactRefs: [{ key: "uploads/artifact-1/notes.txt", version: 0 }],
    });
    expect(validateArtifactUploadInput({
      agentId: "writer",
      sessionId: "session-1",
      fileName: "notes.txt",
      mimeType: "text/plain",
      dataBase64: "aGVsbG8=",
    })).toEqual({
      agentId: "writer",
      sessionId: "session-1",
      fileName: "notes.txt",
      mimeType: "text/plain",
      dataBase64: "aGVsbG8=",
    });
    expect(validateArtifactSummaryInput({
      id: "artifact-1",
      key: "uploads/artifact-1/notes.txt",
      fileName: "notes.txt",
      mimeType: "text/plain",
      sizeBytes: 5,
      version: 0,
      source: "user_upload",
      createdAt: "2026-08-04T00:00:00Z",
    })).toEqual({
      id: "artifact-1",
      key: "uploads/artifact-1/notes.txt",
      fileName: "notes.txt",
      mimeType: "text/plain",
      sizeBytes: 5,
      version: 0,
      source: "user_upload",
      createdAt: "2026-08-04T00:00:00Z",
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
    expect(validateUserLoginRequest({
      connection: {
        targetType: "lan",
        targetId: "team-node",
        targetName: "Team Node",
        clientApiBaseUrl: "https://node.example.com",
      },
      email: "user@example.com",
      secret: "correct horse battery staple",
    }).email).toBe("user@example.com");
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
      instruction: "",
      privilegeLevel: "medium",
      modelProfileId: "primary",
    });
    expect(validateModelProfileCreateInput({
      displayName: "  Coding  ",
      providerId: "openai_codex",
      model: "openai-codex/gpt-5.5-codex",
      executionLocation: "remote",
      apiBase: "https://api.example.com/v1",
      capabilities: ["text", "tool_calling", "reasoning"],
      contextWindowTokens: 200000,
      inputCostPerMillionUsd: "1.25",
      outputCostPerMillionUsd: null,
      fallbackProfileIds: ["primary"],
      enabled: true,
      apiKey: "write-only-key",
      profileId: "renderer-must-not-select-id",
      credential: { store: "system", name: "renderer-must-not-control-this" },
    })).toEqual({
      displayName: "Coding",
      providerId: "openai_codex",
      model: "openai-codex/gpt-5.5-codex",
      executionLocation: "remote",
      apiBase: "https://api.example.com/v1",
      capabilities: ["text", "tool_calling", "reasoning"],
      contextWindowTokens: 200000,
      inputCostPerMillionUsd: "1.25",
      outputCostPerMillionUsd: null,
      fallbackProfileIds: ["primary"],
      enabled: true,
      apiKey: "write-only-key",
    });
    expect(validateModelProfileUpdateInput({
      ...validateModelProfileCreateInput({
        displayName: "Coding",
        providerId: "openai_codex",
        model: "openai-codex/gpt-5.5-codex",
        executionLocation: "remote",
        apiBase: null,
        capabilities: ["text"],
        contextWindowTokens: null,
        inputCostPerMillionUsd: null,
        outputCostPerMillionUsd: null,
        fallbackProfileIds: [],
        enabled: true,
        apiKey: null,
      }),
      profileId: "coding-primary",
      expectedRevision: "revision-1",
    })).toMatchObject({ profileId: "coding-primary", expectedRevision: "revision-1" });
    expect(validateExtensionPreviewRequest({
      kind: "skill",
      source: { type: "git", locator: "https://github.com/openppx/example", revision: "main" },
    })).toEqual({
      kind: "skill",
      source: { type: "git", locator: "https://github.com/openppx/example", revision: "main" },
    });
    expect(validateExtensionInstallRequest({
      kind: "plugin",
      source: { type: "catalog", locator: "openppx/github" },
      expectedDigest: `sha256:${"a".repeat(64)}`,
      expectedRevision: null,
    })).toMatchObject({ kind: "plugin", expectedDigest: `sha256:${"a".repeat(64)}`, expectedRevision: null });
    expect(validateMcpMutationRequest({
      resource: {
        apiVersion: "openppx.io/v1alpha1",
        kind: "McpServer",
        metadata: { name: "github-tools" },
        spec: {
          displayName: "GitHub tools",
          description: "Repository access",
          presentation: { icon: "github", brandColor: "#1f2328" },
          transport: {
            type: "stdio",
            command: "npx",
            args: ["-y", "server"],
            environment: { TOKEN: { kind: "secret", secretRef: { store: "system", name: "github-token" } } },
          },
          policy: {
            toolFilter: ["search"],
            toolNamePrefix: "github",
            requireConfirmation: true,
            progressEvents: true,
            longTaskProxy: true,
            inlineBudgetMs: 1500,
          },
          risk: "medium",
          enabledAgentIds: ["main"],
          managedBy: { kind: "plugin", name: "renderer-owned" },
        },
      },
      secretValues: { "github-token": "write-only-value" },
      expectedRevision: null,
    })).toMatchObject({
      resource: { spec: { managedBy: null } },
      secretValues: { "github-token": "write-only-value" },
      expectedRevision: null,
    });
    expect(validateMcpMutationRequest({
      resource: {
        apiVersion: "openppx.io/v1alpha1",
        kind: "McpServer",
        metadata: { name: "browserbase" },
        spec: {
          displayName: "Browserbase",
          description: "Remote browser automation",
          presentation: { icon: "browserbase", brandColor: "#1f2328" },
          transport: {
            type: "streamable_http",
            url: "https://mcp.browserbase.com/mcp",
            headers: {},
            query: { browserbaseApiKey: { kind: "secret", secretRef: { store: "system", name: "browserbase-api-key" } } },
            auth: "oauth",
          },
          policy: { toolFilter: [], requireConfirmation: true, progressEvents: true, longTaskProxy: true, inlineBudgetMs: 1500 },
          risk: "medium",
          enabledAgentIds: [],
        },
      },
      secretValues: { "browserbase-api-key": "write-only-value" },
      expectedRevision: null,
    })).toMatchObject({
      resource: { spec: { transport: { auth: "oauth", query: { browserbaseApiKey: { kind: "secret" } } } } },
    });
    expect(validateAppConnectionSaveRequest({
      appId: "github",
      connectionId: "github-work",
      displayName: "Work GitHub",
      enabledTools: ["search", "issues.create"],
      requireConfirmation: true,
      credentialValues: { token: "write-only-value" },
      expectedRevision: null,
    })).toEqual({
      appId: "github",
      connectionId: "github-work",
      displayName: "Work GitHub",
      enabledTools: ["search", "issues.create"],
      requireConfirmation: true,
      credentialValues: { token: "write-only-value" },
      expectedRevision: null,
    });
  });

  it("rejects invalid Goal mutations", () => {
    expect(() => validateGoalUpdateRequest({
      goalId: "goal-1",
      expectedRevision: 0,
      objective: "",
    })).toThrow();
    expect(() => validateGoalTransitionOperation("complete")).toThrow("Goal transition is not supported");
    expect(() => validateGoalRevision("latest")).toThrow();
  });

  it("rejects malformed renderer requests before they reach services", () => {
    expect(() => validateRuntimeCommand("delete")).toThrow("Runtime command");
    expect(() => validateIdentifier("", "Run id")).toThrow("Run id is required");
    expect(() => validateProviderId("OpenAI Codex")).toThrow("Provider id");
    expect(() => validateModelProfileId("Primary Profile")).toThrow("lowercase resource name");
    expect(() => validateExternalUrl("file:///tmp/token")).toThrow("must use HTTPS");
    expect(() => validateSendMessageInput({ agentId: "writer", sessionId: [], text: "hello" })).toThrow(
      "Session id must be a string",
    );
    expect(() => validateSendMessageInput({
      agentId: "writer",
      sessionId: "session-1",
      text: "hello",
      artifactRefs: [{ key: "artifact", version: -1 }],
    })).toThrow("Artifact version");
    expect(() => validateArtifactUploadInput({
      agentId: "writer",
      sessionId: "session-1",
      fileName: "notes.txt",
      mimeType: "text/plain",
      dataBase64: "x".repeat(28_000_001),
    })).toThrow("Artifact content");
    expect(() => validateConnectionSettings({ targetType: "internet" })).toThrow("targetType");
    expect(() => validateUserLoginRequest({
      connection: {
        targetType: "lan",
        targetId: "team-node",
        targetName: "Team Node",
        clientApiBaseUrl: "http://node.example.com:18765",
      },
      email: "user@example.com",
      secret: "correct horse battery staple",
    })).toThrow("requires an HTTPS Node URL");
    expect(() => validateSlashCommandRequest({ rawCommand: "status" })).toThrow("start with '/'");
    expect(() => validateSetupHelloText("")).toThrow("Setup Hello is required");
    expect(() => validateSetupApplyRequest({ ...setupRequest(), secret: { ref: { store: "system", name: "Primary Key" }, value: "secret" } })).toThrow("lowercase resource name");
    expect(() => validateAgentCreateRequest({ agentId: "Research Agent", displayName: "Research", privilegeLevel: "medium", modelProfileId: "primary" })).toThrow("lowercase resource name");
    expect(() => validateModelProfileCreateInput({
      displayName: "Primary",
      providerId: "google",
      model: "gemini-2.5-flash",
      executionLocation: "remote",
      apiBase: null,
      capabilities: ["text", "made_up"],
      contextWindowTokens: null,
      inputCostPerMillionUsd: null,
      outputCostPerMillionUsd: null,
      fallbackProfileIds: [],
      enabled: true,
      apiKey: null,
    })).toThrow("capability is not supported");
    expect(() => validateExtensionPreviewRequest({ kind: "app", source: { type: "git", locator: "https://example.com/app" } })).toThrow("kind");
    expect(() => validateMcpMutationRequest({
      resource: {
        apiVersion: "openppx.io/v1alpha1",
        kind: "McpServer",
        metadata: { name: "unsafe" },
        spec: {
          displayName: "Unsafe",
          description: "Unsafe MCP",
          presentation: { icon: "mcp", brandColor: null },
          transport: { type: "stdio", command: "node", args: [], environment: { TOKEN: { kind: "secret", secretRef: { store: "renderer", name: "token" } } } },
          policy: { toolFilter: [], requireConfirmation: true, progressEvents: true, longTaskProxy: true, inlineBudgetMs: 1500 },
          risk: "high",
          enabledAgentIds: [],
        },
      },
      secretValues: {},
      expectedRevision: null,
    })).toThrow("SecretRef store must be system");
  });
});
