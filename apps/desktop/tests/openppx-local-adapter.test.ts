import {
  buildMessagePartsFromSessionEvent,
  projectRunEventToStepParts,
} from "../app/src/lib/openppx-projection";
import type { ChatMessage } from "../app/src/types";
import {
  findLatestPersistedRunMessage,
  isTerminalRunStatus,
  monitorPersistedTerminalRun,
} from "../electron/main/run-terminal-reconciliation";
import { normalizeWorkspaceAgents } from "../app/src/lib/agent-projection";

describe("openppx local adapter projections", () => {
  it("keeps disabled Agents out of the workspace session surface", () => {
    expect(normalizeWorkspaceAgents([
      { id: "root", name: "Root", enabled: false, status: "disabled" },
      { id: "main", name: "Monica", enabled: true, status: "healthy" },
    ])).toEqual([
      expect.objectContaining({ id: "main", name: "Monica", enabled: true }),
    ]);
  });

  it("uses persisted messages only after the outer Run is authoritatively terminal", () => {
    const messages: ChatMessage[] = [
      {
        id: "message-running",
        sessionId: "session-1",
        runId: "run-1",
        role: "assistant",
        status: "streaming",
        createdAt: "2026-08-08T12:00:00.000Z",
        parts: [],
      },
      {
        id: "message-complete",
        sessionId: "session-1",
        runId: "run-1",
        role: "assistant",
        status: "completed",
        createdAt: "2026-08-08T12:00:01.000Z",
        parts: [{ type: "markdown", text: "Done" }],
      },
    ];

    expect(isTerminalRunStatus("running")).toBe(false);
    expect(isTerminalRunStatus("completed")).toBe(true);
    expect(findLatestPersistedRunMessage(messages, "run-1")?.id).toBe("message-complete");
  });

  it("selects the latest durable subturn after terminality is proven", () => {
    const persisted = findLatestPersistedRunMessage([
      {
        id: "message-first-subturn",
        sessionId: "session-1",
        runId: "run-1",
        role: "assistant",
        status: "completed",
        createdAt: "2026-08-08T12:00:01.000Z",
        parts: [{ type: "commentary", text: "I will inspect this." }],
      },
      {
        id: "message-final-subturn",
        sessionId: "session-1",
        runId: "run-1",
        role: "assistant",
        status: "completed",
        createdAt: "2026-08-08T12:00:05.000Z",
        parts: [{ type: "markdown", text: "Final answer" }],
      },
    ], "run-1");

    expect(persisted?.id).toBe("message-final-subturn");
  });

  it("periodically reconciles an active stream until the persisted terminal message appears", async () => {
    const reconcile = vi
      .fn<() => Promise<boolean>>()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    const wait = vi.fn(async () => undefined);

    await expect(monitorPersistedTerminalRun({ reconcile, wait, intervalMs: 2_000 })).resolves.toBe(true);

    expect(wait).toHaveBeenCalledTimes(2);
    expect(wait).toHaveBeenNthCalledWith(1, 2_000, undefined);
    expect(reconcile).toHaveBeenCalledTimes(2);
  });

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

    expect(parts[0]).toMatchObject({ type: "commentary", text: "I will inspect the workspace." });
    expect(parts[1]).toMatchObject({
      type: "step_ref",
      stepId: "call-1",
      title: "inspect_workspace",
      status: "completed",
    });
    expect(parts[2]).toMatchObject({
      type: "tool_result",
      toolCallId: "call-1",
      toolName: "inspect_workspace",
      status: "completed",
    });
    expect(parts).toHaveLength(3);
  });

  it("updates running tool cards from Node run events", () => {
    const started = projectRunEventToStepParts(
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

    const finished = projectRunEventToStepParts(
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

  it("projects structured FunctionResponse failures without scanning response prose", () => {
    const documentedErrors = projectRunEventToStepParts(
      {
        content: {
          parts: [
            {
              function_response: {
                id: "skill-1",
                name: "read_skill",
                response: { result: "Zero formula errors. Explain blocked cells and failed validation examples." },
              },
            },
          ],
        },
      },
      [],
    );
    const explicitFailure = projectRunEventToStepParts(
      {
        content: {
          parts: [
            {
              function_response: {
                id: "skill-2",
                name: "read_skill",
                response: { ok: false, error: "Unknown skill" },
              },
            },
          ],
        },
      },
      [],
    );

    expect(documentedErrors[0]).toMatchObject({ status: "completed" });
    expect(explicitFailure[0]).toMatchObject({ status: "failed" });
  });

  it("strips request-time guidance from reloaded user history", () => {
    const parts = buildMessagePartsFromSessionEvent({
      content: {
        parts: [
          {
            text:
              "Current request time: 2026-04-03T12:32:17+08:00 (CST)\n" +
              "Use this as the reference 'now' for relative time expressions in this message.\n\n" +
              "今天日期给我一下",
          },
        ],
      },
    });

    expect(parts).toEqual([{ type: "markdown", text: "今天日期给我一下" }]);
  });
});
