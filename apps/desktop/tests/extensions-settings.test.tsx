import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ExtensionsSettings } from "../app/src/components/settings/ExtensionsSettings";
import type { ExtensionDetail, ExtensionStarter, ExtensionSummary, PpxClientApi } from "../app/src/types";

const digest = `sha256:${"a".repeat(64)}`;

function extension(kind: ExtensionSummary["kind"], id: string): ExtensionSummary {
  return {
    kind,
    id,
    displayName: id === "github" ? "GitHub" : "Repository guide",
    description: id === "github" ? "Work with GitHub repositories." : "Repository conventions.",
    version: "1.0.0",
    status: "installed",
    revision: `sha256:${id}`,
    source: { type: kind === "app" ? "plugin" : "local_directory", trust: "local" },
    risk: "medium",
    enabledAgentIds: [],
    readiness: { ready: true, issues: [] },
    presentation: { icon: kind === "app" ? id : kind, brandColor: null },
    managedBy: kind === "app" ? "github-plugin" : null,
  };
}

function renderSettings(items: ExtensionSummary[], kind: ExtensionSummary["kind"] = "plugin") {
  return render(<ExtensionsSettings
    kind={kind}
    extensions={items}
    agents={[{ id: "main", name: "Main", description: "Primary Agent", enabled: true, status: "healthy", tags: [] }]}
    selectedAgentId="main"
    loading={false}
    error={null}
    mutationId={null}
    onRefresh={vi.fn()}
    onSetEnabled={vi.fn()}
  />);
}

