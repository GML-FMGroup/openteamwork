import { shouldStartManagedNode } from "../electron/main/node-start-policy";

describe("Desktop managed Node start policy", () => {
  it("starts only for an unreachable local endpoint with a source root", () => {
    expect(shouldStartManagedNode({ targetType: "local", endpointReachable: false, openppxRootExists: true })).toBe(
      true,
    );
    expect(shouldStartManagedNode({ targetType: "local", endpointReachable: true, openppxRootExists: true })).toBe(
      false,
    );
    expect(shouldStartManagedNode({ targetType: "remote", endpointReachable: false, openppxRootExists: true })).toBe(
      false,
    );
    expect(shouldStartManagedNode({ targetType: "local", endpointReachable: false, openppxRootExists: false })).toBe(
      false,
    );
  });
});
