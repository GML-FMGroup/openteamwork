import {
  projectActivityItems,
  projectArtifactItems,
} from "../app/src/lib/workspace-inspector";
import type { ChatMessage } from "../app/src/types";

const messages: ChatMessage[] = [
  {
    id: "assistant-1",
    sessionId: "session-1",
    role: "assistant",
    status: "streaming",
    createdAt: "2026-07-31T00:00:00Z",
    parts: [
      { type: "step_ref", stepId: "inspect", title: "Inspect repository", status: "running", detail: "Reading files" },
      { type: "tool_result", toolName: "rg", summary: "Found the UI entry points" },
      { type: "file", text: "Implementation notes", fileName: "notes.md", sizeBytes: 2048, mimeType: "text/markdown" },
    ],
  },
  {
    id: "assistant-2",
    sessionId: "session-1",
    role: "assistant",
    status: "completed",
    createdAt: "2026-07-31T00:01:00Z",
    parts: [
      { type: "step_ref", stepId: "inspect", title: "Inspect repository", status: "completed", detail: "Repository mapped" },
      { type: "image", text: "Architecture preview", url: "data:image/png;base64,preview", mimeType: "image/png" },
    ],
  },
];

describe("workspace inspector projections", () => {
  it("updates repeated steps in place while preserving timeline order", () => {
    expect(projectActivityItems(messages)).toEqual([
      {
        id: "step:inspect",
        kind: "step",
        title: "Inspect repository",
        detail: "Repository mapped",
        status: "completed",
        messageId: "assistant-2",
      },
      {
        id: "tool:assistant-1:1",
        kind: "tool",
        title: "rg",
        detail: "Found the UI entry points",
        status: "completed",
        messageId: "assistant-1",
      },
    ]);
  });

  it("projects file and image parts as real artifacts", () => {
    expect(projectArtifactItems(messages)).toMatchObject([
      { id: "file:notes.md", kind: "file", title: "notes.md", sizeBytes: 2048 },
      {
        kind: "image",
        title: "Architecture preview",
        description: "Image artifact from the current session.",
        url: "data:image/png;base64,preview",
      },
    ]);
  });

  it("returns quiet empty collections when the transcript has no inspectable parts", () => {
    const plain = [{ ...messages[0], parts: [{ type: "markdown" as const, text: "Done" }] }];

    expect(projectActivityItems(plain)).toEqual([]);
    expect(projectArtifactItems(plain)).toEqual([]);
  });
});
