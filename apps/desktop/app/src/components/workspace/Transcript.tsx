import type { RefObject } from "react";
import type { ChatMessage } from "../../types";
import { MessageBubble } from "../MessageBubble";

interface TranscriptProps {
  messages: ChatMessage[];
  agentName: string;
  streamRef: RefObject<HTMLElement | null>;
  showJumpToLatest: boolean;
  onScroll: () => void;
  onJumpToLatest: () => void;
  onUseSuggestion: (value: string) => void;
}

const suggestions = [
  "分析当前仓库，给出下一步实施计划",
  "检查最近一次任务的执行过程和风险",
  "整理一份可以直接交付的项目说明",
];

/** Central task transcript with a stable empty state and jump-to-latest affordance. */
export function Transcript({
  messages,
  agentName,
  streamRef,
  showJumpToLatest,
  onScroll,
  onJumpToLatest,
  onUseSuggestion,
}: TranscriptProps) {
  return (
    <div className="transcript-wrap">
      <section ref={streamRef} className="message-stream" onScroll={onScroll}>
        {messages.length === 0 ? (
          <div className="empty-state operator-empty">
            <span className="empty-kicker">READY WHEN YOU ARE</span>
            <h3>{agentName} 已就位</h3>
            <p>描述你想完成的结果。OpenPPX 会保留过程、工具结果和产物，方便你随时检查。</p>
            <div className="suggestion-grid">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  className="suggestion-card"
                  onClick={() => onUseSuggestion(suggestion)}
                >
                  {suggestion}
                  <span>↗</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="transcript-column">
            {messages.map((message, index) => {
              const previousMessage = index > 0 ? messages[index - 1] : null;
              const showIdentity = !(message.role === "assistant" && previousMessage?.role === "assistant");
              return (
                <MessageBubble
                  key={message.id}
                  message={message}
                  showIdentity={showIdentity}
                />
              );
            })}
          </div>
        )}
      </section>
      {showJumpToLatest ? (
        <button className="jump-latest" onClick={onJumpToLatest}>
          跳到最新 <span>↓</span>
        </button>
      ) : null}
    </div>
  );
}
