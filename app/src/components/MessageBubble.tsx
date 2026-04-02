import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage, MessagePart } from "../types";

function roleLabel(role: ChatMessage["role"]): string {
  if (role === "user") {
    return "你";
  }
  if (role === "assistant") {
    return "Agent";
  }
  if (role === "tool") {
    return "Tool";
  }
  return "System";
}

function stepStatusLabel(status: Extract<MessagePart, { type: "step_ref" }>["status"]): string {
  if (status === "running") {
    return "执行中";
  }
  if (status === "failed") {
    return "失败";
  }
  return "完成";
}

function messageStatusLabel(status: ChatMessage["status"]): string | null {
  if (status === "failed") {
    return "本次运行失败";
  }
  if (status === "cancelled") {
    return "本次运行已取消";
  }
  return null;
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
      <div className="asset-card">
        <strong>{part.fileName}</strong>
        <span>{part.text}</span>
        <small>
          {part.mimeType ?? "file"}
          {part.sizeBytes ? ` · ${Math.round(part.sizeBytes / 1024)} KB` : ""}
        </small>
      </div>
    );
  }
  if (part.type === "image") {
    return (
      <div className="asset-card">
        <strong>{part.text}</strong>
        <a href={part.url} target="_blank" rel="noreferrer">
          Open image
        </a>
      </div>
    );
  }
  if (part.type === "error") {
    return (
      <div className="error-card">
        <strong>{part.errorCode ?? "Error"}</strong>
        <span>{part.text}</span>
      </div>
    );
  }
  const detailLines = part.detail
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line, index, all) => line || (index > 0 && index < all.length - 1));
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
        {detailLines.map((line, index) => (
          <pre key={`${part.stepId}-${index}`} className="step-card-detail">
            {line}
          </pre>
        ))}
      </div>
    </div>
  );
}

export function MessageBubble({ message }: { message: ChatMessage }) {
  const statusLabel = messageStatusLabel(message.status);
  return (
    <article className={`message-bubble ${message.role} ${message.status}`}>
      <div className="message-meta">
        <span>{roleLabel(message.role)}</span>
        <span>{new Date(message.createdAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</span>
      </div>
      <div className="message-body">
        {message.parts.map((part, index) => (
          <div key={`${message.id}-${index}`}>{renderPart(part)}</div>
        ))}
        {message.status === "streaming" ? <div className="streaming-indicator">Agent 正在整理结果...</div> : null}
        {statusLabel ? <div className={`message-status-banner ${message.status}`}>{statusLabel}</div> : null}
      </div>
    </article>
  );
}
