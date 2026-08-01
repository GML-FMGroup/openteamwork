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
  "Analyze this repository and propose the next implementation steps",
  "Review the latest run and identify risks",
  "Prepare a project brief that is ready to share",
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
            <h3>{agentName} is ready</h3>
            <p>Describe the outcome you want. OpenPPX keeps the process, tool results, and artifacts available for review.</p>
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
          Jump to latest <span>↓</span>
        </button>
      ) : null}
    </div>
  );
}
