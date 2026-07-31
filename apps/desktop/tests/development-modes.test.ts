import {
  canUseLegacyBridge,
  resolveDesktopDevelopmentModes,
  shouldStartManagedClientApi,
} from "../electron/main/development-modes";

describe("Desktop development modes", () => {
  it("keeps mock and legacy bridge disabled by default", () => {
    expect(resolveDesktopDevelopmentModes({})).toEqual({
      mockEnabled: false,
      legacyBridgeEnabled: false,
    });
  });

  it("requires explicit truthy flags", () => {
    expect(
      resolveDesktopDevelopmentModes({
        OPENPPX_DESKTOP_MOCK: "true",
        OPENPPX_DESKTOP_LEGACY_BRIDGE: "1",
      }),
    ).toEqual({ mockEnabled: true, legacyBridgeEnabled: true });
  });

  it("never permits the local bridge for a remote target", () => {
    const modes = { mockEnabled: false, legacyBridgeEnabled: true };

    expect(canUseLegacyBridge("local", modes)).toBe(true);
    expect(canUseLegacyBridge("remote", modes)).toBe(false);
  });

  it("starts a managed API only for an unreachable local endpoint with a source root", () => {
    expect(
      shouldStartManagedClientApi({ targetType: "local", endpointReachable: false, openppxRootExists: true }),
    ).toBe(true);
    expect(
      shouldStartManagedClientApi({ targetType: "local", endpointReachable: true, openppxRootExists: true }),
    ).toBe(false);
    expect(
      shouldStartManagedClientApi({ targetType: "remote", endpointReachable: false, openppxRootExists: true }),
    ).toBe(false);
  });
});
