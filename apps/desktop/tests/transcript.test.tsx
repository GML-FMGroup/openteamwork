import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import { Transcript } from "../app/src/components/workspace/Transcript";
import type { ChatMessage } from "../app/src/types";

function assistantMessage(id: string, parts: ChatMessage["parts"]): ChatMessage {
  return {
    id,
    sessionId: "session-1",
    role: "assistant",
    parts,
    status: "completed",
    createdAt: "2026-08-07T00:00:00Z",
  };
}

describe("Transcript activity turns", () => {
  it("coalesces Tool calls and responses across consecutive Agent messages", () => {
    const messages = [
      assistantMessage("call-message", [{
        type: "step_ref",
        stepId: "call-1",
        title: "web_search",
        status: "completed",
        detail: '{"query":"OpenPPX"}',
      }]),
      assistantMessage("response-message", [{
        type: "tool_result",
        toolCallId: "call-1",
        toolName: "web_search",
        summary: "web_search returned 1 field.",
        rawText: '{"result":"one source"}',
      }]),
      assistantMessage("answer-message", [{ type: "markdown", text: "Research complete." }]),
    ];

    const { container } = render(
      <Transcript
        messages={messages}
        agentName="Main"
        streamRef={createRef<HTMLElement>()}
        showJumpToLatest={false}
        onScroll={() => undefined}
        onJumpToLatest={() => undefined}
        onUseSuggestion={() => undefined}
      />,
    );

    expect(container.querySelectorAll(".activity-disclosure")).toHaveLength(1);
    expect(screen.getByText("Searched the web")).toBeInTheDocument();
    expect(screen.getByText("1 search")).toBeInTheDocument();
    expect(screen.getByText("Research complete.")).toBeInTheDocument();
    expect(screen.getAllByText("Agent")).toHaveLength(1);
    expect(container.textContent).not.toContain("one source");
    expect(container.querySelectorAll(".message-bubble")).toHaveLength(2);
    expect(
      Array.from(container.querySelectorAll<HTMLElement>(".message-bubble")).every(
        (bubble) => (bubble.textContent ?? "").trim().length > 0,
      ),
    ).toBe(true);
  });

  it("preserves an empty terminal Agent message so its failure state remains visible", () => {
    const failedMessage = {
      ...assistantMessage("failed-message", []),
      status: "failed" as const,
    };

    const { container } = render(
      <Transcript
        messages={[failedMessage]}
        agentName="Main"
        streamRef={createRef<HTMLElement>()}
        showJumpToLatest={false}
        onScroll={() => undefined}
        onJumpToLatest={() => undefined}
        onUseSuggestion={() => undefined}
      />,
    );

    expect(screen.getByText("This run failed")).toBeInTheDocument();
    expect(container.querySelectorAll(".message-bubble")).toHaveLength(1);
  });
});
