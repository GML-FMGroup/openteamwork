import { describe, expect, it } from "vitest";
import { sortSessionsByRecency } from "../app/src/lib/session-order";
import type { SessionSummary } from "../app/src/types";

function session(id: string, updatedAt: string): SessionSummary {
  return {
    id,
    agentId: "agent-1",
    title: id,
    updatedAt,
    lastMessagePreview: "",
  };
}

describe("sortSessionsByRecency", () => {
  it("orders mixed ISO timezone formats by their actual instant", () => {
    const olderLocalOffset = session("older", "2026-08-07T16:00:00+08:00");
    const newerUtc = session("newer", "2026-08-07T09:00:00Z");
    const input = [olderLocalOffset, newerUtc];

    expect(sortSessionsByRecency(input).map((item) => item.id)).toEqual(["newer", "older"]);
    expect(input.map((item) => item.id)).toEqual(["older", "newer"]);
  });

  it("keeps equal timestamps stable and places invalid timestamps last", () => {
    const input = [
      session("first", "2026-08-07T09:00:00Z"),
      session("invalid-a", "not-a-date"),
      session("second", "2026-08-07T09:00:00+00:00"),
      session("invalid-b", ""),
    ];

    expect(sortSessionsByRecency(input).map((item) => item.id)).toEqual([
      "first",
      "second",
      "invalid-a",
      "invalid-b",
    ]);
  });
});
