/** Decide whether Desktop may start its managed local OpenPPX Node. */
export function shouldStartManagedNode(input: {
  targetType: "local" | "remote";
  endpointReachable: boolean;
  openppxRootExists: boolean;
}): boolean {
  return input.targetType === "local" && !input.endpointReachable && input.openppxRootExists;
}
