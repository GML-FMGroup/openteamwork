import type { ChatMessage, MessagePart, RunEvent, SessionSummary } from "@openppx/client";

interface SessionCacheEntry {
  sessions: SessionSummary[];
  expiresAt: number;
}

interface MessageCacheEntry {
  messages: ChatMessage[];
  expiresAt: number;
}

export interface ClientApiSessionCacheOptions {
  ttlMs?: number;
  now?: () => number;
}

function clonePart(part: MessagePart): MessagePart {
  return { ...part };
}

function cloneMessage(message: ChatMessage): ChatMessage {
  return {
    ...message,
    parts: message.parts.map(clonePart),
  };
}

/** TTL cache for Desktop Session/Message projections and run-event updates. */
export class ClientApiSessionCache {
  private readonly ttlMs: number;

  private readonly now: () => number;

  private readonly sessions = new Map<string, SessionCacheEntry>();

  private readonly messages = new Map<string, MessageCacheEntry>();

  public constructor(options: ClientApiSessionCacheOptions = {}) {
    this.ttlMs = options.ttlMs ?? 5_000;
    this.now = options.now ?? Date.now;
  }

  public readSessions(agentId: string): SessionSummary[] | null {
    const cached = this.sessions.get(agentId);
    if (!cached) {
      return null;
    }
    if (cached.expiresAt < this.now()) {
      this.sessions.delete(agentId);
      return null;
    }
    return cached.sessions.map((session) => ({ ...session }));
  }

  public writeSessions(agentId: string, sessions: SessionSummary[]): void {
    this.sessions.set(agentId, {
      sessions: sessions.map((session) => ({ ...session })),
      expiresAt: this.now() + this.ttlMs,
    });
  }

  public readMessages(sessionId: string): ChatMessage[] | null {
    const cached = this.messages.get(sessionId);
    if (!cached) {
      return null;
    }
    if (cached.expiresAt < this.now()) {
      this.messages.delete(sessionId);
      return null;
    }
    return cached.messages.map(cloneMessage);
  }

  public writeMessages(sessionId: string, messages: ChatMessage[]): void {
    this.messages.set(sessionId, {
      messages: messages.map(cloneMessage),
      expiresAt: this.now() + this.ttlMs,
    });
  }

  public invalidate(agentId: string, sessionId?: string): void {
    this.sessions.delete(agentId);
    if (sessionId) {
      this.messages.delete(sessionId);
    }
  }

  public clear(): void {
    this.sessions.clear();
    this.messages.clear();
  }

  public getEntryCounts(): { sessions: number; messages: number } {
    this.evictExpiredEntries();
    return {
      sessions: this.sessions.size,
      messages: this.messages.size,
    };
  }

  public applyEvent(event: RunEvent): void {
    if (event.type === "message.created") {
      const cached = this.readMessages(event.sessionId) ?? [];
      if (!cached.some((message) => message.id === event.message.id)) {
        this.writeMessages(event.sessionId, [...cached, event.message]);
      }
      return;
    }
    if (event.type === "message.updated") {
      const cached = this.readMessages(event.sessionId);
      if (!cached) {
        return;
      }
      const next = cached.map((message) =>
        message.id === event.messageId
          ? {
              ...message,
              status: event.status ?? message.status,
              parts: event.replaceParts ?? [...message.parts, ...(event.appendParts ?? [])],
            }
          : message,
      );
      this.writeMessages(event.sessionId, next);
      return;
    }
    if (event.type === "session.updated") {
      const cached = this.readSessions(event.session.agentId) ?? [];
      const next = [event.session, ...cached.filter((session) => session.id !== event.session.id)].sort((left, right) =>
        right.updatedAt.localeCompare(left.updatedAt),
      );
      this.writeSessions(event.session.agentId, next);
    }
  }

  private evictExpiredEntries(): void {
    const currentTime = this.now();
    for (const [key, entry] of this.sessions) {
      if (entry.expiresAt < currentTime) {
        this.sessions.delete(key);
      }
    }
    for (const [key, entry] of this.messages) {
      if (entry.expiresAt < currentTime) {
        this.messages.delete(key);
      }
    }
  }
}
