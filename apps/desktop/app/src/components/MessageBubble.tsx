import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage, MessagePart } from "../types";
import { projectActivityGroups, type ActivityGroup } from "../lib/activity-presentation";
import { ActivityDisclosure, type ActivityNarrativeItem } from "./ActivityDisclosure";

function roleLabel(role: ChatMessage["role"]): string {
  if (role === "user") {
    return "";
  }
  if (role === "assistant") {
    return "Agent";
  }
  if (role === "tool") {
    return "Tool";
  }
  return "System";
}

function copyableMessageText(message: ChatMessage): string {
  return message.parts
    .map((part) => {
      if (part.type === "tool_result") {
        return part.summary;
      }
      if (part.type === "step_ref") {
        return part.detail;
      }
      return part.text;
    })
    .filter(Boolean)
    .join("\n\n");
}

function messageStatusLabel(status: ChatMessage["status"]): string | null {
  if (status === "failed") {
    return "This run failed";
  }
  if (status === "cancelled") {
    return "This run was cancelled";
  }
  return null;
}

function explainError(errorCode: string | undefined, text: string): { title: string; hint?: string } {
  const normalized = `${errorCode ?? ""} ${text}`.toLowerCase();
  if (normalized.includes("provider list") || normalized.includes("litellm")) {
    return {
      title: "Model provider configuration error",
      hint: "The provider name, model name, or corresponding credential may not match.",
    };
  }
  if (normalized.includes("timeout") || normalized.includes("timed out")) {
    return {
      title: "Request timed out",
      hint: "Try again later, or check the current model and network status.",
    };
  }
  if (normalized.includes("network") || normalized.includes("connection") || normalized.includes("unreachable")) {
    return {
      title: "Connection failed",
      hint: "Check the Node address, network connectivity, and whether OpenPPX Node is running.",
    };
  }
  return {
    title: errorCode === "RUN_FAILED" ? "Run failed" : errorCode ?? "Error",
  };
}

function renderPart(part: MessagePart) {
  if (part.type === "commentary") {
    return (
      <div className="run-commentary">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{part.text}</ReactMarkdown>
      </div>
    );
  }
  if (part.type === "markdown") {
    return (
      <div className="rich-markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{part.text}</ReactMarkdown>
      </div>
    );
  }
  if (part.type === "code") {
    return (
      <div className="code-card">
        <div className="code-card-header">{part.language ?? "code"}</div>
        <pre>
          <code>{part.text}</code>
        </pre>
      </div>
    );
  }
  if (part.type === "file") {
    return (
      <div className="asset-card file-card">
        <div className="asset-card-header">
          <div>
            <strong>{part.fileName}</strong>
            <span>{part.text}</span>
          </div>
          <span className="asset-badge">{part.mimeType ?? "file"}</span>
        </div>
        <small>{part.sizeBytes ? `${Math.max(1, Math.round(part.sizeBytes / 1024))} KB` : "size unavailable"}</small>
      </div>
    );
  }
  if (part.type === "image") {
    return (
      <div className="asset-card image-card">
        <img src={part.url} alt={part.text} className="image-preview" />
        <div className="asset-card-header">
          <div>
            <strong>{part.text}</strong>
            <span>{part.mimeType ?? "image"}</span>
          </div>
          <a href={part.url} target="_blank" rel="noreferrer">
            Open original
          </a>
        </div>
      </div>
    );
  }
  if (part.type === "error") {
    const explained = explainError(part.errorCode, part.text);
    return (
      <div className="error-card">
        <strong>{explained.title}</strong>
        {explained.hint ? <small>{explained.hint}</small> : null}
        <span>{part.text}</span>
      </div>
    );
  }
  return null;
}

