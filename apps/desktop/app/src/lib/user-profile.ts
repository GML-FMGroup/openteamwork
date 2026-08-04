import { LOCAL_USER_ID, type UserProfile } from "../types";

/** Build a non-sensitive local profile while preserving its stable principal id. */
export function buildLocalUserProfile(username: string): UserProfile {
  const displayName = username.trim() || "Local user";
  return {
    id: LOCAL_USER_ID,
    displayName,
    accountKind: "local",
  };
}
