import type { ActionClient, ActionEnvelope, ActionInvocationOptions } from "./actions";
import { ClientApiProtocolError } from "./contract";

export type ExtensionKind = "plugin" | "app" | "mcp" | "skill";
export type InstallableExtensionKind = "plugin" | "skill";
export type AgentEnablementKind = "plugin" | "mcp" | "skill";

export interface ExtensionSourceRef extends Record<string, unknown> {
  type: "builtin" | "local_directory" | "local_archive" | "git" | "catalog";
  locator: string;
  version?: string;
  revision?: string;
  provider?: string;
  subpath?: string;
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
    type: "builtin" | "local_directory" | "local_archive" | "git" | "catalog" | "direct" | "plugin";
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

export interface ExtensionListFilter {
  kind?: ExtensionKind;
  agentId?: string;
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

  public readiness(kind: ExtensionKind, extensionId: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("extension.readiness", { kind, extensionId });
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

  public createMcp(resource: Record<string, unknown>): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("mcp.create", { resource, expectedRevision: null });
  }

  public updateMcp(resource: Record<string, unknown>, expectedRevision: string): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("mcp.update", { resource, expectedRevision });
  }

  public invokeApp<TResult extends Record<string, unknown>>(
    actionId:
      | "app.definition.install"
      | "app.definition.update"
      | "app.definition.remove"
      | "app.connection.create"
      | "app.connection.update"
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
