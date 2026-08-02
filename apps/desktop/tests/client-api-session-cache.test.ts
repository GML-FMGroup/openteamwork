import type { ChatMessage, RunEvent, SessionSummary } from "@openppx/client";
import { ClientApiSessionCache } from "../electron/main/client-api-session-cache";

function session(id: string, updatedAt: string): SessionSummary {
  return {
    id,
    agentId: "agent-1",
    title: id,
    updatedAt,
    lastMessagePreview: id,
  };
}

function message(id: string): ChatMessage {
  return {
    id,
    sessionId: "session-1",
    role: "assistant",
    status: "streaming",
    createdAt: "2026-08-02T10:00:00Z",
    parts: [{ type: "markdown", text: id }],
  };
}

describe("ClientApiSessionCache", () => {
  it("returns defensive copies and expires entries by TTL", () => {
    let currentTime = 1_000;
    const cache = new ClientApiSessionCache({ ttlMs: 500, now: () => currentTime });
    cache.writeSessions("agent-1", [session("session-1", "2026-08-02T10:00:00Z")]);
    cache.writeMessages("session-1", [message("message-1")]);

    const cachedSessions = cache.readSessions("agent-1")!;
    const cachedMessages = cache.readMessages("session-1")!;
    cachedSessions[0].title = "mutated";
    cachedMessages[0].parts[0] = { type: "markdown", text: "mutated" };

    expect(cache.readSessions("agent-1")?.[0].title).toBe("session-1");
    expect(cache.readMessages("session-1")?.[0].parts[0]).toEqual({ type: "markdown", text: "message-1" });
    expect(cache.getEntryCounts()).toEqual({ sessions: 1, messages: 1 });

    currentTime += 501;
    expect(cache.readSessions("agent-1")).toBeNull();
    expect(cache.readMessages("session-1")).toBeNull();
    expect(cache.getEntryCounts()).toEqual({ sessions: 0, messages: 0 });
  });

  it("applies message and session run events", () => {
    const cache = new ClientApiSessionCache();
    cache.writeMessages("session-1", [message("message-1")]);
    cache.writeSessions("agent-1", [session("old", "2026-08-02T09:00:00Z")]);

    const created: RunEvent = {
      type: "message.created",
      runId: "run-1",
      sessionId: "session-1",
      message: message("message-2"),
    };
    cache.applyEvent(created);
    cache.applyEvent({
      type: "message.updated",
      runId: "run-1",
      sessionId: "session-1",
      messageId: "message-2",
      status: "completed",
      replaceParts: [{ type: "markdown", text: "done" }],
    });
    cache.applyEvent({
      type: "session.updated",
      runId: "run-1",
      session: session("new", "2026-08-02T11:00:00Z"),
    });

    expect(cache.readMessages("session-1")).toHaveLength(2);
    expect(cache.readMessages("session-1")?.[1]).toMatchObject({
      id: "message-2",
      status: "completed",
      parts: [{ type: "markdown", text: "done" }],
    });
    expect(cache.readSessions("agent-1")?.map((item) => item.id)).toEqual(["new", "old"]);
  });

  it("invalidates scoped entries and clears all state", () => {
    const cache = new ClientApiSessionCache();
    cache.writeSessions("agent-1", [session("session-1", "2026-08-02T10:00:00Z")]);
    cache.writeMessages("session-1", [message("message-1")]);

    cache.invalidate("agent-1", "session-1");
    expect(cache.getEntryCounts()).toEqual({ sessions: 0, messages: 0 });

    cache.writeSessions("agent-1", [session("session-1", "2026-08-02T10:00:00Z")]);
    cache.writeMessages("session-1", [message("message-1")]);
    cache.clear();
    expect(cache.getEntryCounts()).toEqual({ sessions: 0, messages: 0 });
  });
});
