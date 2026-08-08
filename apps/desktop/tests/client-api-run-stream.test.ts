import { vi } from "vitest";
import { ClientApiRunStream, type ClientApiSseEvent } from "../electron/main/client-api-run-stream";

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

describe("ClientApiRunStream", () => {
  it("parses chunked CRLF frames, multiline data, ids, and the final unterminated frame", async () => {
    const request = vi.fn(async () =>
      streamResponse([
        "id: event-1\r\nevent: message.delta\r\ndata: {\"part\":\r\n",
        "data: {\"type\":\"markdown\",\"text\":\"hello\"}}\r\n\r\n",
        "id: event-2\nevent: run.finished\ndata: {\"status\":\"completed\"}",
      ]),
    );
    const stream = new ClientApiRunStream({ request });
    const events: ClientApiSseEvent[] = [];

    const result = await stream.consume("run-1", (event) => events.push(event), { lastEventId: "event-0" });

    expect(request).toHaveBeenCalledWith("/api/v1/runs/run-1/events", {
      headers: {
        Accept: "text/event-stream",
        "Last-Event-ID": "event-0",
      },
      signal: undefined,
    });
    expect(events).toEqual([
      {
        id: "event-1",
        event: "message.delta",
        data: { part: { type: "markdown", text: "hello" } },
      },
      {
        id: "event-2",
        event: "run.finished",
        data: { status: "completed" },
      },
    ]);
    expect(result).toEqual({ lastEventId: "event-2", eventCount: 2 });
  });

  it("rejects a non-stream response with the run id", async () => {
    const stream = new ClientApiRunStream({
      request: async () => new Response("unavailable", { status: 503 }),
      maxReconnectAttempts: 0,
    });

    await expect(stream.consume("run-503", () => undefined)).rejects.toThrow(
      "Failed opening run event stream for run-503: 503",
    );
  });

  it("reconnects after a read failure and resumes after the last delivered event id", async () => {
    const encoder = new TextEncoder();
    let pullCount = 0;
    const interrupted = new Response(
      new ReadableStream<Uint8Array>({
        pull(controller) {
          pullCount += 1;
          if (pullCount === 1) {
            controller.enqueue(
              encoder.encode('id: run-1:1\nevent: message.delta\ndata: {"part":{"type":"markdown","text":"one"}}\n\n'),
            );
            return;
          }
          controller.error(new Error("socket reset"));
        },
      }),
      { status: 200, headers: { "Content-Type": "text/event-stream" } },
    );
    const request = vi
      .fn()
      .mockResolvedValueOnce(interrupted)
      .mockResolvedValueOnce(
        streamResponse([
          'id: run-1:2\nevent: run.finished\ndata: {"status":"completed"}\n\n',
        ]),
      );
    const wait = vi.fn(async () => undefined);
    const stream = new ClientApiRunStream({ request, maxReconnectAttempts: 2, reconnectDelayMs: 20, wait });
    const events: ClientApiSseEvent[] = [];

    const result = await stream.consume("run-1", (event) => events.push(event));

    expect(request).toHaveBeenNthCalledWith(2, "/api/v1/runs/run-1/events", {
      headers: { Accept: "text/event-stream", "Last-Event-ID": "run-1:1" },
      signal: undefined,
    });
    expect(wait).toHaveBeenCalledWith(20);
    expect(events.map((event) => event.id)).toEqual(["run-1:1", "run-1:2"]);
    expect(result).toEqual({ lastEventId: "run-1:2", eventCount: 2 });
  });

  it("treats a clean EOF before a terminal event as an interrupted stream and resumes", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        streamResponse([
          'id: run-2:1\nevent: message.delta\ndata: {"part":{"type":"markdown","text":"working"}}\n\n',
        ]),
      )
      .mockResolvedValueOnce(
        streamResponse([
          'id: run-2:2\nevent: run.finished\ndata: {"status":"completed"}\n\n',
        ]),
      );
    const wait = vi.fn(async () => undefined);
    const stream = new ClientApiRunStream({ request, maxReconnectAttempts: 2, reconnectDelayMs: 25, wait });
    const events: ClientApiSseEvent[] = [];

    const result = await stream.consume("run-2", (event) => events.push(event));

    expect(request).toHaveBeenNthCalledWith(2, "/api/v1/runs/run-2/events", {
      headers: { Accept: "text/event-stream", "Last-Event-ID": "run-2:1" },
      signal: undefined,
    });
    expect(wait).toHaveBeenCalledWith(25);
    expect(events.map((event) => event.id)).toEqual(["run-2:1", "run-2:2"]);
    expect(result).toEqual({ lastEventId: "run-2:2", eventCount: 2 });
  });
});
