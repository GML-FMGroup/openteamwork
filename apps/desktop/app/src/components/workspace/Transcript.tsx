import type { RefObject } from "react";
import type { ChatMessage } from "../../types";
import { MessageBubble } from "../MessageBubble";

interface TranscriptProps {
  messages: ChatMessage[];
  activeRunId?: string | null;
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

interface TranscriptRow {
  message: ChatMessage;
  startedAt: string;
  endedAt?: string;
}

function mergedAssistantStatus(messages: ChatMessage[]): ChatMessage["status"] {
  if (messages.some((message) => message.status === "streaming")) {
    return "streaming";
  }
  return [...messages]
    .reverse()
    .find((message) => message.role === "assistant")?.status ?? "completed";
}

function normalizedRunId(message: ChatMessage): string {
  return message.runId?.trim() ?? "";
}

function mergedRunRow(messages: ChatMessage[], activeRunId?: string | null): TranscriptRow {
  const first = messages[0]!;
  const representative = messages.find((message) => message.role === "assistant") ?? first;
  const startedAt = first.createdAt;
  const latestCreatedAt = messages.at(-1)?.createdAt;
  const runId = normalizedRunId(first) || normalizedRunId(representative) || null;
  const status = runId && runId === activeRunId
    ? "streaming"
    : mergedAssistantStatus(messages);
  return {
    message: {
      ...representative,
      runId,
      role: "assistant",
      status,
      parts: messages.flatMap((message) => message.parts),
    },
    startedAt,
    endedAt: status !== "streaming" && latestCreatedAt && Date.parse(latestCreatedAt) > Date.parse(startedAt)
      ? latestCreatedAt
      : undefined,
  };
}

/** Coalesce one Client Run or replayed ADK Invocation while preserving event order. */
export function projectTranscriptRows(
  messages: ChatMessage[],
  activeRunId?: string | null,
): TranscriptRow[] {
  const rows: TranscriptRow[] = [];
  let index = 0;

  while (index < messages.length) {
    const current = messages[index];
    if (!current) {
      index += 1;
      continue;
    }
    if (current.role === "user") {
      rows.push({ message: current, startedAt: current.createdAt });
      index += 1;
      continue;
    }

    const runId = normalizedRunId(current);
    if (runId) {
      const runMessages: ChatMessage[] = [];
      while (
        index < messages.length
        && messages[index]?.role !== "user"
        && normalizedRunId(messages[index]!) === runId
      ) {
        runMessages.push(messages[index]!);
        index += 1;
      }
      rows.push(mergedRunRow(runMessages, activeRunId));
      continue;
    }

    if (current.role !== "assistant") {
      rows.push({ message: current, startedAt: current.createdAt });
      index += 1;
      continue;
    }

    const turnMessages: ChatMessage[] = [];
    while (
      index < messages.length
      && messages[index]?.role === "assistant"
      && !normalizedRunId(messages[index]!)
    ) {
      turnMessages.push(messages[index]!);
      index += 1;
    }
    rows.push(mergedRunRow(turnMessages, activeRunId));
  }

  return rows;
}

/** Central task transcript with a stable empty state and jump-to-latest affordance. */
export function Transcript({
  messages,
  activeRunId,
  agentName,
  streamRef,
  showJumpToLatest,
  onScroll,
  onJumpToLatest,
  onUseSuggestion,
}: TranscriptProps) {
  const rows = projectTranscriptRows(messages, activeRunId);
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
            {rows.map((row, index) => {
              const message = row.message;
              const previousMessage = index > 0 ? rows[index - 1]?.message : null;
              const showIdentity = !(message.role === "assistant" && previousMessage?.role === "assistant");
              return (
                <MessageBubble
                  key={message.id}
                  message={message}
                  agentName={agentName}
                  showIdentity={showIdentity}
                  activityStreaming={message.status === "streaming"}
                  activityStartedAt={row.startedAt}
                  activityEndedAt={row.endedAt}
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
