import { CLIENT_API_PROTOCOL_VERSION } from "./contract";
import { ClientApiProtocolError } from "./contract";
import { ClientApiHttpTransport, type ClientApiHttpTransportOptions } from "./http-transport";

export type ActionScope = "node" | "agent" | "session" | "run" | "task" | "extension";
export type ActionRisk = "low" | "medium" | "high";
export type ActionProjection = "cli" | "slash" | "desktop" | "mobile";

export interface ActionCatalogItem {
  actionId: string;
  namespace: string;
  title: string;
  description: string;
  scope: ActionScope;
  inputSchema: Record<string, unknown>;
  requiredCapabilities: string[];
  permission: string;
  risk: ActionRisk;
  confirmation: "never" | "required";
  execution: "sync" | "long_running";
  projections: ActionProjection[];
  available: boolean;
  availabilityReason: string | null;
}

export interface ActionEnvelope<TResult extends Record<string, unknown>> {
  protocolVersion: number;
  requestId: string;
  correlationId: string;
  ok: true;
  result: TResult;
}

export interface ActionInvocationOptions {
  confirmed?: boolean;
  requestId?: string;
  correlationId?: string;
}

export interface SessionNewResult extends Record<string, unknown> {
  session: {
    id: string;
    agentId: string;
    subjectPrincipalId: string;
    title: string;
    updatedAt: string;
    lastMessagePreview: string;
    archived: boolean;
  };
}

export interface RunStopResult extends Record<string, unknown> {
  run: {
    id: string;
    agentId: string;
    sessionId: string;
    snapshotRevision: string;
    startedAt: string;
    state: string;
  };
}

export interface ModelSelectionInput extends Record<string, unknown> {
  agentId: string;
  role?: "fast" | "reasoning" | "vision";
  runOverride?: string;
  requiredCapabilities?: string[];
  privacy?: "any" | "local_only";
  minContextTokens?: number;
  maxInputCostPerMillionUsd?: string;
  maxOutputCostPerMillionUsd?: string;
}

export interface ActionClientOptions {
  idFactory?: () => string;
}

export interface ActionTransport {
  requestJson(pathname: string, init?: RequestInit): Promise<Record<string, unknown>>;
}

let fallbackId = 0;

function nextWireId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  fallbackId += 1;
  return `client-${Date.now().toString(36)}-${fallbackId.toString(36)}`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

/** Parse one successful common Action envelope without weakening the shared wire contract. */
export function parseActionEnvelope<TResult extends Record<string, unknown>>(
  payload: unknown,
): ActionEnvelope<TResult> {
  const envelope = asRecord(payload);
  const result = asRecord(envelope?.result);
  if (
    envelope?.ok !== true ||
    envelope.protocolVersion !== CLIENT_API_PROTOCOL_VERSION ||
    typeof envelope.requestId !== "string" ||
    !envelope.requestId ||
    typeof envelope.correlationId !== "string" ||
    !envelope.correlationId ||
    !result
  ) {
    throw new ClientApiProtocolError("Action response is not a successful compatible v1 envelope.");
  }
  return {
    protocolVersion: CLIENT_API_PROTOCOL_VERSION,
    requestId: envelope.requestId,
    correlationId: envelope.correlationId,
    ok: true,
    result: result as TResult,
  };
}

/** Transport-neutral entry point for catalog discovery and typed Action invocation. */
export class ActionClient {
  private readonly idFactory: () => string;

  public constructor(
    private readonly transport: ActionTransport,
    options: ActionClientOptions = {},
  ) {
    this.idFactory = options.idFactory ?? nextWireId;
  }

  public async catalog(namespace?: string): Promise<ActionCatalogItem[]> {
    const query = namespace ? `?namespace=${encodeURIComponent(namespace)}` : "";
    const envelope = parseActionEnvelope<{ items: ActionCatalogItem[] }>(
      await this.transport.requestJson(`/api/v1/actions${query}`),
    );
    if (!Array.isArray(envelope.result.items)) {
      throw new ClientApiProtocolError("Action catalog result is missing items.");
    }
    return envelope.result.items;
  }

  public async invoke<TInput extends Record<string, unknown>, TResult extends Record<string, unknown>>(
    actionId: string,
    input: TInput,
    options: ActionInvocationOptions = {},
  ): Promise<ActionEnvelope<TResult>> {
    const requestId = options.requestId ?? this.idFactory();
    const correlationId = options.correlationId ?? requestId;
    return parseActionEnvelope<TResult>(
      await this.transport.requestJson("/api/v1/actions/invoke", {
        method: "POST",
        body: JSON.stringify({
          requestId,
          correlationId,
          actionId,
          input,
          confirmed: options.confirmed ?? false,
        }),
      }),
    );
  }
}

export class SystemClient {
  public constructor(private readonly actions: ActionClient) {}

  public status(): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("system.status", {});
  }
}

export class ModelClient {
  public constructor(private readonly actions: ActionClient) {}

  public list(): Promise<ActionEnvelope<{ items: Array<Record<string, unknown>> }>> {
    return this.actions.invoke("model.list", {});
  }

  public readiness(input: ModelSelectionInput): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("model.readiness", input);
  }

  public select(input: ModelSelectionInput): Promise<ActionEnvelope<Record<string, unknown>>> {
    return this.actions.invoke("model.select", input);
  }
}

export class SessionClient {
  public constructor(private readonly actions: ActionClient) {}

  public create(agentId: string, userId: string): Promise<ActionEnvelope<SessionNewResult>> {
    return this.actions.invoke("session.new", { agentId, userId });
  }
}

export class RunClient {
  public constructor(private readonly actions: ActionClient) {}

  public stop(runId: string): Promise<ActionEnvelope<RunStopResult>> {
    return this.actions.invoke("run.stop", { runId });
  }
}

/** Public TypeScript SDK facade shared by Desktop and future Mobile clients. */
export class OpenPpxClient {
  public readonly transport: ClientApiHttpTransport;

  public readonly actions: ActionClient;

  public readonly system: SystemClient;

  public readonly model: ModelClient;

  public readonly session: SessionClient;

  public readonly run: RunClient;

  public constructor(options: ClientApiHttpTransportOptions & ActionClientOptions) {
    this.transport = new ClientApiHttpTransport(options);
    this.actions = new ActionClient(this.transport, options);
    this.system = new SystemClient(this.actions);
    this.model = new ModelClient(this.actions);
    this.session = new SessionClient(this.actions);
    this.run = new RunClient(this.actions);
  }
}
