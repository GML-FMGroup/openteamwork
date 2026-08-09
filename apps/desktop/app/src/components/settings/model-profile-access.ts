import type { ModelProfileSummary, ProviderAuthStatus, SetupProvider } from "../../types";

export type ModelProviderAuthCheck =
  | { kind: "loading" }
  | { kind: "resolved"; status: ProviderAuthStatus }
  | { kind: "error" };

export interface ModelProfileAccessPresentation {
  label: string;
  tone: "ready" | "pending" | "blocked";
}

/** Project storage and provider-auth details into one user-facing access state. */
export function modelProfileAccessPresentation(
  profile: ModelProfileSummary,
  provider: SetupProvider | null,
  authCheck?: ModelProviderAuthCheck,
): ModelProfileAccessPresentation {
  if (!provider) {
    return { label: "Access status unavailable", tone: "blocked" };
  }
  if (provider.credentialMode === "none") {
    return { label: "No credentials needed", tone: "ready" };
  }
  if (provider.credentialMode === "api_key") {
    if (profile.credentialState === "available") {
      return { label: "API key saved", tone: "ready" };
    }
    if (profile.credentialState === "backend_unavailable") {
      return { label: "Credential store unavailable", tone: "blocked" };
    }
    return { label: "API key required", tone: "blocked" };
  }
  if (!authCheck || authCheck.kind === "loading") {
    return { label: "Checking sign-in…", tone: "pending" };
  }
  if (authCheck.kind === "error") {
    return { label: "Sign-in status unavailable", tone: "blocked" };
  }
  if (authCheck.status.state === "authenticated") {
    return { label: "Signed in on Node", tone: "ready" };
  }
  if (authCheck.status.state === "pending") {
    return { label: "Sign-in in progress", tone: "pending" };
  }
  return { label: "Sign-in required", tone: "blocked" };
}
