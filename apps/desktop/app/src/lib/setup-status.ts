import type { SetupReadinessResult, SetupStatusResult } from "../types";

const COMPLETE_CREDENTIAL_STATES = new Set(["available", "not_required"]);

/** Return whether the persisted Node, Agent, and model can open a workspace. */
export function isWorkspaceConfigurationComplete(
  status: SetupReadinessResult | SetupStatusResult | null | undefined,
): boolean {
  if (!status) {
    return false;
  }
  return status.steps.node === "complete"
    && status.steps.agent === "complete"
    && status.steps.model === "complete"
    && COMPLETE_CREDENTIAL_STATES.has(status.steps.credential);
}

/** Project the rich root setup document to the ordinary workspace-readiness contract. */
export function setupReadinessFromStatus(status: SetupStatusResult): SetupReadinessResult {
  return {
    state: status.state,
    workspaceReady: isWorkspaceConfigurationComplete(status),
    steps: {
      node: status.steps.node ?? "missing",
      agent: status.steps.agent ?? "missing",
      model: status.steps.model ?? "missing",
      credential: status.steps.credential ?? "missing",
    },
  };
}
