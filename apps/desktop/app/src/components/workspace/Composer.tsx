import { useEffect, useMemo, useState } from "react";
import type { KeyboardEvent, RefObject } from "react";
import type { ProjectedSlashCommand } from "../../types";

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
  onChange: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  onStop: () => void;
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
  onChange,
  onKeyDown,
  onSend,
  onStop,
}: ComposerProps) {
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const [dismissedValue, setDismissedValue] = useState<string | null>(null);
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
  return (
    <footer className="composer-shell">
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
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Describe the outcome you want..."
        rows={2}
      />
      <div className="composer-actions">
        <span className={helperText ? "composer-helper" : undefined}>
          {helperText || "Enter to send · Shift+Enter for a new line"}
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
