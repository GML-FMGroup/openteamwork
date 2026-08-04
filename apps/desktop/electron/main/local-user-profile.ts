import os from "node:os";
import type { UserProfile } from "../../app/src/types";
import { buildLocalUserProfile } from "../../app/src/lib/user-profile";

/** Resolve the Desktop account without exposing process or environment details. */
export function resolveLocalUserProfile(): UserProfile {
  return buildLocalUserProfile(os.userInfo().username);
}
