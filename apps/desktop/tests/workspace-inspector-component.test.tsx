import { fireEvent, render, screen, within } from "@testing-library/react";
import { WorkspaceInspector } from "../app/src/components/workspace/WorkspaceInspector";
import type { ChatMessage } from "../app/src/types";

const messages: ChatMessage[] = [
  {
    id: "assistant-1",
    sessionId: "session-1",
    role: "assistant",
    status: "completed",
    createdAt: "2026-08-01T00:00:00Z",
    parts: [
      {
        type: "step_ref",
        stepId: "inspect",
        title: "Inspect repository",
        status: "completed",
        detail: "Repository mapped",
      },
      {
        type: "file",
        text: "Implementation notes",
        fileName: "notes.md",
        sizeBytes: 2048,
        mimeType: "text/markdown",
      },
    ],
  },
];

describe("WorkspaceInspector", () => {
  it("renders Progress and Artifacts as independent disclosure sections", () => {
    render(
      <WorkspaceInspector
        sessionId="session-1"
        messages={messages}
        running={false}
        collapsed={false}
      />,
    );

    const taskPanel = screen.getByLabelText("Task panel");
    expect(within(taskPanel).queryByRole("tab")).not.toBeInTheDocument();
    expect(within(taskPanel).getByText("Inspect repository")).toBeInTheDocument();
    expect(within(taskPanel).getByText("notes.md")).toBeInTheDocument();

    const progressToggle = within(taskPanel).getByRole("button", { name: "Progress" });
    fireEvent.click(progressToggle);

    expect(progressToggle).toHaveAttribute("aria-expanded", "false");
    expect(within(taskPanel).queryByText("Inspect repository")).not.toBeInTheDocument();
    expect(within(taskPanel).getByText("notes.md")).toBeInTheDocument();
  });

  it("removes the panel completely when collapsed", () => {
    const { rerender } = render(
      <WorkspaceInspector
        sessionId="session-1"
        messages={messages}
        running={false}
        collapsed={false}
      />,
    );

    rerender(
      <WorkspaceInspector
        sessionId="session-1"
        messages={messages}
        running={false}
        collapsed
      />,
    );

    expect(screen.queryByLabelText("Task panel")).not.toBeInTheDocument();
    expect(document.querySelector(".workspace-inspector.collapsed")).toBeNull();
  });
});
