import type { KeyboardEvent, RefObject } from "react";

interface ComposerProps {
  value: string;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  canSend: boolean;
  busy: boolean;
  canStop: boolean;
  stopping: boolean;
  helperText: string;
  agentName: string;
  nodeName: string;
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
  nodeName,
  onChange,
  onKeyDown,
  onSend,
  onStop,
}: ComposerProps) {
  const actionLabel = busy ? (stopping ? "Stopping" : canStop ? "Stop" : "Running") : "Send";
  const actionEnabled = busy ? canStop && !stopping : canSend;
  return (
    <footer className="composer-shell">
      <div className="composer-context">
        <span>{agentName}</span>
        <span>Running on {nodeName}</span>
      </div>
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
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
