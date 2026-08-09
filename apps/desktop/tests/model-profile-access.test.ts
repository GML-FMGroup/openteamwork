import { describe, expect, it } from "vitest";
import { modelProfileAccessPresentation } from "../app/src/components/settings/model-profile-access";
import type { ModelProfileSummary, ProviderAuthStatus, SetupProvider } from "../app/src/types";

const profile: ModelProfileSummary = {
  id: "primary",
  displayName: "Primary",
  revision: "sha256:primary",
  provider: "openai_codex",
  model: "openai-codex/gpt-5.5",
  enabled: true,
  credentialState: "not_required",
};

function provider(credentialMode: SetupProvider["credentialMode"]): SetupProvider {
  return {
    id: profile.provider,
    displayName: "Provider",
    runtime: "test",
    credentialMode,
    credentialRequired: credentialMode === "api_key",
    defaultModel: profile.model,
  };
}

function auth(state: ProviderAuthStatus["state"]): ProviderAuthStatus {
  return {
    providerId: profile.provider,
    state,
    source: state === "authenticated" ? "codex_cli" : null,
    expiresAt: null,
    loginMode: "device_code",
    session: null,
  };
}

describe("modelProfileAccessPresentation", () => {
  it("describes OAuth readiness using Node authentication instead of Profile credential storage", () => {
    expect(modelProfileAccessPresentation(profile, provider("oauth"), { kind: "resolved", status: auth("authenticated") })).toEqual({
      label: "Signed in on Node",
      tone: "ready",
    });
    expect(modelProfileAccessPresentation(profile, provider("oauth"), { kind: "resolved", status: auth("not_authenticated") })).toEqual({
      label: "Sign-in required",
      tone: "blocked",
    });
    expect(modelProfileAccessPresentation(profile, provider("oauth"), { kind: "resolved", status: auth("expired") })).toEqual({
      label: "Sign-in required",
      tone: "blocked",
    });
    expect(modelProfileAccessPresentation(profile, provider("oauth"), { kind: "resolved", status: auth("pending") })).toEqual({
      label: "Sign-in in progress",
      tone: "pending",
    });
    expect(modelProfileAccessPresentation(profile, provider("oauth"), { kind: "loading" })).toEqual({
      label: "Checking sign-in…",
      tone: "pending",
    });
    expect(modelProfileAccessPresentation(profile, provider("oauth"), { kind: "error" })).toEqual({
      label: "Sign-in status unavailable",
      tone: "blocked",
    });
  });

  it("uses explicit access copy for API-key and credential-free providers", () => {
    expect(modelProfileAccessPresentation({ ...profile, credentialState: "available" }, provider("api_key"))).toEqual({
      label: "API key saved",
      tone: "ready",
    });
    expect(modelProfileAccessPresentation({ ...profile, credentialState: "missing" }, provider("api_key"))).toEqual({
      label: "API key required",
      tone: "blocked",
    });
    expect(modelProfileAccessPresentation({ ...profile, credentialState: "backend_unavailable" }, provider("api_key"))).toEqual({
      label: "Credential store unavailable",
      tone: "blocked",
    });
    expect(modelProfileAccessPresentation(profile, provider("none"))).toEqual({
      label: "No credentials needed",
      tone: "ready",
    });
  });

  it("does not report an unknown provider as ready", () => {
    expect(modelProfileAccessPresentation(profile, null)).toEqual({
      label: "Access status unavailable",
      tone: "blocked",
    });
  });
});
