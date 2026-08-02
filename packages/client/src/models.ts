export type MessageRole = "user" | "assistant" | "system" | "tool";

export type MessageStatus = "streaming" | "completed" | "failed" | "cancelled";

export type MessagePart =
  | { type: "markdown"; text: string }
  | { type: "code"; text: string; language?: string }
  | { type: "file"; text: string; fileName: string; sizeBytes?: number; mimeType?: string }
  | { type: "image"; text: string; url: string; mimeType?: string }
  | { type: "error"; text: string; errorCode?: string }
  | { type: "tool_result"; toolName: string; summary: string; detail?: string; rawText?: string }
  | { type: "step_ref"; stepId: string; title: string; status: "running" | "completed" | "failed"; detail: string };

export interface AgentProfile {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  status: "healthy" | "idle" | "starting" | "disabled";
  tags: string[];
}

export interface SessionSummary {
  id: string;
  agentId: string;
  title: string;
  updatedAt: string;
  lastMessagePreview: string;
}

export interface ChatMessage {
  id: string;
  sessionId: string;
  role: MessageRole;
  status: MessageStatus;
  createdAt: string;
  parts: MessagePart[];
}

export type RunEvent =
  | {
      type: "message.created";
      runId: string;
      sessionId: string;
      message: ChatMessage;
    }
  | {
      type: "message.updated";
      runId: string;
      sessionId: string;
      messageId: string;
      status?: MessageStatus;
      appendParts?: MessagePart[];
      replaceParts?: MessagePart[];
    }
  | {
      type: "session.updated";
      runId: string;
      session: SessionSummary;
    }
  | {
      type: "run.finished";
      runId: string;
      sessionId: string;
    };

export interface SendMessageInput {
  agentId: string;
  sessionId: string;
  text: string;
}
