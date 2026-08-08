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

  it("preserves chronology and only merges adjacent repeated operations", () => {
    const groups = projectActivityGroups([
      message([
        { type: "step_ref", stepId: "search-1", title: "web_search", status: "completed", detail: '{"query":"first"}' },
        { type: "step_ref", stepId: "search-2", title: "web_search", status: "completed", detail: '{"query":"second"}' },
        { type: "step_ref", stepId: "read-1", title: "web_fetch", status: "completed", detail: '{"url":"https://example.com"}' },
        { type: "step_ref", stepId: "search-3", title: "web_search", status: "completed", detail: '{"query":"third"}' },
      ]),
    ]);

    expect(groups.map((group) => group.key)).toEqual(["web-search", "web-read", "web-search"]);
    expect(groups.map((group) => group.count)).toEqual([2, 1, 1]);
    expect(summarizeActivityGroups(groups)).toBe("3 searches · 1 source");
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

  it("projects search, URL, file, and command targets into safe action details", () => {
    const groups = projectActivityGroups([
      message([
        { type: "step_ref", stepId: "search", title: "web_search", status: "completed", detail: '{"query":"OpenPPX architecture"}' },
        { type: "step_ref", stepId: "source", title: "web_fetch", status: "completed", detail: '{"url":"https://docs.example.com/guide"}' },
        { type: "step_ref", stepId: "file", title: "read_file", status: "completed", detail: "path: docs/design.md\nline: 12" },
        { type: "step_ref", stepId: "command", title: "exec", status: "completed", detail: '{"command":"pnpm test"}' },
      ]),
    ]);

    const entries = groups.flatMap((group) => group.entries);
    expect(entries[0]).toMatchObject({
      detail: "OpenPPX architecture",
      details: [
        { label: "Query", value: "OpenPPX architecture", kind: "query" },
        { label: "Status", value: "Completed", kind: "status" },
      ],
    });
    expect(entries[1]?.detail).toBe("docs.example.com");
    expect(entries[1]?.details[0]).toMatchObject({
      label: "Source",
      value: "https://docs.example.com/guide",
      kind: "url",
      href: "https://docs.example.com/guide",
    });
    expect(entries[2]?.details[0]).toMatchObject({ label: "File", value: "docs/design.md", kind: "file" });
    expect(entries[3]?.details[0]).toMatchObject({ label: "Command", value: "pnpm test", kind: "command" });
  });

  it("uses a generic MCP target without exposing credentials", () => {
    const groups = projectActivityGroups([
      message([
        {
          type: "step_ref",
          stepId: "mcp-call",
          title: "functions.custom_mcp/read_resource",
          status: "completed",
          detail: '{"resource":"project://roadmap","api_key":"secret-value"}',
        },
      ]),
    ]);

    const entry = groups[0]?.entries[0];
    expect(entry?.detail).toBe("project://roadmap");
    expect(entry?.details[0]).toMatchObject({ label: "Resource", value: "project://roadmap" });
    expect(JSON.stringify(entry?.details)).not.toContain("secret-value");
    expect(entry?.rawDetail).not.toContain("secret-value");
  });
});
