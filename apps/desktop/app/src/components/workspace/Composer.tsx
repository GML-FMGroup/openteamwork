import { useEffect, useMemo, useRef, useState } from "react";
import type { ClipboardEvent, DragEvent, KeyboardEvent, RefObject } from "react";
import type { ProjectedSlashCommand } from "../../types";
import type { PendingAttachment } from "../../hooks/use-desktop-workspace";
import { ATTACHMENT_ACCEPT } from "../../attachment-policy";

interface ComposerProps {
  value: string;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  canSend: boolean;
  busy: boolean;
  canStop: boolean;
  stopping: boolean;
  helperText: string;
  agentName: string;
  commands: ProjectedSlashCommand[];
  attachments: PendingAttachment[];
  onChange: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  onStop: () => void;
  onAddAttachments: (files: File[]) => void;
  onRemoveAttachment: (attachmentId: string) => void;
}

/** Focused composer that only exposes controls backed by real behavior. */
export function Composer({
  value,
  textareaRef,
  canSend,
  busy,
  canStop,
  stopping,
  helperText,
  agentName,
  commands,
  attachments,
  onChange,
  onKeyDown,
  onSend,
  onStop,
  onAddAttachments,
  onRemoveAttachment,
}: ComposerProps) {
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const [dismissedValue, setDismissedValue] = useState<string | null>(null);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const commandToken = value.trimStart().split(/\s/, 1)[0]?.toLowerCase() ?? "";
  const matchingCommands = useMemo(
    () =>
      commandToken.startsWith("/")
        ? commands.filter((command) => command.available && command.command.startsWith(commandToken))
        : [],
    [commandToken, commands],
  );
  const commandMenuOpen = matchingCommands.length > 0 && dismissedValue !== value;

  useEffect(() => {
    setSelectedCommandIndex(0);
  }, [commandToken]);

  useEffect(() => {
    if (dismissedValue !== null && dismissedValue !== value) {
      setDismissedValue(null);
    }
  }, [dismissedValue, value]);

  function chooseCommand(command: ProjectedSlashCommand): void {
    const nextValue = `${command.command}${command.acceptsArgs ? " " : ""}`;
    onChange(nextValue);
    setDismissedValue(nextValue);
    queueMicrotask(() => textareaRef.current?.focus());
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (commandMenuOpen && event.key === "ArrowDown") {
      event.preventDefault();
      setSelectedCommandIndex((current) => (current + 1) % matchingCommands.length);
      return;
    }
    if (commandMenuOpen && event.key === "ArrowUp") {
      event.preventDefault();
      setSelectedCommandIndex((current) => (current - 1 + matchingCommands.length) % matchingCommands.length);
      return;
    }
    if (commandMenuOpen && event.key === "Escape") {
      event.preventDefault();
      setDismissedValue(value);
      return;
    }
    if (commandMenuOpen && event.key === "Enter" && !event.shiftKey) {
      const selected = matchingCommands[selectedCommandIndex] ?? matchingCommands[0];
      const normalized = value.trim();
      const alreadySelected = normalized === selected.command || normalized.startsWith(`${selected.command} `);
      if (!alreadySelected) {
        event.preventDefault();
        chooseCommand(selected);
        return;
      }
    }
    onKeyDown(event);
  }

  const actionLabel = busy ? (stopping ? "Stopping" : canStop ? "Stop" : "Running") : "Send";
  const actionEnabled = busy ? canStop && !stopping : canSend;
  function addFiles(files: FileList | null): void {
    if (files?.length) onAddAttachments(Array.from(files));
  }
  function handleDrop(event: DragEvent<HTMLElement>): void {
    event.preventDefault();
    setDraggingFiles(false);
    addFiles(event.dataTransfer.files);
  }
  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>): void {
    const files = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    if (files.length) {
      event.preventDefault();
      onAddAttachments(files);
    }
  }
  return (
    <footer
      className={draggingFiles ? "composer-shell dragging-files" : "composer-shell"}
      onDragEnter={(event) => { event.preventDefault(); setDraggingFiles(true); }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDraggingFiles(false);
      }}
      onDrop={handleDrop}
    >
      {commandMenuOpen ? (
        <div className="command-palette" role="listbox" aria-label="Slash commands">
          <div className="command-palette-heading">
            <span>Commands</span>
            <kbd>↑↓ Enter</kbd>
          </div>
          <div className="command-palette-list">
            {matchingCommands.map((command, index) => (
              <button
                type="button"
                role="option"
                aria-selected={index === selectedCommandIndex}
                className={index === selectedCommandIndex ? "command-option selected" : "command-option"}
                key={command.command}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => chooseCommand(command)}
              >
                <span className="command-glyph">/</span>
                <span className="command-copy">
                  <strong>{command.command}</strong>
                  <small>{command.description}</small>
                </span>
                {command.argHint ? <code>{command.argHint}</code> : null}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="composer-context">
        <span>{agentName}</span>
      </div>
      {attachments.length ? (
        <div className="composer-attachments" aria-label="Attachments">
          {attachments.map((attachment) => (
            <div className={`composer-attachment ${attachment.status}`} key={attachment.id}>
              <span className="composer-attachment-kind">
                {attachment.mimeType.startsWith("image/") ? "IMG" : attachment.fileName.split(".").pop()?.slice(0, 3).toUpperCase() || "FILE"}
              </span>
              <span>
                <strong>{attachment.fileName}</strong>
                <small>{attachment.status === "uploading" ? "Uploading…" : `${Math.max(1, Math.round(attachment.sizeBytes / 1024))} KB`}</small>
              </span>
              <button type="button" aria-label={`Remove ${attachment.fileName}`} disabled={attachment.status === "uploading"} onClick={() => onRemoveAttachment(attachment.id)}>×</button>
            </div>
          ))}
        </div>
      ) : null}
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        placeholder="Describe the outcome you want..."
        rows={2}
      />
      <div className="composer-actions">
        <span className="composer-left-actions">
          <input
            ref={fileInputRef}
            className="composer-file-input"
            type="file"
            multiple
            accept={ATTACHMENT_ACCEPT}
            onChange={(event) => { addFiles(event.target.files); event.currentTarget.value = ""; }}
          />
          <button type="button" className="composer-attach-button" aria-label="Attach files" title="Attach files" disabled={busy} onClick={() => fileInputRef.current?.click()}>+</button>
          <span className={helperText ? "composer-helper" : undefined}>
            {helperText || "Enter to send · Shift+Enter for a new line"}
          </span>
        </span>
        <button
          className={`${actionEnabled ? "send-button ready" : "send-button"}${busy && canStop ? " stop" : ""}`}
          disabled={!actionEnabled}
          onClick={busy ? onStop : onSend}
          aria-label={actionLabel}
          title={busy ? (canStop ? "Stop the current Run" : "The current Agent is starting") : "Send"}
        >
          <span>{actionLabel}</span>
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M3.5 10.8 15.6 4.9c.8-.4 1.5.3 1.1 1.1l-5.9 12.1c-.4.9-1.7.8-2-.1L7.2 12.6a1 1 0 0 0-.6-.6L3.6 10.3c-.9-.3-.9-1.6-.1-2Z" />
          </svg>
        </button>
      </div>
    </footer>
  );
}
