import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage, MessagePart } from "../types";

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
  return (
    <div className={`step-card ${part.status}`}>
      <div className="step-card-header">
        <strong>{part.title}</strong>
        <span>{part.status}</span>
      </div>
      <p>{part.detail}</p>
    </div>
  );
}

export function MessageBubble({ message }: { message: ChatMessage }) {
  return (
    <article className={`message-bubble ${message.role}`}>
      <div className="message-meta">
        <span>{message.role}</span>
        <span>{new Date(message.createdAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</span>
      </div>
      <div className="message-body">
        {message.parts.map((part, index) => (
          <div key={`${message.id}-${index}`}>{renderPart(part)}</div>
        ))}
        {message.status === "streaming" ? <div className="streaming-indicator">streaming...</div> : null}
      </div>
    </article>
  );
}
