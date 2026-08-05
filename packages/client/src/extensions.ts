import type { ActionClient, ActionEnvelope, ActionInvocationOptions } from "./actions";
import { ClientApiProtocolError } from "./contract";

export type ExtensionKind = "plugin" | "app" | "mcp" | "skill";
export type InstallableExtensionKind = "plugin" | "skill";
export type AgentEnablementKind = "plugin" | "mcp" | "skill";

export interface ExtensionSourceRef extends Record<string, unknown> {
  type: "builtin" | "local_directory" | "local_archive" | "git" | "npm" | "catalog";
  locator: string;
  version?: string;
  revision?: string;
  provider?: string;
  subpath?: string;
}

export interface PluginHookStatus extends Record<string, unknown> {
  pluginId: string;
  pluginRevision: string;
  pluginDigest: string;
  hookDigest: string;
  trusted: boolean;
  declaredEvents: string[];
  supportedEvents: string[];
  handlerCount: number;
  executableCount: number;
  unsupportedHandlers: number;
  handlers: Array<{
    event: string;
    matcher: string;
    type: "command" | "prompt" | "agent";
    command: string | null;
    timeout: number;
    async: boolean;
    supported: boolean;
  }>;
}

export interface PluginMarketplaceSourceSpec extends Record<string, unknown> {
  displayName: string;
  type: "local" | "git";
  locator: string;
  ref: string;
}

export interface PluginMarketplaceSource extends PluginMarketplaceSourceSpec {
  id: string;
  revision: string;
  resolvedRevision: string | null;
  catalogDigest: string | null;
  entryCount: number;
  refreshedAt: string | null;
  ready: boolean;
}

export interface PluginMarketplaceEntry extends Record<string, unknown> {
  marketplaceId: string;
  pluginId: string;
  displayName: string;
  description: string;
  version: string;
  developer: string;
  category: string;
  installationPolicy: string;
  authenticationPolicy: string;
  sourceKind: string;
  source: ExtensionSourceRef | null;
  ready: boolean;
  issue: string | null;
}

export interface ExtensionSummary extends Record<string, unknown> {
  kind: ExtensionKind;
  id: string;
  displayName: string;
  description: string;
  version: string;
  status: string;
  revision: string;
  source: {
    type: "builtin" | "local_directory" | "local_archive" | "git" | "npm" | "catalog" | "direct" | "plugin";
    trust: "builtin" | "local" | "third_party";
  };
  risk: "low" | "medium" | "high";
  enabledAgentIds: string[];
  readiness: { ready: boolean; issues: string[] };
  managedBy: string | null;
}

export interface ExtensionDetail extends ExtensionSummary {
  details: Record<string, unknown>;
}

export interface ExtensionPreview extends Record<string, unknown> {
  kind: InstallableExtensionKind;
  preview: Record<string, unknown> & { digest: string; risk: "low" | "medium" | "high" };
}

export interface ExtensionReadinessResult extends Record<string, unknown> {
  kind: ExtensionKind;
  id: string;
  ready: boolean;
  issues: string[];
  status: string;
  revision: string;
}

export interface ExtensionProbeResult extends Record<string, unknown> {
  kind: "mcp" | "app_connection";
  id: string;
  revision: string;
  checkedAt: string;
  ready: boolean;
  status: "ok" | "blocked" | "timeout" | "error";
  transport: string;
  elapsedMs: number;
  attempts: number;
  toolCount: number;
  toolNames: string[];
  issues: string[];
  errorKind: string | null;
  message: string;
}

export interface McpOAuthStatus extends Record<string, unknown> {
  serverId: string;
  status: "needs_auth" | "starting" | "authorizing" | "connected" | "error";
  authorizeUrl: string;
  error: string;
}

export interface SecretRef extends Record<string, unknown> {
  store: "system";
  name: string;
}

export type McpValueBinding =
  | { kind: "literal"; value: string }
  | { kind: "secret"; secretRef: SecretRef; prefix?: string; suffix?: string }
  | { kind: "environment"; name: string; prefix?: string; suffix?: string };

