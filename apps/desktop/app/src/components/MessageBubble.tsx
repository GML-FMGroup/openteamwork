import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage, MessagePart } from "../types";

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

function stepStatusLabel(status: Extract<MessagePart, { type: "step_ref" }>["status"]): string {
  if (status === "running") {
    return "Running";
  }
  if (status === "failed") {
    return "Failed";
  }
  return "Completed";
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
  if (part.type === "tool_result") {
    return (
      <div className="tool-result-card">
        <div className="tool-result-header">
          <div>
            <strong>{part.toolName}</strong>
            <span>Tool result</span>
          </div>
          <span className="asset-badge">Completed</span>
        </div>
        <p>{part.summary}</p>
        {part.detail ? <small>{part.detail}</small> : null}
        {part.rawText ? (
          <details className="tool-result-raw">
            <summary>View raw result</summary>
            <pre>
              <code>{part.rawText}</code>
            </pre>
          </details>
        ) : null}
      </div>
    );
  }
  const detailText = part.detail
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line, index, all) => line || (index > 0 && index < all.length - 1))
    .join("\n");
  return (
    <div className={`step-card ${part.status}`}>
      <div className="step-card-header">
        <div className="step-card-title">
          <span className={`step-status-dot ${part.status}`} />
          <strong>{part.title}</strong>
        </div>
        <span className={`step-status-badge ${part.status}`}>{stepStatusLabel(part.status)}</span>
      </div>
      <div className="step-card-body">
        {part.status === "running" ? (
          <div className="step-progress-bar" aria-hidden="true">
            <span />
          </div>
        ) : null}
        {detailText ? <pre className="step-card-detail">{detailText}</pre> : null}
      </div>
    </div>
  );
}

export function MessageBubble({ message, showIdentity = true }: { message: ChatMessage; showIdentity?: boolean }) {
  const [copied, setCopied] = useState(false);
  const statusLabel = messageStatusLabel(message.status);
  const isAssistant = message.role === "assistant";
  const isUser = message.role === "user";
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
        {message.parts.map((part, index) => (
          <div key={`${message.id}-${index}`}>{renderPart(part)}</div>
        ))}
        {message.status === "streaming" ? <div className="streaming-indicator">Preparing the result...</div> : null}
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
