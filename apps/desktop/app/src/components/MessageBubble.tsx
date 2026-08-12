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
}: {
  message: ChatMessage;
  agentName?: string;
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
    </article>
  );
}
