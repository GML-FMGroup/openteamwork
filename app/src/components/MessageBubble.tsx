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

function explainError(errorCode: string | undefined, text: string): { title: string; hint?: string } {
  const normalized = `${errorCode ?? ""} ${text}`.toLowerCase();
  if (normalized.includes("provider list") || normalized.includes("litellm")) {
    return {
      title: "模型提供方配置异常",
      hint: "通常是 provider 名称、模型名，或对应的密钥配置不匹配。",
    };
  }
  if (normalized.includes("timeout") || normalized.includes("timed out")) {
    return {
      title: "请求超时",
      hint: "可以稍后重试，或者检查当前模型和网络状态。",
    };
  }
  if (normalized.includes("network") || normalized.includes("connection") || normalized.includes("unreachable")) {
    return {
      title: "连接失败",
      hint: "请检查 gateway 地址、网络连通性，或本地 client-api 是否正在运行。",
    };
  }
  return {
    title: errorCode === "RUN_FAILED" ? "运行失败" : errorCode ?? "Error",
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
            打开原图
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
          <span className="asset-badge">完成</span>
        </div>
        <p>{part.summary}</p>
        {part.detail ? <small>{part.detail}</small> : null}
        {part.rawText ? (
          <details className="tool-result-raw">
            <summary>查看原始结果</summary>
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
  const statusLabel = messageStatusLabel(message.status);
  const isAssistant = message.role === "assistant";
  return (
    <article className={`message-bubble ${message.role} ${message.status} ${isAssistant ? "agent-thread" : ""}`}>
      {showIdentity ? (
        <div className={`message-meta ${isAssistant ? "agent-meta" : ""}`}>
          <span className={isAssistant ? "agent-name" : ""}>{roleLabel(message.role)}</span>
          <span>{new Date(message.createdAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</span>
        </div>
      ) : null}
      <div className="message-body">
        {message.parts.map((part, index) => (
          <div key={`${message.id}-${index}`}>{renderPart(part)}</div>
        ))}
        {message.status === "streaming" ? <div className="streaming-indicator">正在整理结果...</div> : null}
        {statusLabel ? <div className={`message-status-banner ${message.status}`}>{statusLabel}</div> : null}
      </div>
    </article>
  );
}
