import {
  projectActivityGroups,
  summarizeActivityGroups,
} from "../app/src/lib/activity-presentation";
import type { ChatMessage } from "../app/src/types";

function message(parts: ChatMessage["parts"], overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "assistant-1",
    sessionId: "session-1",
    role: "assistant",
    status: "completed",
    createdAt: "2026-08-07T00:00:00Z",
    parts,
    ...overrides,
  };
}

describe("activity presentation", () => {
  it("coalesces a tool call and response into one semantic activity", () => {
    const groups = projectActivityGroups([
      message([
        {
          type: "step_ref",
          stepId: "call-1",
          title: "web_search",
          status: "completed",
          detail: '{"query":"Discovery Loop company"}',
        },
        {
          type: "tool_result",
          toolCallId: "call-1",
          toolName: "web_search",
          summary: "web_search returned 1 field.",
          detail: '{"result":"three results"}',
          rawText: '{"result":"three results"}',
        },
      ]),
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({
      key: "web-search",
      label: "Searched the web",
      count: 1,
      status: "completed",
    });
    expect(groups[0]?.entries).toHaveLength(1);
    expect(groups[0]?.entries[0]?.detail).toBe("Discovery Loop company");
  });

  it("folds response-shaped legacy steps into the preceding call", () => {
    const groups = projectActivityGroups([
      message([
        {
          type: "step_ref",
          stepId: "legacy-call",
          title: "list_skills",
          status: "completed",
          detail: "No tool arguments",
        },
        {
          type: "step_ref",
          stepId: "legacy-response",
          title: "list_skills",
          status: "completed",
          detail: '{"result":[{"name":"web"}]}',
        },
        {
          type: "tool_result",
          toolName: "list_skills",
          summary: "list_skills returned 1 field.",
          rawText: '{"result":[{"name":"web"}]}',
        },
      ]),
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({
      key: "capabilities",
      label: "Prepared capabilities",
      count: 1,
    });
  });

  it("groups repeated searches and keeps failures visible", () => {
    const groups = projectActivityGroups([
      message([
        { type: "step_ref", stepId: "search-1", title: "web_search", status: "completed", detail: '{"query":"one"}' },
        { type: "step_ref", stepId: "search-2", title: "web_search", status: "completed", detail: '{"query":"two"}' },
        { type: "step_ref", stepId: "search-3", title: "web_search", status: "completed", detail: '{"query":"three"}' },
        { type: "step_ref", stepId: "exec-1", title: "exec", status: "completed", detail: '{"result":"Error: Command blocked by policy"}' },
      ]),
    ]);

    expect(groups).toHaveLength(2);
    expect(groups[0]).toMatchObject({
      key: "web-search",
      label: "Searched the web",
      count: 3,
      countLabel: "3 searches",
    });
    expect(groups[1]).toMatchObject({
      key: "command",
      label: "Command blocked by security policy",
      status: "failed",
    });
    expect(summarizeActivityGroups(groups)).toBe("3 searches · 1 command");
  });

  it("redacts credential-like values from technical details", () => {
    const groups = projectActivityGroups([
      message([
        {
          type: "step_ref",
          stepId: "call-1",
          title: "custom_tool",
          status: "completed",
          detail: '{"api_key":"secret-value","query":"safe"}',
        },
      ]),
    ]);

    expect(groups[0]?.entries[0]?.rawDetail).toContain("[redacted]");
    expect(groups[0]?.entries[0]?.rawDetail).not.toContain("secret-value");
  });
});
