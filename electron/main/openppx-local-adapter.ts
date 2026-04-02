import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  bootstrap as mockBootstrap,
  createSession as mockCreateSession,
  loadSession as mockLoadSession,
  runRuntimeCommand as mockRunRuntimeCommand,
  sendMessage as mockSendMessage,
  subscribe as subscribeMock,
} from "../../app/src/lib/mock-client";
import type {
  AgentProfile,
  BootstrapPayload,
  ChatMessage,
  MessagePart,
  PpxClientApi,
  RunEvent,
  RuntimeCommand,
  RuntimeStatus,
  SendMessageInput,
  SessionSummary,
} from "../../app/src/types";

type EventSink = (event: RunEvent) => void;

interface GlobalAgentConfigEntry {
  name?: string;
  id?: string;
  enabled?: boolean;
}

interface GlobalAgentConfig {
  agents?: GlobalAgentConfigEntry[] | { list?: GlobalAgentConfigEntry[] };
}

function now(): string {
  return new Date().toISOString();
}

function normalizeAgentName(input: string): string {
  return input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function readJsonFile<T>(filePath: string): T | null {
  try {
    if (!fs.existsSync(filePath)) {
      return null;
    }
    return JSON.parse(fs.readFileSync(filePath, "utf-8")) as T;
  } catch {
    return null;
  }
}

function detectOpenPpxRoot(): string {
  if (process.env.OPENPPX_ROOT?.trim()) {
    return path.resolve(process.env.OPENPPX_ROOT);
  }
  return path.resolve(process.cwd(), "../openppx_root");
}

function globalConfigPath(): string {
  return path.join(os.homedir(), ".openpipixia", "global_config.json");
}

function agentConfigPath(agentId: string): string {
  return path.join(os.homedir(), ".openpipixia", agentId, "config.json");
}

function resolvePythonBin(openppxRoot: string): string {
  const venvPython = path.join(openppxRoot, ".venv", "bin", "python");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return "python3";
}

export class OpenPpxLocalAdapter implements PpxClientApi {
  private readonly listeners = new Set<EventSink>();

  private readonly openppxRoot = detectOpenPpxRoot();

  private readonly bridgeScriptPath = path.resolve(process.cwd(), "scripts/openppx_bridge.py");

  private readonly pythonBin = resolvePythonBin(this.openppxRoot);

  private readonly mockUnsubscribe = subscribeMock((event) => {
    if (this.shouldUseMock()) {
      this.emit(event);
    }
  });

  private emit(event: RunEvent): void {
    this.listeners.forEach((listener) => listener(event));
  }

  private async callBridge(args: string[]): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const child = spawn(this.pythonBin, [this.bridgeScriptPath, ...args], {
        cwd: this.openppxRoot,
        stdio: ["ignore", "pipe", "pipe"],
      });

      let stdout = "";
      let stderr = "";

      child.stdout.on("data", (chunk: Buffer | string) => {
        stdout += chunk.toString();
      });
      child.stderr.on("data", (chunk: Buffer | string) => {
        stderr += chunk.toString();
      });
      child.on("close", (code) => {
        if (code !== 0) {
          reject(new Error(stderr.trim() || stdout.trim() || `Bridge exited with code ${code}`));
          return;
        }
        const lines = stdout
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);
        if (!lines.length) {
          resolve(null);
          return;
        }
        try {
          resolve(JSON.parse(lines.at(-1)!));
        } catch (error) {
          reject(error);
        }
      });
    });
  }

  private bridgeArgs(action: string, agentId: string, extra: string[] = []): string[] {
    return ["--openppx-root", this.openppxRoot, action, "--agent", agentId, ...extra];
  }

  private formatSessionSummary(agentId: string, payload: Record<string, unknown>): SessionSummary {
    const updatedAt = typeof payload.last_update_time === "number" ? new Date(payload.last_update_time * 1000).toISOString() : now();
    return {
      id: String(payload.id ?? ""),
      agentId,
      title: `Session ${String(payload.id ?? "").slice(0, 8)}`,
      updatedAt,
      lastMessagePreview: typeof payload.last_preview === "string" ? payload.last_preview : "Openppx session",
    };
  }

  private buildMessagePartsFromEvent(event: Record<string, unknown>): MessagePart[] {
    const content = event.content as Record<string, unknown> | undefined;
    const parts = Array.isArray(content?.parts) ? (content?.parts as Array<Record<string, unknown>>) : [];
    const messageParts: MessagePart[] = [];
    for (const part of parts) {
      if (typeof part.text === "string" && part.text.trim()) {
        messageParts.push({ type: "markdown", text: part.text });
      }
      const functionCall = part.function_call as Record<string, unknown> | undefined;
      if (functionCall) {
        messageParts.push({
          type: "step_ref",
          stepId: String(functionCall.id ?? crypto.randomUUID()),
          title: String(functionCall.name ?? "Tool call"),
          status: "completed",
          detail: typeof functionCall.args === "string" ? functionCall.args : JSON.stringify(functionCall.args ?? {}, null, 2),
        });
      }
      const functionResponse = part.function_response as Record<string, unknown> | undefined;
      if (functionResponse) {
        messageParts.push({
          type: "code",
          language: "json",
          text: JSON.stringify(functionResponse.response ?? {}, null, 2),
        });
      }
    }
    if (!messageParts.length) {
      messageParts.push({
        type: "markdown",
        text: "(event without renderable text)",
      });
    }
    return messageParts;
  }

  private buildMessagesFromSession(sessionId: string, payload: Record<string, unknown>): ChatMessage[] {
    const events = Array.isArray(payload.events) ? (payload.events as Array<Record<string, unknown>>) : [];
    return events.map((event) => {
      const author = String(event.author ?? "");
      const timestamp = typeof event.timestamp === "number" ? new Date(event.timestamp * 1000).toISOString() : now();
      return {
        id: String(event.id ?? crypto.randomUUID()),
        sessionId,
        role: author === "user" ? "user" : "assistant",
        status: "completed",
        createdAt: timestamp,
        parts: this.buildMessagePartsFromEvent(event),
      };
    });
  }

  private shouldUseMock(): boolean {
    return !fs.existsSync(this.openppxRoot) || !fs.existsSync(this.bridgeScriptPath) || this.listRealAgents().length === 0;
  }

  private listRealAgents(): AgentProfile[] {
    const raw = readJsonFile<GlobalAgentConfig>(globalConfigPath());
    if (!raw) {
      return [];
    }

    const entries = Array.isArray(raw.agents)
      ? raw.agents
      : Array.isArray(raw.agents?.list)
        ? raw.agents.list
        : [];

    const agents: Array<AgentProfile | null> = entries.map((entry) => {
        const name = normalizeAgentName(String(entry.name ?? entry.id ?? ""));
        if (!name || entry.enabled === false) {
          return null;
        }
        const config = readJsonFile<{ agent?: { workspace?: string } }>(agentConfigPath(name));
        const workspace = config?.agent?.workspace?.trim() ?? "";
        return {
          id: name,
          name,
          description: workspace ? `Workspace: ${workspace}` : "Local openppx agent",
          enabled: true,
          status: "healthy",
          tags: ["local", "openppx"],
        };
      });
    return agents.filter((item): item is AgentProfile => item !== null);
  }

  private getRealRuntimeStatus(): RuntimeStatus {
    if (!fs.existsSync(this.openppxRoot)) {
      return {
        target: { id: "local-default", type: "local", name: "This Mac" },
        state: "error",
        summary: "openppx root was not found.",
        detail: "Set OPENPPX_ROOT or keep ppx-client beside openppx_root.",
        lastError: "OPENPPX_ROOT_NOT_FOUND",
      };
    }
    if (!fs.existsSync(globalConfigPath())) {
      return {
        target: { id: "local-default", type: "local", name: "This Mac" },
        state: "starting",
        summary: "openppx config is not initialized yet.",
        detail: "Run the openppx setup first so ~/.openpipixia/global_config.json exists.",
      };
    }
    return {
      target: { id: "local-default", type: "local", name: "This Mac" },
      state: "healthy",
      summary: "Direct local openppx bridge is ready.",
      detail: "This version calls a local Python bridge directly, without requiring the gateway HTTP service.",
    };
  }

  public async bootstrap(): Promise<BootstrapPayload> {
    if (this.shouldUseMock()) {
      return mockBootstrap();
    }

    const agents = this.listRealAgents();
    const selectedAgentId = agents[0]?.id ?? "";
    const sessions = selectedAgentId ? await this.listSessionsForAgent(selectedAgentId) : [];
    const selectedSessionId = sessions[0]?.id ?? "";
    const messages = selectedSessionId ? (await this.loadSession(selectedSessionId)).messages : [];
    return {
      runtime: this.getRealRuntimeStatus(),
      agents,
      sessions,
      messages,
      selectedAgentId,
      selectedSessionId,
    };
  }

  public async runRuntimeCommand(command: RuntimeCommand): Promise<RuntimeStatus> {
    if (this.shouldUseMock()) {
      return mockRunRuntimeCommand(command);
    }
    if (command === "stop") {
      return {
        ...this.getRealRuntimeStatus(),
        summary: "Direct local bridge does not keep a background runtime to stop.",
        detail: "This version talks to openppx on demand per request.",
      };
    }
    return this.getRealRuntimeStatus();
  }

  public async listSessions(agentId: string): Promise<{ sessions: SessionSummary[] }> {
    if (this.shouldUseMock()) {
      const payload = await mockBootstrap();
      return {
        sessions: payload.selectedAgentId === agentId ? payload.sessions : [],
      };
    }
    return {
      sessions: await this.listSessionsForAgent(agentId),
    };
  }

  public async createSession(agentId: string): Promise<{ session: SessionSummary }> {
    if (this.shouldUseMock()) {
      return mockCreateSession(agentId);
    }
    const response = (await this.callBridge(
      this.bridgeArgs("create_session", agentId, ["--session-id", `${agentId}-${crypto.randomUUID()}`]),
    )) as { session?: Record<string, unknown> } | null;
    const session = this.formatSessionSummary(agentId, response?.session ?? {});
    return { session };
  }

  public async loadSession(sessionId: string): Promise<{ messages: ChatMessage[] }> {
    if (this.shouldUseMock()) {
      return mockLoadSession(sessionId);
    }
    const sessions = await Promise.all(this.listRealAgents().map((agent) => this.listSessionsForAgent(agent.id)));
    const flat = sessions.flat();
    const session = flat.find((item) => item.id === sessionId);
    if (!session) {
      return { messages: [] };
    }
    const response = (await this.callBridge(
      this.bridgeArgs("get_session", session.agentId, ["--session-id", sessionId]),
    )) as { session?: Record<string, unknown> | null } | null;
    if (!response?.session) {
      return { messages: [] };
    }
    return {
      messages: this.buildMessagesFromSession(sessionId, response.session),
    };
  }

  public async sendMessage(input: SendMessageInput): Promise<{ runId: string }> {
    if (this.shouldUseMock()) {
      return mockSendMessage(input);
    }

    const runId = `run-${crypto.randomUUID()}`;
    const userMessage: ChatMessage = {
      id: `user-${crypto.randomUUID()}`,
      sessionId: input.sessionId,
      role: "user",
      status: "completed",
      createdAt: now(),
      parts: [{ type: "markdown", text: input.text }],
    };
    const assistantMessage: ChatMessage = {
      id: `assistant-${crypto.randomUUID()}`,
      sessionId: input.sessionId,
      role: "assistant",
      status: "streaming",
      createdAt: now(),
      parts: [
        {
          type: "step_ref",
          stepId: `step-${crypto.randomUUID()}`,
          title: "Connecting to openppx",
          status: "running",
          detail: "Launching the local Python bridge for this agent session.",
        },
      ],
    };
    this.emit({
      type: "message.created",
      runId,
      message: assistantMessage,
    });

    const sessions = await this.listSessionsForAgent(input.agentId);
    const session = sessions.find((item) => item.id === input.sessionId) ?? {
      id: input.sessionId,
      agentId: input.agentId,
      title: "Local session",
      updatedAt: now(),
      lastMessagePreview: input.text,
    };

    await new Promise<void>((resolve) => {
      const child = spawn(
        this.pythonBin,
        this.bridgeArgs("run", input.agentId, ["--session-id", input.sessionId, "--message", input.text]),
        {
          cwd: this.openppxRoot,
          stdio: ["ignore", "pipe", "pipe"],
        },
      );

      let stdoutBuffer = "";
      let finalText = "";
      let stderrText = "";

        const applyAssistantParts = (parts: MessagePart[], status: ChatMessage["status"]): void => {
          const updated: ChatMessage = {
            ...assistantMessage,
            status,
            parts,
          };
          assistantMessage.status = status;
          assistantMessage.parts = parts;
          this.emit({
            type: "message.updated",
            runId,
          messageId: assistantMessage.id,
          replaceParts: parts,
          status,
        });
      };

      const handleLine = (line: string): void => {
        if (!line.trim()) {
          return;
        }
        try {
          const payload = JSON.parse(line) as { type: string; text?: string; message?: string };
          if (payload.type === "delta") {
            finalText = payload.text ?? finalText;
            applyAssistantParts([{ type: "markdown", text: finalText }], "streaming");
            return;
          }
          if (payload.type === "final") {
            finalText = payload.text ?? finalText;
            applyAssistantParts([{ type: "markdown", text: finalText }], "completed");
            return;
          }
          if (payload.type === "error") {
            applyAssistantParts(
              [{ type: "error", text: payload.message ?? "Unknown bridge error", errorCode: "OPENPPX_BRIDGE_ERROR" }],
              "failed",
            );
          }
        } catch {
          finalText = line.trim();
          applyAssistantParts([{ type: "markdown", text: finalText }], "streaming");
        }
      };

      child.stdout.on("data", (chunk: Buffer | string) => {
        stdoutBuffer += chunk.toString();
        const lines = stdoutBuffer.split("\n");
        stdoutBuffer = lines.pop() ?? "";
        lines.forEach(handleLine);
      });

      child.stderr.on("data", (chunk: Buffer | string) => {
        stderrText += chunk.toString();
      });

      child.on("close", (code) => {
        if (stdoutBuffer.trim()) {
          handleLine(stdoutBuffer.trim());
        }
        if (code !== 0 && assistantMessage.status !== "failed") {
          applyAssistantParts(
            [
              {
                type: "error",
                text: stderrText.trim() || `Bridge exited with code ${code}`,
                errorCode: "OPENPPX_BRIDGE_EXIT",
              },
            ],
            "failed",
          );
        }

        session.updatedAt = now();
        session.lastMessagePreview = finalText || input.text;
        this.emit({
          type: "session.updated",
          runId,
          session,
        });
        this.emit({
          type: "run.finished",
          runId,
        });
        resolve();
      });
    });

    return { runId };
  }

  public onRunEvent(listener: (event: RunEvent) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  public dispose(): void {
    this.mockUnsubscribe();
  }

  private async listSessionsForAgent(agentId: string): Promise<SessionSummary[]> {
    const response = (await this.callBridge(this.bridgeArgs("list_sessions", agentId))) as
      | { sessions?: Array<Record<string, unknown>> }
      | null;
    const sessions = Array.isArray(response?.sessions) ? response.sessions : [];
    return sessions
      .map((payload) => this.formatSessionSummary(agentId, payload))
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  }
}