export interface McpToolPolicy extends Record<string, unknown> {
  toolFilter: string[];
  disabledTools?: string[];
  toolNamePrefix?: string | null;
  requireConfirmation: boolean;
  runtimeHeaders?: Record<string, string>;
  progressEvents: boolean;
  longTaskProxy: boolean;
  inlineBudgetMs: number;
  jobProtocol?: Record<string, unknown> | null;
}

export interface McpServerResource extends Record<string, unknown> {
  apiVersion: "openppx.io/v1alpha1";
  kind: "McpServer";
  metadata: {
    name: string;
    labels?: Record<string, string>;
    annotations?: Record<string, string>;
  };
  spec: {
    displayName: string;
    description: string;
    transport:
      | {
          type: "stdio";
          command: string;
          args: string[];
          cwd?: string | null;
          environment: Record<string, McpValueBinding>;
        }
      | {
          type: "streamable_http" | "sse";
          url: string;
          headers: Record<string, McpValueBinding>;
          query?: Record<string, McpValueBinding>;
          auth?: "none" | "oauth";
        };
    policy: McpToolPolicy;
    risk: "low" | "medium" | "high";
    enabledAgentIds: string[];
    managedBy?: { kind: "plugin" | "app"; name: string } | null;
  };
}

export interface AppCredentialSpec extends Record<string, unknown> {
  name: string;
  label: string;
  required: boolean;
}

export interface AppToolSpec extends Record<string, unknown> {
  name: string;
  title: string;
  description: string;
  access: "read" | "write";
  risk: "low" | "medium" | "high";
  enabledByDefault: boolean;
}

export interface AppConnectionDetail extends Record<string, unknown> {
  id: string;
  appId: string;
  displayName: string;
  status: string;
  revision: string;
  authState: string;
  ready: boolean;
  issues: string[];
  credentialRefs: Record<string, SecretRef>;
  enabledAgentIds: string[];
  enabledTools: string[] | null;
  requiresConfirmation: boolean;
}

export interface AppConnectionResource extends Record<string, unknown> {
  apiVersion: "openppx.io/v1alpha1";
  kind: "AppConnection";
  metadata: { name: string };
  spec: {
    appId: string;
    displayName: string;
    credentialRefs: Record<string, SecretRef>;
    enabledTools: string[] | null;
    requireConfirmation: boolean;
    enabledAgentIds: string[];
  };
}

export interface ExtensionListFilter {
  kind?: ExtensionKind;
  agentId?: string;
}

export type ExtensionStarterAvailability = "ready" | "needs_auth" | "needs_dependency" | "planned";
export type ExtensionStarterInstallMode = "direct_app" | "direct_mcp" | "source" | "builtin" | "reference" | "unavailable";

export interface ExtensionStarter extends Record<string, unknown> {
  id: string;
  kind: ExtensionKind;
  runtimeKind: ExtensionKind;
  displayName: string;
  description: string;
  category: string;
  developer: string;
  availability: ExtensionStarterAvailability;
  installMode: ExtensionStarterInstallMode;
  auth: "none" | "secret" | "oauth";
  requirements: string[];
  note: string;
  featured: boolean;
  provenance: Record<string, string>;
  template: Record<string, unknown>;
}

export interface ExtensionStarterListFilter {
  kind?: ExtensionKind;
  query?: string;
}

function parseExtensionStarter(value: unknown): ExtensionStarter {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ClientApiProtocolError("Extension starter result contains a non-object item.");
  }
  const item = value as Record<string, unknown>;
  if (
    typeof item.id !== "string" ||
    !["plugin", "app", "mcp", "skill"].includes(String(item.kind)) ||
    !["plugin", "app", "mcp", "skill"].includes(String(item.runtimeKind)) ||
    typeof item.displayName !== "string" ||
    typeof item.description !== "string" ||
    typeof item.category !== "string" ||
    typeof item.developer !== "string" ||
    !["ready", "needs_auth", "needs_dependency", "planned"].includes(String(item.availability)) ||
    !["direct_app", "direct_mcp", "source", "builtin", "reference", "unavailable"].includes(String(item.installMode)) ||
    !["none", "secret", "oauth"].includes(String(item.auth)) ||
    !Array.isArray(item.requirements) ||
    item.requirements.some((entry) => typeof entry !== "string") ||
    typeof item.note !== "string" ||
    typeof item.featured !== "boolean" ||
    !item.provenance || typeof item.provenance !== "object" || Array.isArray(item.provenance) ||
    !item.template || typeof item.template !== "object" || Array.isArray(item.template)
  ) {
    throw new ClientApiProtocolError("Extension starter result does not match the v1 catalog contract.");
  }
  return item as unknown as ExtensionStarter;
}

