export type MessageRole = "user" | "assistant" | "system" | "tool";

export type MessageStatus = "streaming" | "completed" | "failed" | "cancelled";

export type MessagePart =
  | { type: "markdown"; text: string }
  | { type: "commentary"; text: string }
  | { type: "code"; text: string; language?: string }
  | { type: "file"; text: string; fileName: string; sizeBytes?: number; mimeType?: string }
  | { type: "image"; text: string; url: string; mimeType?: string }
  | { type: "error"; text: string; errorCode?: string }
  | { type: "tool_result"; toolCallId?: string; toolName: string; summary: string; detail?: string; rawText?: string }
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
  archived?: boolean;
}

export interface ChatMessage {
  id: string;
  sessionId: string;
  /** Stable identity of the Client Run or replayed ADK Invocation that produced this message. */
  runId?: string | null;
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
  artifactRefs?: ArtifactReference[];
}

export interface ArtifactReference {
  key: string;
  version: number;
}

export interface ArtifactSummary extends ArtifactReference {
  id: string;
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  source: "user_upload" | "agent_output" | string;
  createdAt: string;
}

export interface ArtifactUploadInput {
  agentId: string;
  sessionId: string;
  fileName: string;
  mimeType: string;
  dataBase64: string;
}
