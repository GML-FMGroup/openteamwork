import { render, screen } from "@testing-library/react";
import { MessageBubble } from "../app/src/components/MessageBubble";
import type { ChatMessage } from "../app/src/types";

function buildMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "message-1",
    sessionId: "session-1",
    role: "assistant",
    status: "streaming",
    createdAt: "2026-04-02T12:00:00.000Z",
    parts: [
      {
        type: "step_ref",
        stepId: "step-1",
        title: "read_file",
        status: "running",
        detail: "path: README.md\nline: 12",
      },
    ],
    ...overrides,
  };
}

describe("MessageBubble", () => {
  it("renders localized role, step status, and streaming hint", () => {
    render(<MessageBubble message={buildMessage()} />);

    expect(screen.getByText("Agent")).toBeInTheDocument();
    expect(screen.getByText("执行中")).toBeInTheDocument();
    expect(screen.getByText("Agent 正在整理结果...")).toBeInTheDocument();
    expect(screen.getByText("path: README.md")).toBeInTheDocument();
    expect(screen.getByText("line: 12")).toBeInTheDocument();
  });

  it("renders completed status for finished steps", () => {
    render(
      <MessageBubble
        message={buildMessage({
          status: "completed",
          parts: [
            {
              type: "step_ref",
              stepId: "step-2",
              title: "exec",
              status: "completed",
              detail: "command: ls",
            },
          ],
        })}
      />,
    );

    expect(screen.getByText("完成")).toBeInTheDocument();
    expect(screen.queryByText("Agent 正在整理结果...")).not.toBeInTheDocument();
  });
});
