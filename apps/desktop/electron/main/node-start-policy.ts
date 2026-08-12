export type ClientApiConnectionFailure = "connection-refused" | "timeout" | "other";

/** Decide whether Desktop may start its managed local OpenPPX Node. */
export function shouldStartManagedNode(input: {
  targetType: "local" | "remote";
  failure: ClientApiConnectionFailure;
  openppxRootExists: boolean;
}): boolean {
  return (
    input.targetType === "local"
    && input.failure === "connection-refused"
    && input.openppxRootExists
  );
}