function parseExtensionSummary(value: unknown): ExtensionSummary {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ClientApiProtocolError("Extension result contains a non-object item.");
  }
  const item = value as Record<string, unknown>;
  const source = item.source as Record<string, unknown> | undefined;
  const readiness = item.readiness as Record<string, unknown> | undefined;
  if (
    !["plugin", "app", "mcp", "skill"].includes(String(item.kind)) ||
    typeof item.id !== "string" ||
    typeof item.displayName !== "string" ||
    typeof item.description !== "string" ||
    typeof item.version !== "string" ||
    typeof item.status !== "string" ||
    typeof item.revision !== "string" ||
    !source ||
    typeof source.type !== "string" ||
    typeof source.trust !== "string" ||
    !["low", "medium", "high"].includes(String(item.risk)) ||
    !Array.isArray(item.enabledAgentIds) ||
    !readiness ||
    typeof readiness.ready !== "boolean" ||
    !Array.isArray(readiness.issues)
  ) {
    throw new ClientApiProtocolError("Extension result does not match the v1 inventory contract.");
  }
  return item as unknown as ExtensionSummary;
}

function parseExtensionProbe(value: unknown): ExtensionProbeResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ClientApiProtocolError("Extension probe result is not an object.");
  }
  const result = value as Record<string, unknown>;
  if (
    !["mcp", "app_connection"].includes(String(result.kind)) ||
    typeof result.id !== "string" ||
    typeof result.revision !== "string" ||
    typeof result.checkedAt !== "string" ||
    typeof result.ready !== "boolean" ||
    !["ok", "blocked", "timeout", "error"].includes(String(result.status)) ||
    typeof result.transport !== "string" ||
    typeof result.elapsedMs !== "number" ||
    typeof result.attempts !== "number" ||
    typeof result.toolCount !== "number" ||
    !Array.isArray(result.toolNames) ||
    result.toolNames.some((item) => typeof item !== "string") ||
    !Array.isArray(result.issues) ||
    result.issues.some((item) => typeof item !== "string") ||
    (result.errorKind !== null && typeof result.errorKind !== "string") ||
    typeof result.message !== "string"
  ) {
    throw new ClientApiProtocolError("Extension probe result does not match the v1 contract.");
  }
  return result as unknown as ExtensionProbeResult;
}

/** Four-domain Extension client over the shared Action transport. */
export class ExtensionClient {
  public constructor(private readonly actions: ActionClient) {}

  public async list(filter: ExtensionListFilter = {}): Promise<ActionEnvelope<{ items: ExtensionSummary[] }>> {
    const envelope = await this.actions.invoke<Record<string, unknown>, { items: ExtensionSummary[] }>(
      "extension.list",
      { kind: filter.kind ?? null, agentId: filter.agentId ?? null },
    );
    if (!Array.isArray(envelope.result.items)) {
      throw new ClientApiProtocolError("Extension list result is missing items.");
    }
    return {
      ...envelope,
      result: { items: envelope.result.items.map(parseExtensionSummary) },
    };
  }

  public async listStarters(
    filter: ExtensionStarterListFilter = {},
  ): Promise<ActionEnvelope<{ items: ExtensionStarter[]; counts: Record<ExtensionKind, number> }>> {
    const envelope = await this.actions.invoke<Record<string, unknown>, { items: ExtensionStarter[]; counts: Record<ExtensionKind, number> }>(
      "extension.starter.list",
      { kind: filter.kind ?? null, query: filter.query ?? null },
    );
    if (!Array.isArray(envelope.result.items) || !envelope.result.counts || typeof envelope.result.counts !== "object") {
      throw new ClientApiProtocolError("Extension starter list result is missing catalog data.");
    }
    return {
      ...envelope,
      result: {
        items: envelope.result.items.map(parseExtensionStarter),
        counts: envelope.result.counts,
      },
    };
  }