type ActivityPart = Extract<MessagePart, { type: "step_ref" | "tool_result" }>;
/** Build one turn-level progress narrative without losing commentary/tool chronology. */
function activityNarrative(message: ChatMessage): ActivityNarrativeItem[] {
  const items: ActivityNarrativeItem[] = [];
  let activityParts: ActivityPart[] = [];
  const flushActivity = (): void => {
    if (activityParts.length) {
      const groups = projectActivityGroups([{ ...message, parts: activityParts }]);
      if (groups.length) {
        items.push({
          id: `activity-${items.length}-${groups[0]?.entries[0]?.id ?? groups[0]?.key ?? "segment"}`,
          kind: "activity",
          groups,
        });
      }
      activityParts = [];
    }
  };

  for (const part of message.parts) {
    if (part.type === "step_ref" || part.type === "tool_result") {
      activityParts.push(part);
      continue;
    }
    flushActivity();
    if (part.type === "commentary" && part.text.trim()) {
      items.push({
        id: `commentary-${items.length}`,
        kind: "commentary",
        text: part.text,
      });
    }
  }
  flushActivity();
  return items;
}

export function MessageBubble({
  message,
  showIdentity = true,
  activityGroups: activityGroupsOverride,
  activityStreaming,
  activityStartedAt,
  activityEndedAt,
}: {
  message: ChatMessage;
  showIdentity?: boolean;
  activityGroups?: ActivityGroup[];
  activityStreaming?: boolean;
  activityStartedAt?: string;
  activityEndedAt?: string;
}) {
  const [copied, setCopied] = useState(false);
  const statusLabel = messageStatusLabel(message.status);
  const isAssistant = message.role === "assistant";
  const isUser = message.role === "user";
  const activityGroups = activityGroupsOverride ?? projectActivityGroups([message]);
  const hasActivity = activityGroups.length > 0;
  const contentParts = message.parts.filter((part) => (
    part.type !== "step_ref"
    && part.type !== "tool_result"
    && (!hasActivity || part.type !== "commentary")
  ));
  const hasCommentary = message.parts.some((part) => part.type === "commentary");
  const narrative = hasActivity && hasCommentary ? activityNarrative(message) : undefined;
  const activityStatus = activityStreaming === true
    ? "streaming"
    : activityStreaming === false && message.status === "streaming"
      ? "completed"
      : message.status;
  const timestamp = new Date(message.createdAt).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });

  async function copyMessage(): Promise<void> {
    if (!navigator.clipboard) {
      return;
    }
    try {
      await navigator.clipboard.writeText(copyableMessageText(message));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard access may be denied when the window is not focused.
    }
  }

  return (
    <article
      id={`message-${message.id}`}
      className={`message-bubble ${message.role} ${message.status} ${
        isAssistant ? "agent-thread plain-assistant" : ""
      }`}
    >
      {showIdentity && !isUser ? (
        <div className={`message-meta ${isAssistant ? "agent-meta" : ""}`}>
          <span className={isAssistant ? "agent-name" : ""}>{roleLabel(message.role)}</span>
          <span>{timestamp}</span>
        </div>
      ) : null}
      <div className="message-body">
        {activityGroups.length ? (
          <ActivityDisclosure
            groups={activityGroups}
            narrative={narrative}
            status={activityStatus}
            startedAt={activityStartedAt ?? message.createdAt}
            endedAt={activityEndedAt}
          />
        ) : null}
        {contentParts.map((part, index) => (
          <div key={`${message.id}-${index}`}>{renderPart(part)}</div>
        ))}
        {message.status === "streaming" && !activityGroups.length ? <div className="streaming-indicator">Preparing the result...</div> : null}
        {statusLabel ? <div className={`message-status-banner ${message.status}`}>{statusLabel}</div> : null}
      </div>
      {isUser ? (
        <div className="user-message-actions" aria-label="Message actions">
          <button type="button" onClick={copyMessage} aria-label="Copy message">
            {copied ? "Copied" : "Copy"}
          </button>
          <time dateTime={message.createdAt}>{timestamp}</time>
        </div>
      ) : null}
    </article>
  );
}
