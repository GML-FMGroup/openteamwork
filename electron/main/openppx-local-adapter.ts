import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import {
  bootstrap as mockBootstrap,
  createSession as mockCreateSession,
  loadSession as mockLoadSession,
  runRuntimeCommand as mockRunRuntimeCommand,
  sendMessage as mockSendMessage,
  subscribe as subscribeMock,
} from "../../app/src/lib/mock-client";
import {
  normalizeClientApiMessage,
  normalizeClientApiPart,
  normalizeClientApiRuntime,
  normalizeClientApiSession,
} from "../../app/src/lib/client-api-projection";
import {
  buildMessagePartsFromSessionEvent,
  mergeAssistantParts,
  projectBridgeEventToStepParts,
  sessionEventRole,
} from "../../app/src/lib/openppx-projection";
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
type StepPart = Extract<MessagePart, { type: "step_ref" }>;

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

function clientDebugEnabled(): boolean {
  const raw = process.env.OPENPPX_CLIENT_DEBUG ?? process.env.PPX_CLIENT_DEBUG ?? "";
  return ["1", "true", "yes", "on"].includes(raw.trim().toLowerCase());
}

function clientDebugLog(tag: string, payload: unknown): void {
  if (!clientDebugEnabled()) {
    return;
  }
  console.log(`[ppx-client][debug] ${tag}`, payload);
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

function normalizeAgentProfile(payload: Record<string, unknown>): AgentProfile {
  return {
    id: String(payload.id ?? ""),
    name: String(payload.name ?? payload.id ?? ""),
    description: String(payload.description ?? "Local openppx agent"),
    enabled: payload.enabled !== false,
    status: (String(payload.status ?? "healthy") as AgentProfile["status"]) || "healthy",
    tags: Array.isArray(payload.tags) ? payload.tags.map((tag) => String(tag)) : [],
  };
}

export class OpenPpxLocalAdapter implements PpxClientApi {
  private readonly listeners = new Set<EventSink>();

  private readonly openppxRoot = detectOpenPpxRoot();

  private readonly bridgeScriptPath = path.resolve(process.cwd(), "scripts/openppx_bridge.py");

  private readonly pythonBin = resolvePythonBin(this.openppxRoot);

  private readonly clientApiHost = process.env.OPENPPX_CLIENT_API_HOST?.trim() || "127.0.0.1";

  private readonly clientApiPort = Number(process.env.OPENPPX_CLIENT_API_PORT?.trim() || "8765");

  private readonly clientApiBaseUrl = `http://${this.clientApiHost}:${this.clientApiPort}`;

  private clientApiProcess: ReturnType<typeof spawn> | null = null;

  private readonly mockUnsubscribe = subscribeMock((event) => {
    if (this.shouldUseMock()) {
      this.emit(event);
    }
  });

  private emit(event: RunEvent): void {
    this.listeners.forEach((listener) => listener(event));
  }

  private async fetchClientApiJson(pathname: string, init?: RequestInit): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.clientApiBaseUrl}${pathname}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
    const payload = (await response.json()) as Record<string, unknown>;
    if (!response.ok || payload.ok === false) {
      const error = (payload.error as Record<string, unknown> | undefined) ?? {};
      throw new Error(String(error.message ?? `Client API request failed: ${response.status}`));
    }
    return payload;
  }

  private async isClientApiHealthy(): Promise<boolean> {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 500);
      const response = await fetch(`${this.clientApiBaseUrl}/api/v1/health`, {
        signal: controller.signal,
      });
      clearTimeout(timeout);
      return response.ok;
    } catch {
      return false;
    }
  }

  private async ensureClientApiAvailable(): Promise<boolean> {
    if (await this.isClientApiHealthy()) {
      clientDebugLog("client-api.health", {
        baseUrl: this.clientApiBaseUrl,
        status: "healthy",
      });
      return true;
    }
    if (!fs.existsSync(this.openppxRoot)) {
      clientDebugLog("client-api.health", {
        baseUrl: this.clientApiBaseUrl,
        status: "openppx-root-missing",
        openppxRoot: this.openppxRoot,
      });
      return false;
    }
    if (!this.clientApiProcess) {
      clientDebugLog("client-api.spawn", {
        baseUrl: this.clientApiBaseUrl,
        pythonBin: this.pythonBin,
        openppxRoot: this.openppxRoot,
      });
      this.clientApiProcess = spawn(
        this.pythonBin,
        ["-m", "openpipixia.app.cli", "client-api", "serve", "--host", this.clientApiHost, "--port", String(this.clientApiPort)],
        {
          cwd: this.openppxRoot,
          stdio: ["ignore", "pipe", "pipe"],
        },
      );
      this.clientApiProcess.stdout?.on("data", (chunk: Buffer | string) => {
        clientDebugLog("client-api.stdout", chunk.toString().trim());
      });
      this.clientApiProcess.stderr?.on("data", (chunk: Buffer | string) => {
        clientDebugLog("client-api.stderr", chunk.toString().trim());
      });
      this.clientApiProcess.on("close", () => {
        clientDebugLog("client-api.close", { baseUrl: this.clientApiBaseUrl });
        this.clientApiProcess = null;
      });
    }
    for (let attempt = 0; attempt < 12; attempt += 1) {
      await delay(250);
      if (await this.isClientApiHealthy()) {
        clientDebugLog("client-api.health", {
          baseUrl: this.clientApiBaseUrl,
          status: "healthy-after-spawn",
          attempt: attempt + 1,
        });
        return true;
      }
    }
    clientDebugLog("client-api.health", {
      baseUrl: this.clientApiBaseUrl,
      status: "unreachable",
    });
    return false;
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
    const updatedAt =
      typeof payload.last_update_time === "number" ? new Date(payload.last_update_time * 1000).toISOString() : now();
    return {
      id: String(payload.id ?? ""),
      agentId,
      title: `Session ${String(payload.id ?? "").slice(0, 8)}`,
      updatedAt,
      lastMessagePreview: typeof payload.last_preview === "string" ? payload.last_preview : "Openppx session",
    };
  }

  private buildMessagesFromSession(sessionId: string, payload: Record<string, unknown>): ChatMessage[] {
    const events = Array.isArray(payload.events) ? (payload.events as Array<Record<string, unknown>>) : [];
    return events.map((event) => {
      const author = String(event.author ?? "");
      const timestamp = typeof event.timestamp === "number" ? new Date(event.timestamp * 1000).toISOString() : now();
      return {
        id: String(event.id ?? crypto.randomUUID()),
        sessionId,
        role: sessionEventRole(author),
        status: "completed",
        createdAt: timestamp,
        parts: buildMessagePartsFromSessionEvent(event),
      };
    });
  }

  private shouldUseMock(): boolean {
    return !fs.existsSync(this.openppxRoot) || this.listRealAgents().length === 0;
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

  private getFallbackRuntimeStatus(): RuntimeStatus {
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
      summary: "Local openppx runtime is available.",
      detail: "The client will prefer the local client-api gateway and fall back to the legacy bridge when needed.",
    };
  }

  private async fetchRuntimeStatus(): Promise<RuntimeStatus> {
    if (await this.ensureClientApiAvailable()) {
      const payload = await this.fetchClientApiJson("/api/v1/runtime/status");
      const runtime = normalizeClientApiRuntime((payload.data as Record<string, unknown> | undefined) ?? {});
      if (runtime) {
        return runtime;
      }
    }
    return this.getFallbackRuntimeStatus();
  }

  public async bootstrap(): Promise<BootstrapPayload> {
    if (this.shouldUseMock()) {
      return mockBootstrap();
    }

    const runtime = await this.fetchRuntimeStatus();
    let agents = this.listRealAgents();
    if (await this.isClientApiHealthy()) {
      try {
        const payload = await this.fetchClientApiJson("/api/v1/agents");
        const items = Array.isArray((payload.data as Record<string, unknown> | undefined)?.items)
          ? ((payload.data as Record<string, unknown>).items as Array<Record<string, unknown>>)
          : [];
        agents = items.map((item) => normalizeAgentProfile(item));
      } catch {
        // Keep local fallback data when the client-api agent query fails.
      }
    }

    const selectedAgentId = agents[0]?.id ?? "";
    const sessions = selectedAgentId ? (await this.listSessions(selectedAgentId)).sessions : [];
    const selectedSessionId = sessions[0]?.id ?? "";
    const messages = selectedSessionId ? (await this.loadSession(selectedSessionId)).messages : [];
    return {
      runtime,
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
      if (this.clientApiProcess && this.clientApiProcess.exitCode === null) {
        this.clientApiProcess.kill();
      }
      this.clientApiProcess = null;
      return {
        ...this.getFallbackRuntimeStatus(),
        state: "stopped",
        summary: "Local client-api process was stopped.",
        detail: "The next request can start it again on demand.",
      };
    }
    if (command === "start" || command === "restart") {
      await this.ensureClientApiAvailable();
    }
    return this.fetchRuntimeStatus();
  }

  public async listSessions(agentId: string): Promise<{ sessions: SessionSummary[] }> {
    if (this.shouldUseMock()) {
      const payload = await mockBootstrap();
      return {
        sessions: payload.selectedAgentId === agentId ? payload.sessions : [],
      };
    }
    if (await this.ensureClientApiAvailable()) {
      try {
        const payload = await this.fetchClientApiJson(`/api/v1/agents/${agentId}/sessions`);
        const items = Array.isArray((payload.data as Record<string, unknown> | undefined)?.items)
          ? ((payload.data as Record<string, unknown>).items as unknown[])
          : [];
        return {
          sessions: items
            .map((item) => normalizeClientApiSession(item))
            .filter((item): item is SessionSummary => item !== null),
        };
      } catch {
        // Fall back to the legacy bridge path below.
      }
    }
    return {
      sessions: await this.listSessionsForAgent(agentId),
    };
  }

  public async createSession(agentId: string): Promise<{ session: SessionSummary }> {
    if (this.shouldUseMock()) {
      return mockCreateSession(agentId);
    }
    if (await this.ensureClientApiAvailable()) {
      try {
        const payload = await this.fetchClientApiJson(`/api/v1/agents/${agentId}/sessions`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        const session = normalizeClientApiSession((payload.data as Record<string, unknown> | undefined)?.session);
        if (session) {
          return { session };
        }
      } catch {
        // Fall through to the legacy bridge path.
      }
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
    if (await this.ensureClientApiAvailable()) {
      try {
        const payload = await this.fetchClientApiJson(`/api/v1/sessions/${sessionId}/messages`);
        const items = Array.isArray((payload.data as Record<string, unknown> | undefined)?.items)
          ? ((payload.data as Record<string, unknown>).items as unknown[])
          : [];
        return {
          messages: items
            .map((item) => normalizeClientApiMessage(item))
            .filter((item): item is ChatMessage => item !== null),
        };
      } catch {
        // Fall through to the legacy bridge path.
      }
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
    clientDebugLog("send.start", {
      agentId: input.agentId,
      sessionId: input.sessionId,
      textPreview: input.text.slice(0, 240),
      mode: this.shouldUseMock() ? "mock" : "local",
    });
    if (this.shouldUseMock()) {
      return mockSendMessage(input);
    }
    if (await this.ensureClientApiAvailable()) {
      try {
        return await this.sendMessageViaClientApi(input);
      } catch (error) {
        clientDebugLog("send.client-api.failed", {
          agentId: input.agentId,
          sessionId: input.sessionId,
          error: error instanceof Error ? error.message : String(error),
        });
        // Fall back to the bridge path if the service flow fails.
      }
    }
    clientDebugLog("send.bridge.fallback", {
      agentId: input.agentId,
      sessionId: input.sessionId,
    });
    return this.sendMessageViaBridge(input);
  }

  private async sendMessageViaClientApi(input: SendMessageInput): Promise<{ runId: string }> {
    const payload = await this.fetchClientApiJson(`/api/v1/agents/${input.agentId}/sessions/${input.sessionId}/runs`, {
      method: "POST",
      body: JSON.stringify({ text: input.text }),
    });
    const run = ((payload.data as Record<string, unknown> | undefined)?.run ?? {}) as Record<string, unknown>;
    const runId = String(run.id ?? `run-${crypto.randomUUID()}`);
    clientDebugLog("send.client-api.run-created", {
      runId,
      agentId: input.agentId,
      sessionId: input.sessionId,
    });
    const sessionPayload = await this.listSessions(input.agentId);
    const session = sessionPayload.sessions.find((item) => item.id === input.sessionId) ?? {
      id: input.sessionId,
      agentId: input.agentId,
      title: "Local session",
      updatedAt: now(),
      lastMessagePreview: input.text,
    };

    const response = await fetch(`${this.clientApiBaseUrl}/api/v1/runs/${runId}/events`);
    if (!response.ok || !response.body) {
      throw new Error(`Failed opening run event stream for ${runId}`);
    }
    clientDebugLog("send.client-api.stream-open", {
      runId,
      url: `${this.clientApiBaseUrl}/api/v1/runs/${runId}/events`,
    });

    await new Promise<void>(async (resolve, reject) => {
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let assistantMessage: ChatMessage | null = null;
      let finalText = "";
      let stepParts: StepPart[] = [];

      const syncAssistant = (status: ChatMessage["status"]): void => {
        if (!assistantMessage) {
          return;
        }
        assistantMessage.status = status;
        assistantMessage.parts = mergeAssistantParts(stepParts, finalText);
          this.emit({
            type: "message.updated",
            runId,
            sessionId: assistantMessage.sessionId,
            messageId: assistantMessage.id,
            replaceParts: assistantMessage.parts,
            status,
          });
      };

      const handleClientApiEvent = (eventName: string, data: Record<string, unknown>): void => {
        clientDebugLog("send.client-api.event", {
          runId,
          eventName,
          keys: Object.keys(data),
        });
        if (eventName === "message.created") {
          const message = normalizeClientApiMessage(data.message);
          if (!message) {
            return;
          }
          assistantMessage = message;
          stepParts = message.parts.filter((part): part is StepPart => part.type === "step_ref");
          this.emit({ type: "message.created", runId, sessionId: message.sessionId, message });
          return;
        }
        if (eventName === "step.updated" && assistantMessage) {
          const rawStep = (data.step as Record<string, unknown> | undefined) ?? {};
          const status = String(rawStep.status ?? "running");
          stepParts = projectBridgeEventToStepParts(
            {
              content: {
                parts: [
                  status === "running"
                    ? {
                        function_call: {
                          id: rawStep.step_id,
                          name: rawStep.title,
                          args: rawStep.detail,
                        },
                      }
                    : {
                        function_response: {
                          id: rawStep.step_id,
                          name: rawStep.title,
                          response: rawStep.detail,
                        },
                      },
                ],
              },
            },
            stepParts,
          );
          syncAssistant("streaming");
          return;
        }
        if (eventName === "message.delta" && assistantMessage) {
          const part = normalizeClientApiPart(data.part);
          if (part?.type === "markdown") {
            finalText = part.text;
            syncAssistant("streaming");
          }
          return;
        }
        if (eventName === "message.completed" && assistantMessage) {
          const message = normalizeClientApiMessage(data.message);
          finalText = message?.parts.find((part) => part.type === "markdown")?.text ?? finalText;
          stepParts = stepParts.map((part) => (part.status === "running" ? { ...part, status: "completed" } : part));
          syncAssistant("completed");
          return;
        }
        if (eventName === "message.failed" && assistantMessage) {
          const errorPart = normalizeClientApiPart(data.error);
          if (errorPart?.type === "error") {
            this.emit({
              type: "message.updated",
              runId,
              sessionId: assistantMessage.sessionId,
              messageId: assistantMessage.id,
              replaceParts: [errorPart],
              status: "failed",
            });
          }
          return;
        }
        if (eventName === "run.finished") {
          session.updatedAt = now();
          session.lastMessagePreview = finalText || input.text;
          this.emit({ type: "session.updated", runId, session });
          this.emit({ type: "run.finished", runId, sessionId: input.sessionId });
          clientDebugLog("send.client-api.finished", {
            runId,
            status: "completed",
            finalTextLength: finalText.length,
          });
        }
      };

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            clientDebugLog("send.client-api.stream-closed", { runId });
            break;
          }
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const lines = frame.split("\n");
            let eventName = "message";
            let dataLine = "";
            for (const line of lines) {
              if (line.startsWith("event:")) {
                eventName = line.slice(6).trim();
              } else if (line.startsWith("data:")) {
                dataLine += line.slice(5).trim();
              }
            }
            if (!dataLine) {
              continue;
            }
            handleClientApiEvent(eventName, JSON.parse(dataLine) as Record<string, unknown>);
          }
        }
        resolve();
      } catch (error) {
        clientDebugLog("send.client-api.stream-error", {
          runId,
          error: error instanceof Error ? error.message : String(error),
        });
        reject(error);
      }
    });

    return { runId };
  }

  private async sendMessageViaBridge(input: SendMessageInput): Promise<{ runId: string }> {
    const runId = `run-${crypto.randomUUID()}`;
    clientDebugLog("send.bridge.start", {
      runId,
      agentId: input.agentId,
      sessionId: input.sessionId,
    });
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
      sessionId: input.sessionId,
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
      let stepParts: StepPart[] = assistantMessage.parts.filter((part): part is StepPart => part.type === "step_ref");
      let hasStructuredEvent = false;

      const applyAssistantParts = (parts: MessagePart[], status: ChatMessage["status"]): void => {
        assistantMessage.status = status;
        assistantMessage.parts = parts;
        this.emit({
          type: "message.updated",
          runId,
          sessionId: assistantMessage.sessionId,
          messageId: assistantMessage.id,
          replaceParts: parts,
          status,
        });
      };

      const syncAssistant = (status: ChatMessage["status"]): void => {
        applyAssistantParts(mergeAssistantParts(stepParts, finalText), status);
      };

      const handleLine = (line: string): void => {
        if (!line.trim()) {
          return;
        }
        try {
          const payload = JSON.parse(line) as { type: string; text?: string; message?: string; event?: Record<string, unknown> };
          clientDebugLog("send.bridge.payload", {
            runId,
            type: payload.type,
          });
          if (payload.type === "event" && payload.event) {
            hasStructuredEvent = true;
            stepParts = projectBridgeEventToStepParts(payload.event, stepParts).filter(
              (part) => !part.title.startsWith("Connecting to openppx"),
            );
            syncAssistant("streaming");
            return;
          }
          if (payload.type === "delta") {
            finalText = payload.text ?? finalText;
            if (hasStructuredEvent) {
              syncAssistant("streaming");
            } else {
              applyAssistantParts([{ type: "markdown", text: finalText }], "streaming");
            }
            return;
          }
          if (payload.type === "final") {
            finalText = payload.text ?? finalText;
            if (hasStructuredEvent) {
              stepParts = stepParts.map((part) =>
                part.status === "running"
                  ? { ...part, status: "completed", detail: `${part.detail}\n\nFinished without an explicit tool response event.` }
                  : part,
              );
              syncAssistant("completed");
            } else {
              applyAssistantParts([{ type: "markdown", text: finalText }], "completed");
            }
            return;
          }
          if (payload.type === "error") {
            if (hasStructuredEvent && stepParts.length) {
              stepParts = stepParts.map((part) =>
                part.status === "running"
                  ? { ...part, status: "failed", detail: payload.message ?? part.detail }
                  : part,
              );
              syncAssistant("failed");
              return;
            }
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
        clientDebugLog("send.bridge.stderr", {
          runId,
          text: chunk.toString().trim(),
        });
      });

      child.on("close", (code) => {
        clientDebugLog("send.bridge.close", {
          runId,
          code,
          assistantStatus: assistantMessage.status,
        });
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
          sessionId: input.sessionId,
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
    if (this.clientApiProcess && this.clientApiProcess.exitCode === null) {
      this.clientApiProcess.kill();
    }
    this.clientApiProcess = null;
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
