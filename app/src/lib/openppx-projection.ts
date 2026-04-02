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
      detail: "The local bridge is running, but no renderable event has arrived yet.",
    },
  ];
}

export function buildMessagePartsFromSessionEvent(event: Record<string, unknown>): MessagePart[] {
  const content = asRecord(event.content);
  const parts = asRecordList(content?.parts);
  const messageParts: MessagePart[] = [];

  for (const part of parts) {
    if (typeof part.text === "string" && part.text.trim()) {
      messageParts.push({ type: "markdown", text: part.text });
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
        type: "step_ref",
        stepId: String(functionResponse.id ?? toolName),
        title: toolName,
        status: "completed",
        detail: previewValue(response, "Tool returned without a payload"),
      });
      messageParts.push({
        type: "code",
        language: "json",
        text: stringifyDetail(response),
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

export function projectBridgeEventToStepParts(
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
      const stepId = String(functionResponse.id ?? crypto.randomUUID());
      const existing = nextParts.find((item) => item.stepId === stepId);
      nextParts = upsertStepPart(nextParts, {
        type: "step_ref",
        stepId,
        title: existing?.title ?? String(functionResponse.name ?? "Tool response"),
        status: "completed",
        detail: previewValue(functionResponse.response, "Tool returned without a payload"),
      });
    }
  }

  return nextParts;
}
