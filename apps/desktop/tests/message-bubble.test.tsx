import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
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
  it("keeps user identity metadata out of the bubble and exposes copy/time actions", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    const { container } = render(
      <MessageBubble
        message={buildMessage({
          role: "user",
          status: "completed",
          parts: [{ type: "markdown", text: "Review the latest run" }],
        })}
      />,
    );

    expect(screen.queryByText("You")).not.toBeInTheDocument();
    expect(container.querySelector(".message-meta")).not.toBeInTheDocument();
    expect(container.querySelector(".user-message-actions")).toBeInTheDocument();
    expect(container.querySelector(".user-message-actions time")).toHaveAttribute(
      "dateTime",
      "2026-04-02T12:00:00.000Z",
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Review the latest run"));
  });

  it("renders localized role, step status, and streaming hint", () => {
    const { container } = render(<MessageBubble message={buildMessage()} />);

    expect(screen.getAllByText("Agent")).toHaveLength(1);
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Preparing the result...")).toBeInTheDocument();
    const detailBlock = container.querySelector(".step-card-detail");
    expect(detailBlock?.textContent).toContain("path: README.md");
    expect(detailBlock?.textContent).toContain("line: 12");
  });

  it("can hide repeated assistant identity for continued replies", () => {
    render(<MessageBubble message={buildMessage()} showIdentity={false} />);

    expect(screen.queryByText("Agent")).not.toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("renders tool result and attachment cards", () => {
    render(
      <MessageBubble
        message={buildMessage({
          status: "completed",
          parts: [
            {
              type: "tool_result",
              toolName: "inspect_repo",
              summary: "inspect_repo 返回了 2 个字段。",
              detail: "2 files changed",
              rawText: "{\n  \"ok\": true\n}",
            },
            {
              type: "file",
              text: "Planned artifact",
              fileName: "client_session_notes.md",
              mimeType: "text/markdown",
              sizeBytes: 2048,
            },
            {
              type: "image",
              text: "Runtime architecture preview",
              url: "https://example.com/runtime.png",
              mimeType: "image/png",
            },
          ],
        })}
      />,
    );

    expect(screen.getByText("inspect_repo")).toBeInTheDocument();
    expect(screen.getByText("View raw result")).toBeInTheDocument();
    expect(screen.getByText("client_session_notes.md")).toBeInTheDocument();
    expect(screen.getByText("Open original")).toBeInTheDocument();
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

    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.queryByText("Preparing the result...")).not.toBeInTheDocument();
  });

  it("renders exec detail as one continuous block", () => {
    const { container } = render(
      <MessageBubble
        message={buildMessage({
          status: "completed",
          parts: [
            {
              type: "step_ref",
              stepId: "step-4",
              title: "exec",
              status: "completed",
              detail: '{\n  "command": "ls -la",\n  "cwd": "/workspace"\n}',
            },
          ],
        })}
      />,
    );

    const detailBlocks = container.querySelectorAll(".step-card-detail");
    expect(detailBlocks).toHaveLength(1);
    expect(detailBlocks[0]?.textContent).toContain('"command": "ls -la"');
    expect(detailBlocks[0]?.textContent).toContain('"cwd": "/workspace"');
  });

  it("renders explicit failed and cancelled status banners", () => {
    const { rerender } = render(
      <MessageBubble
        message={buildMessage({
          status: "failed",
          parts: [{ type: "error", text: "Provider timeout", errorCode: "RUN_FAILED" }],
        })}
      />,
    );

    expect(screen.getByText("This run failed")).toBeInTheDocument();
    expect(screen.getByText("Provider timeout")).toBeInTheDocument();

    rerender(
      <MessageBubble
        message={buildMessage({
          status: "cancelled",
          parts: [
            {
              type: "step_ref",
              stepId: "step-3",
              title: "exec",
              status: "failed",
              detail: "cancelled by user",
            },
          ],
        })}
      />,
    );

    expect(screen.getByText("This run was cancelled")).toBeInTheDocument();
  });

  it("renders readable provider error guidance", () => {
    render(
      <MessageBubble
        message={buildMessage({
          status: "failed",
          parts: [
            {
              type: "error",
              text: "Provider List: https://docs.litellm.ai/docs/providers",
              errorCode: "RUN_FAILED",
            },
          ],
        })}
      />,
    );

    expect(screen.getByText("Model provider configuration error")).toBeInTheDocument();
    expect(screen.getByText("The provider name, model name, or corresponding credential may not match.")).toBeInTheDocument();
  });
});