describe("Extensions settings", () => {
  beforeEach(() => {
    window.ppxClient = {
      listExtensionStarters: vi.fn().mockResolvedValue({ starters: [], counts: { plugin: 0, app: 0, mcp: 0, skill: 0 } }),
      installAppStarter: vi.fn(),
      previewExtension: vi.fn(),
      installExtension: vi.fn(),
      createMcpServer: vi.fn(),
      updateMcpServer: vi.fn(),
      beginMcpOAuth: vi.fn(),
      getMcpOAuthStatus: vi.fn(),
      signOutMcpOAuth: vi.fn(),
      setExtensionAgentEnabled: vi.fn(),
      openExternalUrl: vi.fn(),
      testMcpServer: vi.fn(),
      getExtension: vi.fn(),
      saveAppConnection: vi.fn(),
      testAppConnection: vi.fn(),
      setAppConnectionAgentEnabled: vi.fn(),
      removeAppConnection: vi.fn(),
      removeExtension: vi.fn(),
    } as unknown as PpxClientApi;
  });

  it("shows curated starters and opens an honest unavailable detail", async () => {
    vi.mocked(window.ppxClient.listExtensionStarters).mockResolvedValue({
      starters: [{
        id: "app-telegram",
        kind: "app",
        runtimeKind: "app",
        displayName: "Telegram",
        description: "Two-way messaging.",
        category: "communication",
        developer: "Telegram",
        availability: "planned",
        installMode: "unavailable",
        auth: "secret",
        requirements: ["OpenPPX product adapter"],
        note: "A verified product adapter is required.",
        featured: true,
        provenance: { project: "OpenWorker", license: "MIT" },
        presentation: { icon: "telegram", brandColor: "#229ed9" },
        template: {},
      }],
      counts: { plugin: 0, app: 1, mcp: 0, skill: 0 },
    });

    renderSettings([], "app");

    expect(await screen.findByText("Telegram")).toBeInTheDocument();
    expect(document.querySelector('[data-extension-icon="telegram"]')).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(await screen.findByText("A verified product adapter is required.")).toBeInTheDocument();
    expect(screen.getAllByText("Not available yet").length).toBeGreaterThan(0);
  });

  it("uses an eight-row starter fold without repeating routine setup metadata", async () => {
    const starters: ExtensionStarter[] = Array.from({ length: 9 }, (_, index) => ({
      id: `app-starter-${index + 1}`,
      kind: "app",
      runtimeKind: "mcp",
      displayName: `Starter ${index + 1}`,
      description: `Starter ${index + 1} description.`,
      category: "communication",
      developer: "OpenPPX",
      availability: "needs_auth",
      installMode: "direct_mcp",
      auth: "secret",
      requirements: ["API token"],
      note: "Credentials are requested during setup.",
      featured: false,
      provenance: { project: "test" },
      presentation: { icon: "app", brandColor: null },
      template: {},
    }));
    vi.mocked(window.ppxClient.listExtensionStarters).mockResolvedValue({
      starters,
      counts: { plugin: 0, app: starters.length, mcp: 0, skill: 0 },
    });

    renderSettings([], "app");

    expect(await screen.findByText("Starter 1")).toBeInTheDocument();
    expect(screen.getByText("1 more ·")).toBeInTheDocument();
    expect(screen.queryByText("Starter 9")).not.toBeInTheDocument();
    expect(screen.queryByText("Authentication required")).not.toBeInTheDocument();
    expect(screen.queryByText("MCP-backed")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show all 9" }));
    expect(await screen.findByText("Starter 9")).toBeInTheDocument();
  });

  it("installs a direct App starter before opening its credential form", async () => {
    const app = extension("app", "telegram");
    const detail: ExtensionDetail = {
      ...app,
      details: {
        credentials: [{ name: "bot-token", label: "Bot token", required: true }],
        tools: [{ name: "telegram_send_message", title: "Send message", description: "Send a message", access: "write", risk: "high", enabledByDefault: true }],
        connections: [],
      },
    };
    vi.mocked(window.ppxClient.listExtensionStarters).mockResolvedValue({
      starters: [{
        id: "app-telegram",
        kind: "app",
        runtimeKind: "app",
        displayName: "Telegram",
        description: "Telegram Bot API.",
        category: "communication",
        developer: "Telegram",
        availability: "needs_auth",
        installMode: "direct_app",
        auth: "secret",
        requirements: ["Telegram bot token"],
        note: "Token remains protected.",
        featured: true,
        provenance: { project: "OpenWorker", license: "MIT" },
        presentation: { icon: "telegram", brandColor: "#229ed9" },
        template: { definition: { metadata: { name: "telegram" } } },
      }],
      counts: { plugin: 0, app: 1, mcp: 0, skill: 0 },
    });
    vi.mocked(window.ppxClient.installAppStarter).mockResolvedValue({
      id: "telegram",
      revision: digest,
      status: "installed",
    });
    vi.mocked(window.ppxClient.getExtension).mockResolvedValue({ extension: detail });

    renderSettings([], "app");
    fireEvent.click(await screen.findByRole("button", { name: "Connect" }));

    await waitFor(() => expect(window.ppxClient.installAppStarter).toHaveBeenCalledWith("app-telegram"));
    expect(await screen.findByLabelText("Bot token *")).toBeInTheDocument();
    expect(screen.getByLabelText(/Connection ID/)).toHaveValue("telegram-default");
  });

  it("prefills a ready MCP starter without exposing a secret value", async () => {
    vi.mocked(window.ppxClient.listExtensionStarters).mockResolvedValue({
      starters: [{
        id: "mcp-context7",
        kind: "mcp",
        runtimeKind: "mcp",
        displayName: "Context7",
        description: "Current library documentation.",
        category: "developer-tools",
        developer: "Upstash",
        availability: "ready",
        installMode: "direct_mcp",
        auth: "none",
        requirements: [],
        note: "",
        featured: true,
        provenance: { project: "nanobot", license: "MIT" },
        presentation: { icon: "context7", brandColor: null },
        template: {
          serverId: "context7",
          displayName: "Context7",
          risk: "low",
          transport: { type: "streamable_http", url: "https://mcp.context7.com/mcp", headers: {} },
        },
      }],
      counts: { plugin: 0, app: 0, mcp: 1, skill: 0 },
    });

    renderSettings([], "mcp");
    fireEvent.click(await screen.findByRole("button", { name: "Use" }));

    expect(screen.getByLabelText(/Server ID/)).toHaveValue("context7");
    expect(screen.getByLabelText("Name")).toHaveValue("Context7");
    expect(screen.getByLabelText("Server URL")).toHaveValue("https://mcp.context7.com/mcp");
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(window.ppxClient.createMcpServer).toHaveBeenCalled());
    expect(vi.mocked(window.ppxClient.createMcpServer).mock.calls[0][0].resource.spec.presentation).toEqual({
      icon: "context7",
      brandColor: null,
    });
  });

  it("connects the Granola starter through explicit browser OAuth before Agent enablement", async () => {
    vi.mocked(window.ppxClient.listExtensionStarters).mockResolvedValue({
      starters: [{
        id: "app-granola",
        kind: "app",
        runtimeKind: "mcp",
        displayName: "Granola",
        description: "Meeting notes and transcripts.",
        category: "meetings",
        developer: "Granola",
        availability: "needs_auth",
        installMode: "direct_mcp",
        auth: "oauth",
        requirements: ["Granola account"],
        note: "Sign in with Granola.",
        featured: true,
        provenance: { project: "OpenWorker", license: "MIT" },
        presentation: { icon: "granola", brandColor: null },
        template: {
          serverId: "granola",
          displayName: "Granola",
          risk: "medium",
          transport: { type: "streamable_http", url: "https://mcp.granola.ai/mcp", headers: {}, auth: "oauth" },
          credentials: [],
        },
      }],
      counts: { plugin: 0, app: 1, mcp: 0, skill: 0 },
    });
    vi.mocked(window.ppxClient.createMcpServer).mockResolvedValue({ revision: digest, status: "disabled" });
    vi.mocked(window.ppxClient.getMcpOAuthStatus)
      .mockResolvedValueOnce({ serverId: "granola", status: "needs_auth", authorizeUrl: "", error: "" })
      .mockResolvedValueOnce({ serverId: "granola", status: "authorizing", authorizeUrl: "https://granola.example/authorize", error: "" })
      .mockResolvedValue({ serverId: "granola", status: "connected", authorizeUrl: "", error: "" });
    vi.mocked(window.ppxClient.beginMcpOAuth).mockResolvedValue({
      serverId: "granola", status: "starting", authorizeUrl: "", error: "",
    });
    vi.mocked(window.ppxClient.setExtensionAgentEnabled).mockResolvedValue({ revision: `${digest}-enabled`, status: "enabled" });

    renderSettings([], "app");
    fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
    expect(screen.getByLabelText("Authentication")).toHaveValue("oauth");
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));

    await waitFor(() => expect(window.ppxClient.openExternalUrl).toHaveBeenCalledWith("https://granola.example/authorize"));
    await waitFor(() => expect(window.ppxClient.setExtensionAgentEnabled).toHaveBeenCalledWith({
      kind: "mcp",
      extensionId: "granola",
      agentId: "main",
      expectedRevision: digest,
      enabled: true,
    }));
    expect(vi.mocked(window.ppxClient.createMcpServer).mock.calls[0][0].resource.spec.transport).toMatchObject({
      type: "streamable_http",
      auth: "oauth",
      url: "https://mcp.granola.ai/mcp",
    });
  });

  it("renders only the requested type without duplicate type navigation", () => {
    renderSettings([], "mcp");

    expect(screen.getByRole("heading", { name: "MCP Servers" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Extension types" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Overview" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add MCP server" }).length).toBeGreaterThan(0);
  });

  it("previews an exact Skill source before installing it", async () => {
    vi.mocked(window.ppxClient.previewExtension).mockResolvedValue({
      kind: "skill",
      preview: { digest, risk: "low", displayName: "Repository guide", description: "Verified package" },
    });
    vi.mocked(window.ppxClient.installExtension).mockResolvedValue({ revision: digest, status: "installed" });
    renderSettings([], "skill");
    fireEvent.click(screen.getAllByRole("button", { name: "Add Skill" })[0]);
    fireEvent.change(screen.getByLabelText("Repository URL"), { target: { value: "https://github.com/openppx/repo-guide" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));

    expect(await screen.findByText("Verified preview")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Install" }));
    await waitFor(() => expect(window.ppxClient.installExtension).toHaveBeenCalledWith({
      kind: "skill",
      source: { type: "git", locator: "https://github.com/openppx/repo-guide" },
      expectedDigest: digest,
      expectedRevision: null,
    }));
  });

  it("creates a direct MCP server with write-only secret aliases", async () => {
    vi.mocked(window.ppxClient.createMcpServer).mockResolvedValue({ revision: digest, status: "installed" });
    renderSettings([], "mcp");
    fireEvent.click(screen.getAllByRole("button", { name: "Add MCP server" })[0]);
    fireEvent.change(screen.getByLabelText(/Server ID/), { target: { value: "github-tools" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "GitHub tools" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Repository tools" } });
    fireEvent.change(screen.getByLabelText("Command"), { target: { value: "npx" } });
    fireEvent.change(screen.getByLabelText(/Environment/), { target: { value: "TOKEN=@secret:github-token" } });
    fireEvent.change(screen.getByLabelText(/New secret values/), { target: { value: "github-token=write-only-value" } });
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));

    await waitFor(() => expect(window.ppxClient.createMcpServer).toHaveBeenCalled());
    expect(vi.mocked(window.ppxClient.createMcpServer).mock.calls[0][0]).toMatchObject({
      resource: {
        metadata: { name: "github-tools" },
        spec: { transport: { environment: { TOKEN: { kind: "secret", secretRef: { store: "system", name: "github-token" } } } } },
      },
      secretValues: { "github-token": "write-only-value" },
      expectedRevision: null,
    });
  });

  it("adds a credential-backed connection to an installed App", async () => {
    const app = extension("app", "github");
    const detail: ExtensionDetail = {
      ...app,
      details: {
        credentials: [{ name: "token", label: "Personal access token", required: true }],
        tools: [{ name: "search", title: "Search", description: "Search repositories", access: "read", risk: "low", enabledByDefault: true }],
        connections: [],
      },
    };
    vi.mocked(window.ppxClient.getExtension).mockResolvedValue({ extension: detail });
    vi.mocked(window.ppxClient.saveAppConnection).mockResolvedValue({ revision: digest, status: "ready" });
    renderSettings([app], "app");
    fireEvent.click(screen.getByRole("button", { name: "Connections" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add connection" }));
    fireEvent.change(screen.getByLabelText("Personal access token *"), { target: { value: "write-only-token" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(window.ppxClient.saveAppConnection).toHaveBeenCalledWith({
      appId: "github",
      connectionId: "github-default",
      displayName: "GitHub account",
      enabledTools: ["search"],
      requireConfirmation: true,
      credentialValues: { token: "write-only-token" },
      expectedRevision: null,
    }));
  });

  it("runs a live MCP connection test and renders discovered tools", async () => {
    const mcp = extension("mcp", "repository-guide");
    const detail: ExtensionDetail = {
      ...mcp,
      details: {
        resource: {
          apiVersion: "openppx.io/v1alpha1",
          kind: "McpServer",
          metadata: { name: mcp.id },
          spec: {
            displayName: mcp.displayName,
            description: mcp.description,
            transport: { type: "stdio", command: "python", args: [], environment: {} },
            policy: { toolFilter: [], requireConfirmation: false, progressEvents: true, longTaskProxy: true, inlineBudgetMs: 500 },
            risk: "medium",
            enabledAgentIds: [],
          },
        },
      },
    };
    vi.mocked(window.ppxClient.getExtension).mockResolvedValue({ extension: detail });
    vi.mocked(window.ppxClient.testMcpServer).mockResolvedValue({
      kind: "mcp",
      id: mcp.id,
      revision: mcp.revision,
      checkedAt: "2026-08-04T12:00:00Z",
      ready: true,
      status: "ok",
      transport: "stdio",
      elapsedMs: 12,
      attempts: 1,
      toolCount: 1,
      toolNames: ["mcp_repository_guide_search"],
      issues: [],
      errorKind: null,
      message: "",
    });
    renderSettings([mcp], "mcp");

    fireEvent.click(screen.getByRole("button", { name: /Repository guide Repository conventions/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));

    expect(await screen.findByText("Connected · 1 tool · 12 ms")).toBeInTheDocument();
    expect(screen.getByText("mcp_repository_guide_search")).toBeInTheDocument();
  });

  it("condenses builtin Skill metadata and explains global Agent access", async () => {
    const skill: ExtensionSummary = {
      ...extension("skill", "general"),
      displayName: "general",
      version: "builtin",
      status: "builtin",
      source: { type: "builtin", trust: "builtin" },
    };
    vi.mocked(window.ppxClient.getExtension).mockResolvedValue({
      extension: { ...skill, details: { builtin: true, capabilities: [], dependencies: [] } },
    });
    renderSettings([skill], "skill");

    fireEvent.click(screen.getByRole("button", { name: /general Repository conventions/ }));

    await screen.findByText("Built-in capabilities are available to every Agent.");
    const metadata = document.querySelector(".extension-detail-status");
    expect(metadata).toHaveTextContent("ReadyBuilt inmedium risk");
    expect(metadata).not.toHaveTextContent("builtinbuiltinbuiltin");
    expect(screen.getByRole("button", { name: "Always on" })).toBeDisabled();
  });
});
