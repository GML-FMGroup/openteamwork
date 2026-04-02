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
    expect(screen.getByText("查看原始结果")).toBeInTheDocument();
    expect(screen.getByText("client_session_notes.md")).toBeInTheDocument();
    expect(screen.getByText("打开原图")).toBeInTheDocument();
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

  it("renders explicit failed and cancelled status banners", () => {
    const { rerender } = render(
      <MessageBubble
        message={buildMessage({
          status: "failed",
          parts: [{ type: "error", text: "Provider timeout", errorCode: "RUN_FAILED" }],
        })}
      />,
    );

    expect(screen.getByText("本次运行失败")).toBeInTheDocument();
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

    expect(screen.getByText("本次运行已取消")).toBeInTheDocument();
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

    expect(screen.getByText("模型提供方配置异常")).toBeInTheDocument();
    expect(screen.getByText("通常是 provider 名称、模型名，或对应的密钥配置不匹配。")).toBeInTheDocument();
  });
});