  public async getStarter(starterId: string): Promise<ActionEnvelope<ExtensionStarter>> {
    const envelope = await this.actions.invoke<Record<string, unknown>, ExtensionStarter>(
      "extension.starter.get",
      { starterId },
    );
    return { ...envelope, result: parseExtensionStarter(envelope.result) };
  }

  public installAppStarter(starterId: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("app.starter.install", { starterId });
  }

  public async get(kind: ExtensionKind, extensionId: string): Promise<ActionEnvelope<ExtensionDetail>> {
    const envelope = await this.actions.invoke<Record<string, unknown>, ExtensionDetail>(
      "extension.get",
      { kind, extensionId },
    );
    const summary = parseExtensionSummary(envelope.result);
    if (!envelope.result.details || typeof envelope.result.details !== "object" || Array.isArray(envelope.result.details)) {
      throw new ClientApiProtocolError("Extension detail result is missing details.");
    }
    return { ...envelope, result: { ...summary, details: envelope.result.details } };
  }

  public readiness(kind: ExtensionKind, extensionId: string): Promise<ActionEnvelope<ExtensionReadinessResult>> {
    return this.actions.invoke("extension.readiness", { kind, extensionId });
  }

  public getPluginHookStatus(pluginId: string, expectedRevision: string): Promise<ActionEnvelope<PluginHookStatus>> {
    return this.actions.invoke("plugin.hooks.status", { pluginId, expectedRevision });
  }

  public trustPluginHooks(
    pluginId: string,
    expectedRevision: string,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<PluginHookStatus>> {
    return this.actions.invoke(
      "plugin.hooks.trust",
      { pluginId, expectedRevision },
      { ...options, confirmed: options.confirmed ?? true },
    );
  }

  public untrustPluginHooks(pluginId: string, expectedRevision: string): Promise<ActionEnvelope<PluginHookStatus>> {
    return this.actions.invoke("plugin.hooks.untrust", { pluginId, expectedRevision });
  }

  public listPluginMarketplaces(): Promise<ActionEnvelope<{ items: PluginMarketplaceSource[] }>> {
    return this.actions.invoke("plugin.marketplace.source.list", { query: null });
  }

  public listPluginMarketplaceEntries(query?: string): Promise<ActionEnvelope<{ items: PluginMarketplaceEntry[] }>> {
    return this.actions.invoke("plugin.marketplace.entry.list", { query: query ?? null });
  }

  public createPluginMarketplace(
    marketplaceId: string,
    spec: PluginMarketplaceSourceSpec,
  ): Promise<ActionEnvelope<PluginMarketplaceSource>> {
    return this.actions.invoke("plugin.marketplace.source.create", { marketplaceId, spec, expectedRevision: null });
  }

  public updatePluginMarketplace(
    marketplaceId: string,
    spec: PluginMarketplaceSourceSpec,
    expectedRevision: string,
  ): Promise<ActionEnvelope<PluginMarketplaceSource>> {
    return this.actions.invoke("plugin.marketplace.source.update", { marketplaceId, spec, expectedRevision });
  }

  public refreshPluginMarketplace(
    marketplaceId: string,
    expectedRevision: string,
  ): Promise<ActionEnvelope<PluginMarketplaceSource>> {
    return this.actions.invoke("plugin.marketplace.source.refresh", { marketplaceId, expectedRevision });
  }

  public removePluginMarketplace(
    marketplaceId: string,
    expectedRevision: string,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke(
      "plugin.marketplace.source.remove",
      { marketplaceId, expectedRevision },
      { ...options, confirmed: options.confirmed ?? true },
    );
  }

  public preview(kind: InstallableExtensionKind, source: ExtensionSourceRef): Promise<ActionEnvelope<ExtensionPreview>> {
    return this.actions.invoke("extension.preview", { kind, source });
  }

  public install(
    kind: InstallableExtensionKind,
    source: ExtensionSourceRef,
    expectedDigest: string,
    expectedRevision: string | null,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke(
      "extension.install",
      { kind, source, expectedDigest, expectedRevision },
      { ...options, confirmed: options.confirmed ?? true },
    );
  }

  public enable(
    kind: AgentEnablementKind,
    extensionId: string,
    agentId: string,
    expectedRevision: string,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke(
      "extension.enable",
      { kind, extensionId, agentId, expectedRevision },
      { ...options, confirmed: options.confirmed ?? true },
    );
  }

  public disable(
    kind: AgentEnablementKind,
    extensionId: string,
    agentId: string,
    expectedRevision: string,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("extension.disable", { kind, extensionId, agentId, expectedRevision }, options);
  }

  public remove(
    kind: AgentEnablementKind,
    extensionId: string,
    expectedRevision: string,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke(
      "extension.remove",
      { kind, extensionId, expectedRevision },
      { ...options, confirmed: options.confirmed ?? true },
    );
  }

  public createMcp(resource: McpServerResource): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("mcp.create", { resource, expectedRevision: null });
  }

