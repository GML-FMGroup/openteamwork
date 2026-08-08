import {
  normalizeClientApiMessage,
  normalizeClientApiPart,
  normalizeClientApiSession,
} from "@openppx/client";
import { normalizeClientApiRuntime } from "../app/src/lib/client-api-projection";

describe("client api projection helpers", () => {
  it("normalizes runtime payloads", () => {
    const runtime = normalizeClientApiRuntime({
      target: { id: "local-default", type: "local", name: "This Mac" },
      state: "healthy",
      summary: "ready",
      detail: "detail",
    });

    expect(runtime).toMatchObject({
      target: { id: "local-default", type: "local", name: "This Mac" },
      state: "healthy",
      summary: "ready",
      detail: "detail",
    });
  });

  it("normalizes session payloads", () => {
    const session = normalizeClientApiSession({
      id: "session_1",
      agent_id: "writer",
      title: "Demo",
      updated_at: "2026-04-02T12:00:00+08:00",
      last_message_preview: "preview",
    });

    expect(session).toMatchObject({
      id: "session_1",
      agentId: "writer",
      title: "Demo",
      lastMessagePreview: "preview",
    });
  });

  it("normalizes step parts and chat messages", () => {
    const part = normalizeClientApiPart({
      type: "step_ref",
      step_id: "step_1",
      title: "inspect_repo",
      status: "running",
      detail: "Scanning files",
    });
    const message = normalizeClientApiMessage({
      id: "msg_1",
      session_id: "session_1",
      run_id: "run_1",
      role: "assistant",
      status: "streaming",
      created_at: "2026-04-02T12:00:00+08:00",
      parts: [
        { type: "markdown", text: "hello" },
        {
          type: "step_ref",
          step_id: "step_1",
          title: "inspect_repo",
          status: "running",
          detail: "Scanning files",
        },
      ],
    });

    expect(part).toMatchObject({
      type: "step_ref",
      stepId: "step_1",
      title: "inspect_repo",
      status: "running",
    });
    expect(message).toMatchObject({
      id: "msg_1",
      sessionId: "session_1",
      runId: "run_1",
      role: "assistant",
      status: "streaming",
    });
    expect(message?.parts).toHaveLength(2);
  });

  it("normalizes tool result parts", () => {
    const part = normalizeClientApiPart({
      type: "tool_result",
      tool_call_id: "call-1",
      tool_name: "inspect_repo",
      summary: "inspect_repo returned successfully.",
      detail: "2 files changed",
      raw_text: "{\n  \"ok\": true\n}",
    });

    expect(part).toMatchObject({
      type: "tool_result",
      toolCallId: "call-1",
      toolName: "inspect_repo",
      summary: "inspect_repo returned successfully.",
      detail: "2 files changed",
      rawText: "{\n  \"ok\": true\n}",
    });
  });

  it("normalizes visible progress commentary without treating it as a final answer", () => {
    const part = normalizeClientApiPart({
      type: "commentary",
      text: "I will inspect the repository first.",
    });

    expect(part).toEqual({
      type: "commentary",
      text: "I will inspect the repository first.",
    });
  });
});
