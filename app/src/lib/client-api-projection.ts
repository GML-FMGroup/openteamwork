import type {
  ChatMessage,
  MessagePart,
  MessageRole,
  MessageStatus,
  RuntimeState,
  RuntimeStatus,
  SessionSummary,
} from "../types";

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function normalizeRole(value: unknown): MessageRole {
  const role = asString(value, "assistant");
  if (role === "user" || role === "assistant" || role === "system" || role === "tool") {
    return role;
  }
  return "assistant";
}

function normalizeStatus(value: unknown, fallback: MessageStatus): MessageStatus {
  const status = asString(value, fallback);
  if (status === "streaming" || status === "completed" || status === "failed" || status === "cancelled") {
    return status;
  }
  return fallback;
}

function normalizeRuntimeState(value: unknown): RuntimeState {
  const state = asString(value, "healthy");
  if (state === "stopped" || state === "starting" || state === "healthy" || state === "error") {
    return state;
  }
  return "healthy";
}

export function normalizeClientApiPart(payload: unknown): MessagePart | null {
  const part = asRecord(payload);
  if (!part) {
    return null;
  }
  const type = asString(part.type);
  if (type === "markdown") {
    return { type, text: asString(part.text) };
  }
  if (type === "code") {
    return { type, text: asString(part.text), language: asString(part.language) || undefined };
  }
  if (type === "file") {
    return {
      type,
      text: asString(part.text),
      fileName: asString(part.file_name ?? part.fileName, "file"),
      sizeBytes: typeof part.size_bytes === "number" ? part.size_bytes : undefined,
      mimeType: asString(part.mime_type) || undefined,
    };
  }
  if (type === "image") {
    return {
      type,
      text: asString(part.text),
      url: asString(part.url),
      mimeType: asString(part.mime_type) || undefined,
    };
  }
  if (type === "error") {
    return {
      type,
      text: asString(part.text),
      errorCode: asString(part.error_code) || undefined,
    };
  }
  if (type === "tool_result") {
    return {
      type,
      toolName: asString(part.tool_name ?? part.toolName, "tool"),
      summary: asString(part.summary, "Tool returned without a payload"),
      detail: asString(part.detail) || undefined,
      rawText: asString(part.raw_text ?? part.rawText) || undefined,
    };
  }
  if (type === "step_ref") {
    const status = asString(part.status, "running");
    return {
      type,
      stepId: asString(part.step_id ?? part.stepId, "step"),
      title: asString(part.title, "step"),
      status: status === "completed" || status === "failed" ? status : "running",
      detail: asString(part.detail),
    };
  }
  return null;
}

export function normalizeClientApiMessage(payload: unknown): ChatMessage | null {
  const message = asRecord(payload);
  if (!message) {
    return null;
  }
  const rawParts = Array.isArray(message.parts) ? message.parts : [];
  return {
    id: asString(message.id),
    sessionId: asString(message.session_id ?? message.sessionId),
    role: normalizeRole(message.role),
    status: normalizeStatus(message.status, "completed"),
    createdAt: asString(message.created_at ?? message.createdAt, new Date().toISOString()),
    parts: rawParts
      .map((part) => normalizeClientApiPart(part))
      .filter((part): part is MessagePart => part !== null),
  };
}

export function normalizeClientApiSession(payload: unknown): SessionSummary | null {
  const session = asRecord(payload);
  if (!session) {
    return null;
  }
  return {
    id: asString(session.id),
    agentId: asString(session.agent_id ?? session.agentId),
    title: asString(session.title, "Session"),
    updatedAt: asString(session.updated_at ?? session.updatedAt, new Date().toISOString()),
    lastMessagePreview: asString(session.last_message_preview ?? session.lastMessagePreview, ""),
  };
}

export function normalizeClientApiRuntime(payload: unknown): RuntimeStatus | null {
  const runtime = asRecord(payload);
  const target = asRecord(runtime?.target);
  if (!runtime || !target) {
    return null;
  }
  return {
    target: {
      id: asString(target.id, "local-default"),
      type: asString(target.type) === "remote" ? "remote" : "local",
      name: asString(target.name, "This Mac"),
    },
    state: normalizeRuntimeState(runtime.state),
    summary: asString(runtime.summary),
    detail: asString(runtime.detail) || undefined,
    lastError: asString(runtime.lastError ?? runtime.last_error) || undefined,
  };
}
