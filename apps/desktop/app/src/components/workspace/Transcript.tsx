import type { RefObject } from "react";
import type { ChatMessage } from "../../types";
import { projectActivityGroups, type ActivityGroup } from "../../lib/activity-presentation";
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

/** Group consecutive Agent messages into one progressively disclosed work summary. */
function projectTurnActivity(messages: ChatMessage[]): Map<number, ActivityGroup[]> {
  const activityByMessageIndex = new Map<number, ActivityGroup[]>();
  let index = 0;

  while (index < messages.length) {
    if (messages[index]?.role !== "assistant") {
      index += 1;
      continue;
    }

    const turnStart = index;
    const turnMessages: ChatMessage[] = [];
    while (index < messages.length && messages[index]?.role === "assistant") {
      turnMessages.push(messages[index]!);
      index += 1;
    }
    activityByMessageIndex.set(turnStart, projectActivityGroups(turnMessages));
    for (let hiddenIndex = turnStart + 1; hiddenIndex < index; hiddenIndex += 1) {
      activityByMessageIndex.set(hiddenIndex, []);
    }
  }

  return activityByMessageIndex;
}

/** Keep meaningful messages while omitting completed tool-only shells already represented by the turn summary. */
function shouldRenderMessage(message: ChatMessage, activityGroups: ActivityGroup[] | undefined): boolean {
  if (message.role !== "assistant" || message.status !== "completed") {
    return true;
  }
  if (activityGroups?.length) {
    return true;
  }
  return message.parts.some((part) => part.type !== "step_ref" && part.type !== "tool_result");
}

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
  const turnActivity = projectTurnActivity(messages);
  return (
    <div className="transcript-wrap">
      <section ref={streamRef} className="message-stream" onScroll={onScroll}>
        {messages.length === 0 ? (
          <div className="empty-state operator-empty">
            <span className="empty-kicker">START A TASK</span>
            <h3>{agentName} is ready</h3>
            <p>Describe the outcome. Progress and artifacts stay visible while the Agent works.</p>
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
              const activityGroups = turnActivity.get(index);
              if (!shouldRenderMessage(message, activityGroups)) {
                return null;
              }
              return (
                <MessageBubble
                  key={message.id}
                  message={message}
                  showIdentity={showIdentity}
                  activityGroups={activityGroups}
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