  public updateMcp(resource: McpServerResource, expectedRevision: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("mcp.update", { resource, expectedRevision });
  }

  public beginMcpOAuth(serverId: string, callbackBase: string): Promise<ActionEnvelope<McpOAuthStatus>> {
    return this.actions.invoke("mcp.oauth.begin", { serverId, callbackBase });
  }

  public getMcpOAuthStatus(serverId: string): Promise<ActionEnvelope<McpOAuthStatus>> {
    return this.actions.invoke("mcp.oauth.status", { serverId });
  }

  public signOutMcpOAuth(
    serverId: string,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<McpOAuthStatus>> {
    return this.actions.invoke(
      "mcp.oauth.signout",
      { serverId },
      { ...options, confirmed: options.confirmed ?? true },
    );
  }

  public async testMcp(serverId: string): Promise<ActionEnvelope<ExtensionProbeResult>> {
    const envelope = await this.actions.invoke<Record<string, unknown>, ExtensionProbeResult>(
      "mcp.test",
      { serverId },
    );
    return { ...envelope, result: parseExtensionProbe(envelope.result) };
  }

  public createAppConnection(resource: AppConnectionResource): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("app.connection.create", { resource, expectedRevision: null });
  }

  public updateAppConnection(
    resource: AppConnectionResource,
    expectedRevision: string,
  ): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("app.connection.update", { resource, expectedRevision });
  }

  public reauthorizeAppConnection(
    connectionId: string,
    credentialRefs: Record<string, SecretRef>,
    expectedRevision: string,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke(
      "app.connection.reauthorize",
      { connectionId, credentialRefs, expectedRevision },
      { ...options, confirmed: options.confirmed ?? true },
    );
  }

  public async testAppConnection(connectionId: string): Promise<ActionEnvelope<ExtensionProbeResult>> {
    const envelope = await this.actions.invoke<Record<string, unknown>, ExtensionProbeResult>(
      "app.connection.test",
      { connectionId },
    );
    return { ...envelope, result: parseExtensionProbe(envelope.result) };
  }

  public setAppConnectionEnabled(
    connectionId: string,
    agentId: string,
    expectedRevision: string,
    enabled: boolean,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke(
      enabled ? "app.connection.enable" : "app.connection.disable",
      { connectionId, agentId, expectedRevision },
      enabled ? { ...options, confirmed: options.confirmed ?? true } : options,
    );
  }

  public removeAppConnection(
    connectionId: string,
    expectedRevision: string,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke(
      "app.connection.remove",
      { connectionId, expectedRevision },
      { ...options, confirmed: options.confirmed ?? true },
    );
  }

  public invokeApp<TResult extends Record<string, unknown>>(
    actionId:
      | "app.definition.install"
      | "app.definition.update"
      | "app.definition.remove"
      | "app.connection.create"
      | "app.connection.update"
      | "app.connection.test"
      | "app.connection.reauthorize"
      | "app.connection.enable"
      | "app.connection.disable"
      | "app.connection.remove",
    input: Record<string, unknown>,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<TResult>> {
    return this.actions.invoke(actionId, input, options);
  }
}
