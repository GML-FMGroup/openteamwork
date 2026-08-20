import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage, MessagePart } from "../types";
import { projectActivityGroups, type ActivityGroup } from "../lib/activity-presentation";
import { ActivityDisclosure, type ActivityNarrativeItem } from "./ActivityDisclosure";
import { CommandResult } from "./CommandResult";
import { productProfile } from "../../../product";

const FILE_TYPE_BY_EXTENSION: Record<string, string> = {
  "7z": "Archive",
  csv: "Spreadsheet",
  doc: "Word document",
  docx: "Word document",
  gz: "Archive",
  json: "JSON file",
  md: "Markdown document",
  ods: "Spreadsheet",
  odt: "Text document",
  pdf: "PDF document",
  ppt: "Presentation",
  pptx: "Presentation",
  rar: "Archive",
  rtf: "Text document",
  tar: "Archive",
  tgz: "Archive",
  txt: "Text document",
  xls: "Spreadsheet",
  xlsx: "Spreadsheet",
  xml: "XML file",
  yaml: "YAML file",
  yml: "YAML file",
  zip: "Archive",
};

const GENERIC_FILE_DESCRIPTIONS = new Set([
  "attached file",
  "attachment",
  "file",
  "uploaded file",
]);

/** Return a normalized filename extension without treating hidden files as extensions. */
function fileExtension(fileName: string): string {
  const baseName = fileName.trim().split(/[\\/]/).at(-1) ?? "";
  const separator = baseName.lastIndexOf(".");
  if (separator <= 0 || separator === baseName.length - 1) {
    return "";
  }
  return baseName.slice(separator + 1).toLowerCase();
}

/** Return one human-readable file category from the filename, then MIME fallback. */
function fileKind(fileName: string, mimeType?: string): string {
  const extension = fileExtension(fileName);
  const knownKind = FILE_TYPE_BY_EXTENSION[extension];
  if (knownKind) {
    return knownKind;
  }

  const normalizedMime = mimeType?.trim().toLowerCase() ?? "";
  if (normalizedMime.includes("wordprocessing") || normalizedMime.includes("msword")) return "Word document";
  if (normalizedMime.includes("spreadsheet") || normalizedMime.includes("excel")) return "Spreadsheet";
  if (normalizedMime.includes("presentation") || normalizedMime.includes("powerpoint")) return "Presentation";
  if (normalizedMime === "application/pdf") return "PDF document";
  if (normalizedMime.startsWith("text/")) return "Text document";
  if (normalizedMime.startsWith("audio/")) return "Audio";
  if (normalizedMime.startsWith("video/")) return "Video";
  if (normalizedMime.includes("zip") || normalizedMime.includes("compressed")) return "Archive";
  return "File";
}

function fileExtensionBadge(fileName: string): string {
  const extension = fileExtension(fileName).toUpperCase();
  return extension && extension.length <= 5 ? extension : "FILE";
}

/** Format an optional byte count without inventing unavailable size metadata. */
function fileSize(sizeBytes?: number): string | null {
  if (sizeBytes === undefined || !Number.isFinite(sizeBytes) || sizeBytes < 0) {
    return null;
  }
  if (sizeBytes < 1024) {
    return `${Math.round(sizeBytes)} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  }
  const megabytes = sizeBytes / (1024 * 1024);
  return `${megabytes >= 10 ? Math.round(megabytes) : Number(megabytes.toFixed(1))} MB`;
}

function fileDescription(text: string, fileName: string): string | null {
  const description = text.trim();
  const normalized = description.toLowerCase();
  if (!description || normalized === fileName.trim().toLowerCase() || GENERIC_FILE_DESCRIPTIONS.has(normalized)) {
    return null;
  }
  return description;
}

function roleLabel(role: ChatMessage["role"], agentName?: string): string {
  if (role === "user") {
    return "";
  }
  if (role === "assistant") {
    return agentName?.trim() || "Agent";
  }
  if (role === "tool") {
    return "Tool";
  }
  return "System";
}

function markdownCodeFence(text: string): string {
  const longest = Math.max(0, ...Array.from(text.matchAll(/`+/g), (match) => match[0].length));
  return "`".repeat(Math.max(3, longest + 1));
}

