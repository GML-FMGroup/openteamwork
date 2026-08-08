import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { Transcript } from "../app/src/components/workspace/Transcript";
import type { ChatMessage } from "../app/src/types";

function assistantMessage(id: string, parts: ChatMessage["parts"], runId?: string): ChatMessage {
  return {
    id,
    sessionId: "session-1",
    role: "assistant",
    runId,
    parts,
    status: "completed",
    createdAt: "2026-08-07T00:00:00Z",
  };
}

function renderTranscript(messages: ChatMessage[]) {
  return render(
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
}

describe("Transcript activity turns", () => {
  it("coalesces one assistant turn while preserving commentary and tool order", () => {
    const messages = [
      assistantMessage("commentary-message", [
        { type: "commentary", text: "I will inspect the repository first." },
        {
          type: "step_ref",
          stepId: "call-ordered",
          title: "inspect_repo",
          status: "completed",
          detail: '{"path":"."}',
        },
      ]),
      assistantMessage("final-message", [{ type: "markdown", text: "Inspection complete." }]),
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

    expect(container.querySelectorAll(".message-bubble.assistant")).toHaveLength(1);
    expect(container.querySelectorAll(".activity-disclosure")).toHaveLength(1);
    fireEvent.click(screen.getByText("Work completed"));
    expect(screen.queryByText("Used Inspect repo")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Expand Used connected tools/i }));
    const text = container.textContent ?? "";
    expect(text.indexOf("I will inspect the repository first.")).toBeLessThan(text.indexOf("Used Inspect repo"));
    expect(text.indexOf("Used Inspect repo")).toBeLessThan(text.indexOf("Inspection complete."));
  });

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
      {
        ...assistantMessage("answer-message", [{ type: "markdown", text: "Research complete." }]),
        createdAt: "2026-08-07T00:00:03Z",
      },
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
    expect(screen.getByText("Worked for 3s")).toBeInTheDocument();
    expect(screen.getByText("1 search")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Worked for 3s"));
    expect(screen.queryByText("Searched the web")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Expand Researched the web/i }));
    expect(screen.getByText("Searched the web")).toBeInTheDocument();
    expect(screen.getByText("Research complete.")).toBeInTheDocument();
    expect(screen.getAllByText("Agent")).toHaveLength(1);
    expect(container.textContent).not.toContain("one source");
    expect(container.querySelectorAll(".message-bubble")).toHaveLength(1);
    expect(
      Array.from(container.querySelectorAll<HTMLElement>(".message-bubble")).every(
        (bubble) => (bubble.textContent ?? "").trim().length > 0,
      ),
    ).toBe(true);
  });

  it("coalesces assistant and tool events with one stable Run identity", () => {
    const messages: ChatMessage[] = [
      assistantMessage("commentary", [
        { type: "commentary", text: "I will inspect the repository first." },
        {
          type: "step_ref",
          stepId: "call-run",
          title: "inspect_repo",
          status: "completed",
          detail: '{"path":"."}',
        },
      ], "run-1"),
      {
        ...assistantMessage("tool-result", [{
          type: "tool_result",
          toolCallId: "call-run",
          toolName: "inspect_repo",
          summary: "inspect_repo returned 1 field.",
          rawText: '{"ok":true}',
        }], "run-1"),
        role: "tool",
        createdAt: "2026-08-07T00:00:02Z",
      },
      {
        ...assistantMessage("final", [{ type: "markdown", text: "Inspection complete." }], "run-1"),
        createdAt: "2026-08-07T00:00:04Z",
      },
    ];

    const { container } = renderTranscript(messages);

    expect(container.querySelectorAll(".message-bubble.assistant")).toHaveLength(1);
    expect(container.querySelectorAll(".activity-disclosure")).toHaveLength(1);
    expect(screen.getByText("Worked for 4s")).toBeInTheDocument();
  });

  it("keeps separate Runs separate and treats a user message as a hard boundary", () => {
    const messages: ChatMessage[] = [
      assistantMessage("run-one", [{
        type: "step_ref",
        stepId: "one",
        title: "web_search",
        status: "completed",
        detail: '{"query":"one"}',
      }], "run-1"),
      {
        ...assistantMessage("user", [{ type: "markdown", text: "Continue" }], "run-1"),
        role: "user",
      },
      assistantMessage("run-two", [{
        type: "step_ref",
        stepId: "two",
        title: "web_search",
        status: "completed",
        detail: '{"query":"two"}',
      }], "run-2"),
    ];

    const { container } = renderTranscript(messages);

    expect(container.querySelectorAll(".activity-disclosure")).toHaveLength(2);
    expect(container.querySelectorAll(".message-bubble.user")).toHaveLength(1);
  });

  it("shows an ordered live timeline and collapses only after completion", () => {
    const messages = [
      {
        ...assistantMessage("search-message", [{
          type: "step_ref" as const,
          stepId: "search-1",
          title: "web_search",
          status: "completed" as const,
          detail: '{"query":"OpenPPX"}',
        }]),
        status: "completed" as const,
      },
      {
        ...assistantMessage("read-message", [{
          type: "step_ref" as const,
          stepId: "read-1",
          title: "web_fetch",
          status: "running" as const,
          detail: '{"url":"https://example.com"}',
        }]),
        status: "streaming" as const,
        createdAt: "2026-08-07T00:00:03Z",
      },
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

    expect(container.querySelector(".activity-disclosure")).toHaveAttribute("open");
    expect(container.querySelectorAll(".activity-semantic-list .activity-semantic-copy > strong")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /Expand Researched the web/i }));
    const rows = Array.from(container.querySelectorAll(
      ".activity-semantic-list .activity-semantic-copy > strong",
    ))
      .map((row) => row.textContent);
    expect(rows).toEqual(["Searched the web", "Reading sources"]);
    expect(screen.getByText("OpenPPX")).toBeInTheDocument();
    expect(screen.getByText("example.com")).toBeInTheDocument();
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
