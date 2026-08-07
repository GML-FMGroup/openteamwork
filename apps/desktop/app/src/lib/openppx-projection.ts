import type { MessagePart, MessageRole } from "../types";

type StepPart = Extract<MessagePart, { type: "step_ref" }>;

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asRecordList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map((item) => asRecord(item)).filter((item): item is Record<string, unknown> => item !== null) : [];
}

function stripRequestTimePrefix(text: string): string {
  const stripped = text.trim();
  if (!stripped.startsWith("Current request time: ")) {
    return text;
  }
  const lines = stripped.split(/\r?\n/);
  if (lines.length < 2 || !lines[1]?.includes("Use this as the reference 'now' for relative time expressions")) {
    return text;
  }
  const bodyLines = lines.slice(2);
  while (bodyLines.length && !bodyLines[0]?.trim()) {
    bodyLines.shift();
  }
  return bodyLines.join("\n").trim();
}

function stringifyDetail(value: unknown): string {
  if (typeof value === "string") {
    return value.trim() || "(empty)";
  }
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value ?? "");
  }
}

function previewValue(value: unknown, fallback: string): string {
  const detail = stringifyDetail(value).trim();
  if (!detail || detail === "{}") {
    return fallback;
  }
  return detail.length > 320 ? `${detail.slice(0, 317)}...` : detail;
}

function summarizeToolResponse(toolName: string, response: unknown): string {
  const record = asRecord(response);
  if (record) {
    if (typeof record.message === "string" && record.message.trim()) {
      return record.message.trim();
    }
    if (typeof record.summary === "string" && record.summary.trim()) {
      return record.summary.trim();
    }
    if (typeof record.ok === "boolean") {
      return record.ok ? `${toolName} completed successfully.` : `${toolName} returned a failed result.`;
    }
    const keys = Object.keys(record);
    if (keys.length) {
      return `${toolName} returned ${keys.length} fields.`;
    }
  }
  if (typeof response === "string" && response.trim()) {
    return response.trim().slice(0, 140);
  }
  return `${toolName} returned a result.`;
}

function upsertStepPart(parts: StepPart[], nextPart: StepPart): StepPart[] {
  const existingIndex = parts.findIndex((part) => part.stepId === nextPart.stepId);
  if (existingIndex === -1) {
    return [...parts, nextPart];
  }
  return parts.map((part, index) => (index === existingIndex ? nextPart : part));
}

export function sessionEventRole(author: string): MessageRole {
  if (author === "user") {
    return "user";
  }
  if (author === "tool") {
    return "tool";
  }
  if (author === "system") {
    return "system";
  }
  return "assistant";
}

export function mergeAssistantParts(stepParts: StepPart[], text: string): MessagePart[] {
  const parts: MessagePart[] = [...stepParts];
  if (text.trim()) {
    parts.push({ type: "markdown", text });
  }
  if (parts.length) {
    return parts;
  }
  return [
    {
      type: "step_ref",
      stepId: `step-${crypto.randomUUID()}`,
      title: "Waiting for assistant output",
      status: "running",
      detail: "The Node run is active, but no renderable event has arrived yet.",
    },
  ];
}

export function buildMessagePartsFromSessionEvent(event: Record<string, unknown>): MessagePart[] {
  const content = asRecord(event.content);
  const parts = asRecordList(content?.parts);
  const messageParts: MessagePart[] = [];

  for (const part of parts) {
    if (typeof part.text === "string" && part.text.trim()) {
      const normalizedText = stripRequestTimePrefix(part.text);
      if (normalizedText.trim()) {
        messageParts.push({ type: "markdown", text: normalizedText });
      }
    }

    const functionCall = asRecord(part.function_call);
    if (functionCall) {
      const stepId = String(functionCall.id ?? crypto.randomUUID());
      const toolName = String(functionCall.name ?? "Tool call");
      messageParts.push({
        type: "step_ref",
        stepId,
        title: toolName,
        status: "completed",
        detail: previewValue(functionCall.args, "No tool arguments"),
      });
    }

    const functionResponse = asRecord(part.function_response);
    if (functionResponse) {
      const response = functionResponse.response ?? {};
      const toolName = String(functionResponse.name ?? functionResponse.id ?? "Tool response");
      messageParts.push({
        type: "tool_result",
        toolCallId: typeof functionResponse.id === "string" ? functionResponse.id : undefined,
        toolName,
        summary: summarizeToolResponse(toolName, response),
        detail: previewValue(response, "Tool returned without a payload"),
        rawText: stringifyDetail(response),
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

export function projectRunEventToStepParts(
  event: Record<string, unknown>,
  currentParts: StepPart[],
): StepPart[] {
  let nextParts = [...currentParts];
  const content = asRecord(event.content);
  const parts = asRecordList(content?.parts);
  const longRunningIds = new Set(
    Array.isArray(event.long_running_tool_ids) ? event.long_running_tool_ids.map((item) => String(item)) : [],
  );

  for (const part of parts) {
    const functionCall = asRecord(part.function_call);
    if (functionCall) {
      const stepId = String(functionCall.id ?? crypto.randomUUID());
      const toolName = String(functionCall.name ?? "Tool call");
      const isLongRunning = longRunningIds.has(stepId);
      const detail = isLongRunning
        ? `Background task is running.\n\n${previewValue(functionCall.args, "No tool arguments")}`
        : previewValue(functionCall.args, "No tool arguments");
      nextParts = upsertStepPart(nextParts, {
        type: "step_ref",
        stepId,
        title: toolName,
        status: "running",
        detail,
      });
    }

    const functionResponse = asRecord(part.function_response);
    if (functionResponse) {
      const responseName = String(functionResponse.name ?? "Tool response");
      const explicitStepId = typeof functionResponse.id === "string" ? functionResponse.id : "";
      const existing = (explicitStepId ? nextParts.find((item) => item.stepId === explicitStepId) : undefined)
        ?? [...nextParts].reverse().find((item) => item.title === responseName && item.status === "running");
      const stepId = existing?.stepId ?? (explicitStepId || crypto.randomUUID());
      nextParts = upsertStepPart(nextParts, {
        type: "step_ref",
        stepId,
        title: existing?.title ?? responseName,
        status: "completed",
        detail: previewValue(functionResponse.response, "Tool returned without a payload"),
      });
    }
  }

  return nextParts;
}
