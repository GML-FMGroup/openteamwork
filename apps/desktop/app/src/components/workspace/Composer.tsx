import type { KeyboardEvent, RefObject } from "react";

interface ComposerProps {
  value: string;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  canSend: boolean;
  busy: boolean;
  helperText: string;
  agentName: string;
  nodeName: string;
  onChange: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
}

/** Focused composer that only exposes controls backed by real behavior. */
export function Composer({
  value,
  textareaRef,
  canSend,
  busy,
  helperText,
  agentName,
  nodeName,
  onChange,
  onKeyDown,
  onSend,
}: ComposerProps) {
  return (
    <footer className="composer-shell">
      <div className="composer-context">
        <span>{agentName}</span>
        <span>运行于 {nodeName}</span>
      </div>
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder="描述你想完成的结果…"
        rows={2}
      />
      <div className="composer-actions">
        <span className={helperText ? "composer-helper" : undefined}>
          {helperText || "Enter 发送 · Shift+Enter 换行"}
        </span>
        <button
          className={canSend ? "send-button ready" : "send-button"}
          disabled={!canSend}
          onClick={onSend}
          aria-label={busy ? "运行中" : "发送"}
          title={busy ? "当前 Agent 正在运行" : "发送"}
        >
          <span>{busy ? "运行中" : "发送"}</span>
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M3.5 10.8 15.6 4.9c.8-.4 1.5.3 1.1 1.1l-5.9 12.1c-.4.9-1.7.8-2-.1L7.2 12.6a1 1 0 0 0-.6-.6L3.6 10.3c-.9-.3-.9-1.6-.1-2Z" />
          </svg>
        </button>
      </div>
    </footer>
  );
}
