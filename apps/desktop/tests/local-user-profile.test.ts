import { describe, expect, it } from "vitest";
import { buildLocalUserProfile } from "../app/src/lib/user-profile";

describe("local user profile", () => {
  it("keeps the stable principal separate from the display name", () => {
    expect(buildLocalUserProfile("  wenhaojiang  ")).toEqual({
      id: "ppx-client-user",
      displayName: "wenhaojiang",
      accountKind: "local",
    });
  });

  it("uses a neutral label when the operating-system username is unavailable", () => {
    expect(buildLocalUserProfile(" ").displayName).toBe("Local user");
  });
});
