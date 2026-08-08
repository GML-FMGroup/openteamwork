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
  it("renders ordinary assistant replies on the transcript canvas instead of a card surface", () => {
    const { container } = render(
      <MessageBubble
        message={buildMessage({
          status: "completed",
          parts: [{ type: "markdown", text: "A plain assistant reply" }],
        })}
      />,
    );

    expect(container.querySelector(".message-bubble.assistant.plain-assistant")).toBeInTheDocument();
  });

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
    expect(container.querySelector(".message-bubble.user")).toBeInTheDocument();
    expect(container.querySelector(".user-message-actions time")).toHaveAttribute(
      "dateTime",
      "2026-04-02T12:00:00.000Z",
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Review the latest run"));
  });

  it("renders a compact semantic activity disclosure while streaming", () => {
    const { container } = render(<MessageBubble message={buildMessage()} />);

    expect(screen.getAllByText("Agent")).toHaveLength(1);
    expect(screen.getByText("Working")).toBeInTheDocument();
    expect(screen.getByText("1 file")).toBeInTheDocument();
    expect(container.querySelector(".activity-disclosure")).toHaveAttribute("open");
    expect(container.querySelector(".activity-phase.running")).toBeInTheDocument();
    const phaseToggle = screen.getByRole("button", { name: /Expand Reading a file/i });
    expect(phaseToggle).toHaveTextContent("Reading a file");
    fireEvent.click(phaseToggle);
    expect(screen.getAllByText("Reading a file")).toHaveLength(2);
  });

  it("replaces the active phase label with its terminal summary when work completes", () => {
    const runningMessage = buildMessage();
    const { container, rerender } = render(
      <MessageBubble
        activityStartedAt="2026-04-02T12:00:00.000Z"
        message={runningMessage}
      />,
    );

    expect(screen.getByRole("button", { name: /Expand Reading a file/i })).toBeInTheDocument();
    expect(container.querySelector(".activity-phase.running")).toBeInTheDocument();

    rerender(
      <MessageBubble
        activityEndedAt="2026-04-02T12:00:05.000Z"
        activityStartedAt="2026-04-02T12:00:00.000Z"
        message={{
          ...runningMessage,
          status: "completed",
          parts: [{
            type: "step_ref",
            stepId: "step-1",
            title: "read_file",
            status: "completed",
            detail: "path: README.md\nline: 12",
          }],
        }}
      />,
    );

    fireEvent.click(screen.getByText("Worked for 5s"));
    expect(screen.getByRole("button", { name: /Expand Worked with files/i })).toBeInTheDocument();
    expect(container.querySelector(".activity-phase.completed")).toBeInTheDocument();
  });

  it("does not invent a model-waiting action while the Run is between ADK events", () => {
    const { container } = render(
      <MessageBubble
        message={buildMessage({
          parts: [
            {
              type: "step_ref",
              stepId: "step-complete",
              title: "list_skills",
              status: "completed",
              detail: "2 capabilities",
            },
          ],
        })}
      />,
    );

    expect(screen.queryByText(/Waiting for the model/i)).not.toBeInTheDocument();
    expect(container.querySelector(".activity-disclosure.awaiting-next-step")).toBeInTheDocument();
    expect(container.querySelector(".activity-waiting-line")).not.toBeInTheDocument();
  });

  it("uses the authoritative outer Run status instead of an intermediate action failure", () => {
    const { container } = render(
      <MessageBubble
        message={buildMessage({
          status: "completed",
          parts: [{
            type: "step_ref",
            stepId: "step-failed",
            title: "exec",
            status: "failed",
            detail: "Command blocked by security policy",
          }],
        })}
      />,
    );

    expect(screen.getByText("Work completed")).toBeInTheDocument();
    expect(container.querySelector(".activity-disclosure.completed")).toBeInTheDocument();
    expect(container.querySelector(".activity-disclosure-marker.completed")).toBeInTheDocument();
    expect(container.querySelector(".activity-disclosure-marker.failed")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Work completed"));
    expect(container.querySelector(".activity-phase.failed")).toBeInTheDocument();
  });

  it("keeps disclosure chevrons immediately after their summary copy", () => {
    const { container } = render(<MessageBubble message={buildMessage()} />);

    const outerCopy = container.querySelector(".activity-disclosure-copy");
    expect(outerCopy?.querySelector(":scope > .activity-disclosure-chevron")).toBeInTheDocument();

    const phaseCopy = container.querySelector(".activity-phase-copy");
    expect(phaseCopy?.querySelector(":scope > .activity-phase-chevron")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Expand Reading a file/i }));
    const actionCopy = container.querySelector(".activity-semantic-copy");
    expect(actionCopy?.querySelector(":scope > .activity-action-chevron")).toBeInTheDocument();
  });

  it("keeps action metadata collapsed until the user opens an individual action", () => {
    const { container } = render(<MessageBubble message={buildMessage()} />);

    fireEvent.click(screen.getByRole("button", { name: /Expand Reading a file/i }));
    const action = container.querySelector(".activity-action > summary");
    expect(action).toHaveAttribute("aria-label", "Expand Reading a file");
    expect(action).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Status")).not.toBeInTheDocument();

    fireEvent.click(action!);
    expect(action).toHaveAttribute("aria-label", "Collapse Reading a file");
    expect(action).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("File")).toBeInTheDocument();
    expect(screen.getAllByText("README.md")).toHaveLength(2);
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("can hide repeated assistant identity for continued replies", () => {
    render(<MessageBubble message={buildMessage()} showIdentity={false} />);

    expect(screen.queryByText("Agent")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Expand Reading a file/i })).toBeInTheDocument();
  });

  it("keeps tool results inside technical details and renders attachment cards", () => {
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

    fireEvent.click(screen.getByText("Work completed"));
    fireEvent.click(screen.getByRole("button", { name: /Expand Used connected tools/i }));
    expect(screen.getByText("Used Inspect repo")).toBeInTheDocument();
    expect(screen.queryByText("inspect_repo")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Used Inspect repo"));
    expect(screen.getByText("Technical details")).toBeInTheDocument();
    expect(screen.getByText("client_session_notes.md")).toBeInTheDocument();
    expect(screen.getByText("Open original")).toBeInTheDocument();
  });

  it("renders semantic completed status for finished steps", () => {
    const { container } = render(
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

    fireEvent.click(screen.getByText("Work completed"));
    expect(container.querySelector(".activity-phase.completed")).toBeInTheDocument();
    expect(container.querySelector(".activity-phase.running")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Expand Ran commands/i }));
    expect(screen.getAllByText("Ran a local command")).toHaveLength(1);
    expect(screen.queryByText("exec")).not.toBeInTheDocument();
  });

  it("renders commentary, tool activity, and the final answer in chronological order", () => {
    const { container } = render(
      <MessageBubble
        message={buildMessage({
          status: "completed",
          parts: [
            { type: "commentary", text: "I will inspect the repository first." },
            {
              type: "step_ref",
              stepId: "step-ordered",
              title: "inspect_repo",
              status: "completed",
              detail: '{"path":"."}',
            },
            { type: "commentary", text: "The structure is clear; I will verify the tests next." },
            {
              type: "step_ref",
              stepId: "step-tests",
              title: "exec",
              status: "completed",
              detail: '{"command":"pytest"}',
            },
            { type: "markdown", text: "The repository is healthy." },
          ],
        })}
      />,
    );

    expect(container.querySelectorAll(".activity-disclosure")).toHaveLength(1);
    expect(screen.getAllByText("Work completed")).toHaveLength(1);
    fireEvent.click(screen.getByText("Work completed"));
    fireEvent.click(screen.getByRole("button", { name: /Expand Used connected tools/i }));
    fireEvent.click(screen.getByRole("button", { name: /Expand Ran commands/i }));
    const text = container.textContent ?? "";
    expect(text.indexOf("I will inspect the repository first.")).toBeLessThan(text.indexOf("Used Inspect repo"));
    expect(text.indexOf("Used Inspect repo")).toBeLessThan(text.indexOf("The structure is clear"));
    expect(text.indexOf("The structure is clear")).toBeLessThan(text.indexOf("Ran a local command"));
    expect(text.indexOf("Ran a local command")).toBeLessThan(text.indexOf("The repository is healthy."));
    expect(container.querySelectorAll(".run-commentary")).toHaveLength(2);
    expect(screen.getAllByText("Technical details")).toHaveLength(1);
  });

  it("supports independent Run and execution-phase disclosure layers", () => {
    const { container } = render(
      <MessageBubble
        message={buildMessage({
          status: "completed",
          parts: [
            { type: "commentary", text: "I will inspect the repository first." },
            {
              type: "step_ref",
              stepId: "phase-read",
              title: "read_file",
              status: "completed",
              detail: '{"path":"README.md"}',
            },
            {
              type: "step_ref",
              stepId: "phase-exec",
              title: "exec",
              status: "completed",
              detail: '{"command":"pnpm test"}',
            },
            { type: "markdown", text: "Verification complete." },
          ],
        })}
      />,
    );

    fireEvent.click(screen.getByText("Work completed"));
    expect(container.querySelectorAll(".activity-disclosure")).toHaveLength(1);
    expect(container.querySelectorAll(".activity-phase")).toHaveLength(1);
    expect(screen.queryByText("Read a file")).not.toBeInTheDocument();
    expect(screen.queryByText("Ran a local command")).not.toBeInTheDocument();
    const phaseToggle = screen.getByRole("button", { name: /Expand Worked with files, Ran commands/i });
    expect(phaseToggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(phaseToggle);
    expect(screen.getByText("Read a file")).toBeInTheDocument();
    expect(screen.getByText("Ran a local command")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Collapse Worked with files, Ran commands/i })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByText("Verification complete.")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Work completed"));
    expect(screen.queryByText("I will inspect the repository first.")).not.toBeInTheDocument();
    expect(screen.getByText("Verification complete.")).toBeInTheDocument();
  });

  it("keeps commentary visible without progress chrome when no tool runs", () => {
    const { container } = render(
      <MessageBubble
        message={buildMessage({
          status: "completed",
          parts: [
            { type: "commentary", text: "I can answer this directly." },
            { type: "markdown", text: "Here is the answer." },
          ],
        })}
      />,
    );

    expect(screen.getByText("I can answer this directly.")).toBeInTheDocument();
    expect(screen.getByText("Here is the answer.")).toBeInTheDocument();
    expect(container.querySelector(".activity-disclosure")).not.toBeInTheDocument();
  });

  it("keeps exec detail behind technical disclosure", () => {
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

    expect(container.textContent).not.toContain('"command": "ls -la"');
    fireEvent.click(screen.getByText("Work completed"));
    fireEvent.click(screen.getByText("Technical details"));
    expect(container.textContent).toContain('"command": "ls -la"');
    expect(container.textContent).toContain('"cwd": "/workspace"');
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

  it("collapses a live timeline and records observed elapsed time when work completes", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-02T12:00:00.000Z"));
    const liveMessage = buildMessage({ createdAt: "2026-04-02T12:00:00.000Z" });
    const { container, rerender } = render(<MessageBubble message={liveMessage} />);

    expect(container.querySelector(".activity-disclosure")).toHaveAttribute("open");
    vi.setSystemTime(new Date("2026-04-02T12:00:03.000Z"));
    rerender(<MessageBubble message={{ ...liveMessage, status: "completed" }} />);

    expect(screen.getByText("Worked for 3s")).toBeInTheDocument();
    expect(container.querySelector(".activity-disclosure")).not.toHaveAttribute("open");
    vi.useRealTimers();
  });
});
