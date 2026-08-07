import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
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
  it("renders Artifacts without duplicating conversation progress", () => {
    render(
      <WorkspaceInspector
        sessionId="session-1"
        messages={messages}
        collapsed={false}
      />,
    );

    const taskPanel = screen.getByLabelText("Task panel");
    expect(within(taskPanel).queryByRole("button", { name: "Progress" })).not.toBeInTheDocument();
    expect(within(taskPanel).queryByText("Activity details")).not.toBeInTheDocument();
    expect(within(taskPanel).queryByText("Used Inspect repository")).not.toBeInTheDocument();
    expect(within(taskPanel).getByText("notes.md")).toBeInTheDocument();

    const artifactsToggle = within(taskPanel).getByRole("button", { name: "Artifacts (1)" });
    fireEvent.click(artifactsToggle);

    expect(artifactsToggle).toHaveAttribute("aria-expanded", "false");
    expect(within(taskPanel).queryByText("notes.md")).not.toBeInTheDocument();
  });

  it("removes the panel completely when collapsed", () => {
    const { rerender } = render(
      <WorkspaceInspector
        sessionId="session-1"
        messages={messages}
        collapsed={false}
      />,
    );

    rerender(
      <WorkspaceInspector
        sessionId="session-1"
        messages={messages}
        collapsed
      />,
    );

    expect(screen.queryByLabelText("Task panel")).not.toBeInTheDocument();
    expect(document.querySelector(".workspace-inspector.collapsed")).toBeNull();
  });

  it("loads a durable Session artifact only after the user opens it", async () => {
    const onLoadArtifact = vi.fn(async () => "data:image/png;base64,aW1hZ2U=");
    render(
      <WorkspaceInspector
        sessionId="session-1"
        messages={[]}
        collapsed={false}
        artifacts={[{
          id: "artifact-1",
          key: "uploads/artifact-1/diagram.png",
          fileName: "diagram.png",
          mimeType: "image/png",
          sizeBytes: 5,
          version: 0,
          source: "agent_output",
          createdAt: "2026-08-04T00:00:00Z",
        }]}
        onLoadArtifact={onLoadArtifact}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /diagram\.png/i }));

    await waitFor(() => expect(onLoadArtifact).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("img", { name: "diagram.png" })).toHaveAttribute(
      "src",
      "data:image/png;base64,aW1hZ2U=",
    );
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute("download", "diagram.png");
  });

  it("previews text and audio artifacts without executing their content", async () => {
    const textData = btoa("# Result\n\nFinished safely.");
    const onLoadArtifact = vi.fn(async (artifact: { mimeType: string }) =>
      artifact.mimeType === "text/markdown"
        ? `data:text/markdown;base64,${textData}`
        : "data:audio/mpeg;base64,YXVkaW8=",
    );
    const artifacts = [
      {
        id: "artifact-text",
        key: "outputs/result.md",
        fileName: "result.md",
        mimeType: "text/markdown",
        sizeBytes: 26,
        version: 0,
        source: "agent_output" as const,
        createdAt: "2026-08-04T00:00:00Z",
      },
      {
        id: "artifact-audio",
        key: "outputs/briefing.mp3",
        fileName: "briefing.mp3",
        mimeType: "audio/mpeg",
        sizeBytes: 5,
        version: 0,
        source: "agent_output" as const,
        createdAt: "2026-08-04T00:00:01Z",
      },
    ];
    const { rerender } = render(
      <WorkspaceInspector
        sessionId="session-1"
        messages={[]}
        collapsed={false}
        artifacts={artifacts}
        onLoadArtifact={onLoadArtifact}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /result\.md/i }));
    expect(await screen.findByText(/Finished safely/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Back to artifacts/i }));
    fireEvent.click(screen.getByRole("button", { name: /briefing\.mp3/i }));
    expect(await screen.findByLabelText("Preview briefing.mp3")).toHaveAttribute(
      "src",
      "data:audio/mpeg;base64,YXVkaW8=",
    );

    rerender(
      <WorkspaceInspector
        sessionId="session-2"
        messages={[]}
        collapsed={false}
        artifacts={artifacts}
        onLoadArtifact={onLoadArtifact}
      />,
    );
    expect(screen.queryByLabelText("Preview briefing.mp3")).not.toBeInTheDocument();
  });
});
