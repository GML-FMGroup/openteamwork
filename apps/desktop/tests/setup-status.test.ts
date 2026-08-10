import { describe, expect, it } from "vitest";

import { isWorkspaceConfigurationComplete, setupReadinessFromStatus } from "../app/src/lib/setup-status";
import type { SetupStatusResult } from "../app/src/types";

function setupStatus(steps: Record<string, string>): SetupStatusResult {
  return {
    state: "configured",
    steps,
    revisions: { node: "node", agent: "agent", profile: "profile" },
    recommendedWorkspace: "/workspace",
    diagnostic: null,
    current: { node: {}, agent: {}, profile: {} },
    providers: [],
  };
}

describe("workspace setup status", () => {
  it("accepts complete core configuration without requiring a current Hello", () => {
    expect(isWorkspaceConfigurationComplete(setupStatus({
      node: "complete",
      agent: "complete",
      model: "complete",
      credential: "not_required",
      hello: "stale",
    }))).toBe(true);
  });

  it.each(["node", "agent", "model"])("rejects an incomplete %s step", (step) => {
    expect(isWorkspaceConfigurationComplete(setupStatus({
      node: step === "node" ? "missing" : "complete",
      agent: step === "agent" ? "missing" : "complete",
      model: step === "model" ? "missing" : "complete",
      credential: "available",
      hello: "verified",
    }))).toBe(false);
  });

  it("rejects a missing model credential", () => {
    expect(isWorkspaceConfigurationComplete(setupStatus({
      node: "complete",
      agent: "complete",
      model: "complete",
      credential: "missing",
      hello: "verified",
    }))).toBe(false);
  });

  it("projects rich administrator status to non-sensitive workspace readiness", () => {
    expect(setupReadinessFromStatus(setupStatus({
      node: "complete",
      agent: "complete",
      model: "complete",
      credential: "available",
      hello: "stale",
    }))).toEqual({
      state: "configured",
      workspaceReady: true,
      steps: {
        node: "complete",
        agent: "complete",
        model: "complete",
        credential: "available",
      },
    });
  });
});
