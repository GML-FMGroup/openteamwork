import { act, renderHook } from "@testing-library/react";
import { useActiveRuns } from "../app/src/hooks/use-active-runs";

describe("useActiveRuns", () => {
  it("tracks run ids by Session and does not resurrect a finished Run", () => {
    const { result } = renderHook(() => useActiveRuns());

    act(() => result.current.begin("session-1"));
    expect(result.current.sessionIds).toEqual(["session-1"]);
    expect(result.current.runIdForSession("session-1")).toBeUndefined();

    act(() => result.current.attachRunId("session-1", "run-1"));
    expect(result.current.runIdForSession("session-1")).toBe("run-1");

    act(() => result.current.finish("session-1"));
    act(() => result.current.attachRunId("session-1", "late-run-id"));
    expect(result.current.sessionIds).toEqual([]);
  });
});
