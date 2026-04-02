import {
  buildMessagePartsFromSessionEvent,
  projectBridgeEventToStepParts,
} from "../app/src/lib/openppx-projection";

describe("openppx local adapter projections", () => {
  it("renders history tool calls and responses into structured parts", () => {
    const parts = buildMessagePartsFromSessionEvent({
      content: {
        parts: [
          { text: "I will inspect the workspace." },
          {
            function_call: {
              id: "call-1",
              name: "inspect_workspace",
              args: { path: "repo-root" },
            },
          },
          {
            function_response: {
              id: "call-1",
              name: "inspect_workspace",
              response: { files: 12, ok: true },
            },
          },
        ],
      },
    });

    expect(parts[0]).toMatchObject({ type: "markdown", text: "I will inspect the workspace." });
    expect(parts[1]).toMatchObject({
      type: "step_ref",
      stepId: "call-1",
      title: "inspect_workspace",
      status: "completed",
    });
    expect(parts[2]).toMatchObject({
      type: "step_ref",
      stepId: "call-1",
      title: "inspect_workspace",
      status: "completed",
    });
    expect(parts[3]).toMatchObject({
      type: "code",
      language: "json",
    });
  });

  it("updates running tool cards from bridge events", () => {
    const started = projectBridgeEventToStepParts(
      {
        long_running_tool_ids: ["call-2"],
        content: {
          parts: [
            {
              function_call: {
                id: "call-2",
                name: "background_fetch",
                args: { query: "status" },
              },
            },
          ],
        },
      },
      [],
    );

    expect(started).toHaveLength(1);
    expect(started[0]).toMatchObject({
      type: "step_ref",
      stepId: "call-2",
      title: "background_fetch",
      status: "running",
    });

    const finished = projectBridgeEventToStepParts(
      {
        content: {
          parts: [
            {
              function_response: {
                id: "call-2",
                name: "background_fetch",
                response: { ok: true },
              },
            },
          ],
        },
      },
      started,
    );

    expect(finished).toHaveLength(1);
    expect(finished[0]).toMatchObject({
      type: "step_ref",
      stepId: "call-2",
      title: "background_fetch",
      status: "completed",
    });
  });
});
