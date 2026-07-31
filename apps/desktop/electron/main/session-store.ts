import { app } from "electron";
import fs from "node:fs";
import path from "node:path";
import type { ChatMessage, SessionSummary } from "../../app/src/types";

interface PersistedSessionState {
  sessions: SessionSummary[];
  messagesBySession: Record<string, ChatMessage[]>;
}

const DEFAULT_STATE: PersistedSessionState = {
  sessions: [],
  messagesBySession: {},
};

export class SessionStore {
  private readonly storePath: string;

  public constructor() {
    this.storePath = path.join(app.getPath("userData"), "session-store.json");
  }

  public read(): PersistedSessionState {
    try {
      if (!fs.existsSync(this.storePath)) {
        return structuredClone(DEFAULT_STATE);
      }
      const raw = JSON.parse(fs.readFileSync(this.storePath, "utf-8")) as PersistedSessionState;
      return {
        sessions: Array.isArray(raw.sessions) ? raw.sessions : [],
        messagesBySession: raw.messagesBySession && typeof raw.messagesBySession === "object" ? raw.messagesBySession : {},
      };
    } catch {
      return structuredClone(DEFAULT_STATE);
    }
  }

  public write(state: PersistedSessionState): void {
    fs.mkdirSync(path.dirname(this.storePath), { recursive: true });
    fs.writeFileSync(this.storePath, `${JSON.stringify(state, null, 2)}\n`, "utf-8");
  }

  public listSessions(agentId: string): SessionSummary[] {
    return this.read()
      .sessions.filter((session) => session.agentId === agentId)
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  }

  public getMessages(sessionId: string): ChatMessage[] {
    return this.read().messagesBySession[sessionId] ?? [];
  }

  public upsertSession(session: SessionSummary): void {
    const current = this.read();
    current.sessions = [session, ...current.sessions.filter((item) => item.id !== session.id)].sort((left, right) =>
      right.updatedAt.localeCompare(left.updatedAt),
    );
    this.write(current);
  }

  public appendMessage(sessionId: string, message: ChatMessage): void {
    const current = this.read();
    current.messagesBySession[sessionId] = [...(current.messagesBySession[sessionId] ?? []), message];
    this.write(current);
  }

  public replaceMessage(sessionId: string, message: ChatMessage): void {
    const current = this.read();
    const existing = current.messagesBySession[sessionId] ?? [];
    current.messagesBySession[sessionId] = existing.map((item) => (item.id === message.id ? message : item));
    this.write(current);
  }
}
