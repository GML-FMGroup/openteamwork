export interface ClientApiSseEvent {
  id?: string;
  event: string;
  data: Record<string, unknown>;
}

export interface ClientApiRunStreamResult {
  lastEventId?: string;
  eventCount: number;
}

export interface ClientApiRunStreamOptions {
  request: (pathname: string, init?: RequestInit) => Promise<Response>;
  maxReconnectAttempts?: number;
  reconnectDelayMs?: number;
  wait?: (delayMs: number) => Promise<void>;
}

export interface ConsumeRunStreamOptions {
  lastEventId?: string;
  signal?: AbortSignal;
}

function parseFrame(frame: string): ClientApiSseEvent | null {
  let id: string | undefined;
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) {
      continue;
    }
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const rawValue = separator === -1 ? "" : line.slice(separator + 1);
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;
    if (field === "id") {
      id = value;
    } else if (field === "event") {
      event = value || "message";
    } else if (field === "data") {
      dataLines.push(value);
    }
  }
  if (!dataLines.length) {
    return null;
  }
  const payload = JSON.parse(dataLines.join("\n")) as unknown;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error(`Client API SSE event ${event} did not contain a JSON object.`);
  }
  return { id, event, data: payload as Record<string, unknown> };
}

class RunStreamHttpError extends Error {
  public constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

function shouldReconnect(error: unknown, signal?: AbortSignal): boolean {
  if (signal?.aborted) {
    return false;
  }
  if (error instanceof RunStreamHttpError) {
    return error.status >= 500;
  }
  return !(error instanceof SyntaxError);
}

/** Consume one authenticated Client API Run SSE response as structured events. */
export class ClientApiRunStream {
  private readonly request: ClientApiRunStreamOptions["request"];

  private readonly maxReconnectAttempts: number;

  private readonly reconnectDelayMs: number;

  private readonly wait: (delayMs: number) => Promise<void>;

  public constructor(options: ClientApiRunStreamOptions) {
    this.request = options.request;
    this.maxReconnectAttempts = Math.max(0, options.maxReconnectAttempts ?? 3);
    this.reconnectDelayMs = Math.max(0, options.reconnectDelayMs ?? 250);
    this.wait = options.wait ?? ((delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs)));
  }

  public async consume(
    runId: string,
    onEvent: (event: ClientApiSseEvent) => void,
    options: ConsumeRunStreamOptions = {},
  ): Promise<ClientApiRunStreamResult> {
    let lastEventId = options.lastEventId;
    let eventCount = 0;
    const deliveredEventIds = new Set<string>();
    let reconnectAttempts = 0;

    while (true) {
      try {
        const headers: Record<string, string> = { Accept: "text/event-stream" };
        if (lastEventId) {
          headers["Last-Event-ID"] = lastEventId;
        }
        const response = await this.request(`/api/v1/runs/${runId}/events`, {
          headers,
          signal: options.signal,
        });
        if (!response.ok || !response.body) {
          throw new RunStreamHttpError(
            `Failed opening run event stream for ${runId}: ${response.status}`,
            response.status,
          );
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        const deliver = (frame: string): void => {
          const parsed = parseFrame(frame);
          if (!parsed) {
            return;
          }
          if (parsed.id !== undefined) {
            lastEventId = parsed.id;
            if (deliveredEventIds.has(parsed.id)) {
              return;
            }
            deliveredEventIds.add(parsed.id);
          }
          eventCount += 1;
          onEvent(parsed);
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            break;
          }
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split(/\r?\n\r?\n/);
          buffer = frames.pop() ?? "";
          frames.forEach(deliver);
        }
        buffer += decoder.decode();
        if (buffer.trim()) {
          deliver(buffer);
        }
        return { lastEventId, eventCount };
      } catch (error) {
        if (reconnectAttempts >= this.maxReconnectAttempts || !shouldReconnect(error, options.signal)) {
          throw error;
        }
        reconnectAttempts += 1;
        await this.wait(this.reconnectDelayMs * reconnectAttempts);
      }
    }
  }
}
