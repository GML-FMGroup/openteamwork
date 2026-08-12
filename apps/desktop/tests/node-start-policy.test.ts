import { shouldStartManagedNode } from "../electron/main/node-start-policy";

describe("Desktop managed Node start policy", () => {
  it("starts only after an explicit local connection refusal with a source root", () => {
    expect(shouldStartManagedNode({ targetType: "local", failure: "connection-refused", openppxRootExists: true })).toBe(
      true,
    );
    expect(shouldStartManagedNode({ targetType: "local", failure: "timeout", openppxRootExists: true })).toBe(
      false,
    );
    expect(shouldStartManagedNode({ targetType: "local", failure: "other", openppxRootExists: true })).toBe(
      false,
    );
    expect(shouldStartManagedNode({ targetType: "remote", failure: "connection-refused", openppxRootExists: true })).toBe(
      false,
    );
    expect(shouldStartManagedNode({ targetType: "local", failure: "connection-refused", openppxRootExists: false })).toBe(
      false,
    );
  });
});
