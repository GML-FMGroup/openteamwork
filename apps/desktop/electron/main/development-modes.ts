export interface DesktopDevelopmentModes {
  mockEnabled: boolean;
  legacyBridgeEnabled: boolean;
}

function enabled(value: string | undefined): boolean {
  return ["1", "true", "yes", "on"].includes((value ?? "").trim().toLowerCase());
}

/** Resolve explicit development-only runtime modes from the process environment. */
export function resolveDesktopDevelopmentModes(
  environment: Record<string, string | undefined> = process.env,
): DesktopDevelopmentModes {
  return {
    mockEnabled: enabled(environment.OPENPPX_DESKTOP_MOCK),
    legacyBridgeEnabled: enabled(environment.OPENPPX_DESKTOP_LEGACY_BRIDGE),
  };
}

/** Return whether one target may use the opt-in legacy bridge. */
export function canUseLegacyBridge(
  targetType: "local" | "remote",
  modes: DesktopDevelopmentModes,
): boolean {
  return targetType === "local" && modes.legacyBridgeEnabled;
}

/** Decide whether Desktop may start its managed local Client API process. */
export function shouldStartManagedClientApi(input: {
  targetType: "local" | "remote";
  endpointReachable: boolean;
  openppxRootExists: boolean;
}): boolean {
  return input.targetType === "local" && !input.endpointReachable && input.openppxRootExists;
}
