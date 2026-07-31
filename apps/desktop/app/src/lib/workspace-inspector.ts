import type { ChatMessage, MessagePart } from "../types";

export interface ActivityItem {
  id: string;
  kind: "step" | "tool" | "error";
  title: string;
  detail: string;
  status: "running" | "completed" | "failed";
  messageId: string;
}

export interface ArtifactItem {
  id: string;
  kind: "file" | "image";
  title: string;
  description: string;
  mimeType?: string;
  sizeBytes?: number;
  url?: string;
  messageId: string;
}

function activityFromPart(message: ChatMessage, part: MessagePart, index: number): ActivityItem | null {
  if (part.type === "step_ref") {
    return {
      id: `step:${part.stepId}`,
      kind: "step",
      title: part.title,
      detail: part.detail,
      status: part.status,
      messageId: message.id,
    };
  }
  if (part.type === "tool_result") {
    return {
      id: `tool:${message.id}:${index}`,
      kind: "tool",
      title: part.toolName,
      detail: part.detail ? `${part.summary}\n${part.detail}` : part.summary,
      status: message.status === "failed" ? "failed" : "completed",
      messageId: message.id,
    };
  }
  if (part.type === "error") {
    return {
      id: `error:${message.id}:${index}`,
      kind: "error",
      title: part.errorCode || "运行错误",
      detail: part.text,
      status: "failed",
      messageId: message.id,
    };
  }
  return null;
}

/** Project transcript parts into a stable, inspectable activity timeline. */
export function projectActivityItems(messages: ChatMessage[]): ActivityItem[] {
  const orderedIds: string[] = [];
  const items = new Map<string, ActivityItem>();
  messages.forEach((message) => {
    message.parts.forEach((part, index) => {
      const item = activityFromPart(message, part, index);
      if (!item) {
        return;
      }
      if (!items.has(item.id)) {
        orderedIds.push(item.id);
      }
      items.set(item.id, item);
    });
  });
  return orderedIds.map((id) => items.get(id)!).filter(Boolean);
}

/** Project existing file and image message parts without inventing an artifact backend. */
export function projectArtifactItems(messages: ChatMessage[]): ArtifactItem[] {
  const items = new Map<string, ArtifactItem>();
  messages.forEach((message) => {
    message.parts.forEach((part, index) => {
      if (part.type === "file") {
        const id = `file:${part.fileName}`;
        items.set(id, {
          id,
          kind: "file",
          title: part.fileName,
          description: part.text,
          mimeType: part.mimeType,
          sizeBytes: part.sizeBytes,
          messageId: message.id,
        });
      } else if (part.type === "image") {
        const id = `image:${part.url || `${message.id}:${index}`}`;
        items.set(id, {
          id,
          kind: "image",
          title: part.text || "Image",
          description: "来自当前 Session 对话的图像产物。",
          mimeType: part.mimeType,
          url: part.url,
          messageId: message.id,
        });
      }
    });
  });
  return [...items.values()];
}