/** Serialize authored visible content without copying internal Tool activity. */
function copyableMessageText(message: ChatMessage): string {
  return message.parts
    .map((part) => {
      if (part.type === "markdown" || part.type === "commentary") {
        return part.text.trim();
      }
      if (part.type === "code") {
        const fence = markdownCodeFence(part.text);
        const language = (part.language ?? "").trim().replace(/[^a-z0-9_+#.-]/gi, "");
        return `${fence}${language}\n${part.text}\n${fence}`;
      }
      if (part.type === "image") {
        return `![${part.text.replace(/]/g, "\\]")}](${part.url})`;
      }
      if (part.type === "file") {
        const description = part.text.trim();
        return `- **${part.fileName}**${description ? ` — ${description}` : ""}`;
      }
      if (part.type === "error") {
        return part.text.split("\n").map((line) => `> ${line}`).join("\n");
      }
      return "";
    })
    .filter(Boolean)
    .join("\n\n");
}

function ResponseActionIcon({ name }: { name: "copy" | "up" | "down" }) {
  if (name === "copy") {
    return (
      <svg aria-hidden="true" viewBox="0 0 20 20">
        <rect x="6.5" y="6.5" width="9" height="9" rx="2" />
        <path d="M13.5 6.5v-1A2 2 0 0 0 11.5 3.5h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2" />
      </svg>
    );
  }
  return (
    <svg
      aria-hidden="true"
      className={name === "down" ? "is-down" : ""}
      viewBox="0 0 20 20"
    >
      <path d="M6.5 8.5 9.2 3c.4-.8 1.4-1.2 2.2-.8.7.3 1.1 1 .9 1.8l-.5 2.2h3.3c1.2 0 2.1 1.1 1.8 2.3l-1.2 5.2c-.2.9-1 1.5-1.9 1.5H6.5" />
      <path d="M3 7.2h3.5v8.3H3z" />
    </svg>
  );
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
      hint: `Check the Node address, network connectivity, and whether ${productProfile.displayName} Node is running.`,
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
    const metadata = [
      fileDescription(part.text, part.fileName),
      fileKind(part.fileName, part.mimeType),
      fileSize(part.sizeBytes),
    ].filter((value): value is string => Boolean(value)).join(" · ");
    return (
      <div
        aria-label={`${part.fileName}, ${metadata}`}
        className="asset-card file-card"
        role="group"
        title={part.mimeType}
      >
        <span aria-hidden="true" className="file-card-kind">{fileExtensionBadge(part.fileName)}</span>
        <span className="file-card-copy">
          <strong title={part.fileName}>{part.fileName}</strong>
          <span className="file-card-meta">{metadata}</span>
        </span>
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
  agentName,
  showIdentity = true,
  activityGroups: activityGroupsOverride,
  activityStreaming,
  activityStartedAt,
  activityEndedAt,
  onFeedback,
  feedbackPending = false,
  feedbackError,
}: {
  message: ChatMessage;
  agentName?: string;
  showIdentity?: boolean;
  activityGroups?: ActivityGroup[];
  activityStreaming?: boolean;
  activityStartedAt?: string;
  activityEndedAt?: string;
  onFeedback?: (message: ChatMessage, rating: "up" | "down" | null) => void | Promise<void>;
  feedbackPending?: boolean;
  feedbackError?: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const statusLabel = messageStatusLabel(message.status);
  const isAssistant = message.role === "assistant";
  const isUser = message.role === "user";
  const commandResult = message.commandResult;
  const activityGroups = activityGroupsOverride ?? projectActivityGroups([message]);
  const hasActivity = activityGroups.length > 0;
  const contentParts = message.parts.filter((part) => (
    part.type !== "step_ref"
    && part.type !== "tool_result"
    && (!hasActivity || part.type !== "commentary")
    && (!commandResult || part.type !== "markdown")
  ));
  const hasCommentary = message.parts.some((part) => part.type === "commentary");
  const narrative = hasActivity && hasCommentary ? activityNarrative(message) : undefined;
  const activityStatus = activityStreaming === true
    ? "streaming"
    : activityStreaming === false && message.status === "streaming"
      ? "completed"
      : message.status;
  const timestamp = new Date(message.createdAt).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  const markdownText = copyableMessageText(message);

  async function copyMessage(): Promise<void> {
    if (!navigator.clipboard) {
      return;
    }
    try {
      await navigator.clipboard.writeText(markdownText);
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
      } ${commandResult ? "command-thread" : ""}`}
    >
      {showIdentity && !isUser ? (
        <div className={`message-meta ${isAssistant ? "agent-meta" : ""} ${commandResult ? "command-meta" : ""}`}>
          <span className={`${isAssistant ? "agent-name" : ""} ${commandResult ? "command-name" : ""}`}>
            {commandResult?.command ?? roleLabel(message.role, agentName)}
          </span>
          <span>{timestamp}</span>
        </div>
      ) : null}
      <div className="message-body">
        {commandResult ? (
          <CommandResult
            agentName={agentName}
            fallbackText={copyableMessageText(message)}
            presentation={commandResult}
          />
        ) : null}
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
      {isAssistant && message.status !== "streaming" && markdownText ? (
        <div className="assistant-response-actions" aria-label="Response actions">
          <button
            type="button"
            onClick={copyMessage}
            aria-label="Copy response as Markdown"
            title={copied ? "Copied as Markdown" : "Copy as Markdown"}
          >
            <ResponseActionIcon name="copy" />
          </button>
          {onFeedback ? (
            <>
              <button
                type="button"
                className={message.feedback === "up" ? "is-selected" : ""}
                onClick={() => void onFeedback(message, message.feedback === "up" ? null : "up")}
                aria-label="Good response"
                aria-pressed={message.feedback === "up"}
                disabled={feedbackPending}
                title="Good response"
              >
                <ResponseActionIcon name="up" />
              </button>
              <button
                type="button"
                className={message.feedback === "down" ? "is-selected" : ""}
                onClick={() => void onFeedback(message, message.feedback === "down" ? null : "down")}
                aria-label="Bad response"
                aria-pressed={message.feedback === "down"}
                disabled={feedbackPending}
                title="Bad response"
              >
                <ResponseActionIcon name="down" />
              </button>
            </>
          ) : null}
          <span className="response-action-status" aria-live="polite">
            {copied ? "Copied" : feedbackError ? `Feedback not saved: ${feedbackError}` : ""}
          </span>
        </div>
      ) : null}
    </article>
  );
}
