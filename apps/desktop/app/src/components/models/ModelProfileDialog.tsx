import { useEffect, useMemo, useRef, useState } from "react";
import type {
  ModelCapability,
  ModelCatalogResult,
  ModelProfileCreateInput,
  ModelProfileResourceResult,
  ModelProfileSummary,
  ModelProfileUpdateInput,
  ProviderAuthStatus,
  SetupProvider,
} from "../../types";

const CAPABILITIES: Array<{ id: ModelCapability; label: string }> = [
  { id: "text", label: "Text" },
  { id: "vision", label: "Vision" },
  { id: "audio_input", label: "Audio input" },
  { id: "audio_output", label: "Audio output" },
  { id: "tool_calling", label: "Tool calling" },
  { id: "structured_output", label: "Structured output" },
  { id: "reasoning", label: "Reasoning" },
  { id: "long_context", label: "Long context" },
];

interface ModelProfileDialogProps {
  mode: "new" | "edit";
  profileId: string | null;
  profiles: ModelProfileSummary[];
  providers: SetupProvider[];
  onRead: (profileId: string) => Promise<ModelProfileResourceResult>;
  onGetModels: (providerId: string) => Promise<ModelCatalogResult>;
  onGetAuth: (providerId: string) => Promise<ProviderAuthStatus>;
  onBeginAuth: (providerId: string) => Promise<ProviderAuthStatus>;
  onRefreshAuth: (providerId: string) => Promise<ProviderAuthStatus>;
  onOpenExternal: (url: string) => Promise<void>;
  onCreate: (input: ModelProfileCreateInput) => Promise<ModelProfileResourceResult>;
  onUpdate: (input: ModelProfileUpdateInput) => Promise<ModelProfileResourceResult>;
  onCancel: () => void;
  onSaved: (result: ModelProfileResourceResult) => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Create and edit one Node-owned Model Profile without exposing stored credentials. */
export function ModelProfileDialog(props: ModelProfileDialogProps) {
  const initialProviderId = props.profiles[0]?.provider ?? props.providers[0]?.id ?? "";
  const [displayName, setDisplayName] = useState("");
  const [providerId, setProviderId] = useState(initialProviderId);
  const [model, setModel] = useState("");
  const [executionLocation, setExecutionLocation] = useState<"local" | "remote">("remote");
  const [apiBase, setApiBase] = useState("");
  const [capabilities, setCapabilities] = useState<ModelCapability[]>(["text", "tool_calling"]);
  const [contextWindowTokens, setContextWindowTokens] = useState("");
  const [inputCost, setInputCost] = useState("");
  const [outputCost, setOutputCost] = useState("");
  const [fallbackProfileIds, setFallbackProfileIds] = useState<string[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [expectedRevision, setExpectedRevision] = useState<string | null>(null);
  const [originalProviderId, setOriginalProviderId] = useState<string | null>(null);
  const [hasStoredCredential, setHasStoredCredential] = useState(false);
  const [catalog, setCatalog] = useState<ModelCatalogResult | null>(null);
  const [auth, setAuth] = useState<ProviderAuthStatus | null>(null);
  const [loadingProfile, setLoadingProfile] = useState(props.mode === "edit");
  const [loadingProvider, setLoadingProvider] = useState(false);
  const [authWorking, setAuthWorking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const displayNameRef = useRef<HTMLInputElement | null>(null);

  const provider = props.providers.find((item) => item.id === providerId) ?? null;
  const supportsApiBase = provider?.runtime === "litellm" || provider?.runtime === "codex";
  const credentialCanBeReused = originalProviderId === providerId && hasStoredCredential;
  const apiKeyMissing = provider?.credentialMode === "api_key" && !apiKey.trim() && !credentialCanBeReused;
  const oauthMissing = provider?.credentialMode === "oauth" && auth?.state !== "authenticated";

  useEffect(() => {
    displayNameRef.current?.focus();
  }, []);

  useEffect(() => {
    function handleEscape(event: KeyboardEvent): void {
      if (event.key === "Escape" && !saving) {
        event.preventDefault();
        props.onCancel();
      }
    }
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [saving]);

  useEffect(() => {
    if (props.mode !== "edit" || !props.profileId) {
      setLoadingProfile(false);
      return;
    }
    let active = true;
    setLoadingProfile(true);
    setError(null);
    void props.onRead(props.profileId)
      .then((result) => {
        if (!active) return;
        const spec = result.document.spec;
        setDisplayName(spec.displayName);
        setProviderId(spec.provider);
        setOriginalProviderId(spec.provider);
        setModel(spec.model);
        setExecutionLocation(spec.executionLocation);
        setApiBase(spec.apiBase ?? "");
        setCapabilities(spec.capabilities);
        setContextWindowTokens(spec.contextWindowTokens?.toString() ?? "");
        setInputCost(spec.inputCostPerMillionUsd ?? "");
        setOutputCost(spec.outputCostPerMillionUsd ?? "");
        setFallbackProfileIds(spec.fallbackProfiles);
        setEnabled(spec.enabled);
        setExpectedRevision(result.revision);
        setHasStoredCredential(Boolean(spec.credential));
      })
      .catch((nextError: unknown) => {
        if (active) setError(errorMessage(nextError));
      })
      .finally(() => {
        if (active) setLoadingProfile(false);
      });
    return () => {
      active = false;
    };
  }, [props.mode, props.profileId]);

  useEffect(() => {
    if (!providerId || loadingProfile) return;
    let active = true;
    setLoadingProvider(true);
    setError(null);
    setCatalog(null);
    setAuth(null);
    const nextProvider = props.providers.find((item) => item.id === providerId);
    void Promise.all([
      props.onGetModels(providerId),
      nextProvider?.credentialMode === "oauth" ? props.onGetAuth(providerId) : Promise.resolve(null),
    ])
      .then(([nextCatalog, nextAuth]) => {
        if (!active) return;
        setCatalog(nextCatalog);
        setAuth(nextAuth);
        setModel((current) => {
          if (!nextCatalog.authoritative || nextCatalog.items.some((item) => item.id === current)) return current;
          return nextCatalog.items[0]?.id ?? nextCatalog.defaultModel;
        });
      })
      .catch((nextError: unknown) => {
        if (active) setError(errorMessage(nextError));
      })
      .finally(() => {
        if (active) setLoadingProvider(false);
      });
    return () => {
      active = false;
    };
  }, [loadingProfile, providerId]);

  useEffect(() => {
    if (auth?.state !== "pending") return;
    const timer = window.setInterval(() => {
      void props.onGetAuth(providerId)
        .then((nextAuth) => {
          setAuth(nextAuth);
          if (nextAuth.session?.state === "failed") {
            setError(nextAuth.session.error ?? "Provider sign-in did not complete.");
          }
        })
        .catch((nextError: unknown) => setError(errorMessage(nextError)));
    }, 1_500);
    return () => window.clearInterval(timer);
  }, [auth?.state, providerId]);

  const fallbackOptions = useMemo(
    () => props.profiles.filter((item) => item.id !== props.profileId && item.enabled),
    [props.profileId, props.profiles],
  );

  const canSave = Boolean(
    displayName.trim() && providerId && model.trim() && capabilities.length && !apiKeyMissing && !oauthMissing,
  ) && !loadingProfile && !loadingProvider && !saving && provider?.runtime !== "unsupported";

  function toggleCapability(capability: ModelCapability): void {
    setCapabilities((current) => current.includes(capability)
      ? current.filter((item) => item !== capability)
      : [...current, capability]);
  }

  async function beginAuth(): Promise<void> {
    setAuthWorking(true);
    setError(null);
    try {
      const nextAuth = await props.onBeginAuth(providerId);
      setAuth(nextAuth);
      if (nextAuth.session?.verificationUrl) {
        await props.onOpenExternal(nextAuth.session.verificationUrl);
      }
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setAuthWorking(false);
    }
  }

  async function refreshAuth(): Promise<void> {
    setAuthWorking(true);
    setError(null);
    try {
      setAuth(await props.onRefreshAuth(providerId));
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setAuthWorking(false);
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      const fields: ModelProfileCreateInput = {
        displayName: displayName.trim(),
        providerId,
        model: model.trim(),
        executionLocation,
        apiBase: supportsApiBase && apiBase.trim() ? apiBase.trim() : null,
        capabilities,
        contextWindowTokens: contextWindowTokens ? Number(contextWindowTokens) : null,
        inputCostPerMillionUsd: inputCost.trim() || null,
        outputCostPerMillionUsd: outputCost.trim() || null,
        fallbackProfileIds,
        enabled,
        apiKey: apiKey || null,
      };
      const result = props.mode === "new"
        ? await props.onCreate(fields)
        : await props.onUpdate({
            ...fields,
            profileId: props.profileId!,
            expectedRevision: expectedRevision!,
          });
      setApiKey("");
      props.onSaved(result);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="agent-dialog-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !saving) props.onCancel();
    }}>
      <section className="agent-dialog model-profile-dialog" role="dialog" aria-modal="true" aria-labelledby="model-profile-title">
        <header className="agent-dialog-header model-profile-dialog-header">
          <div>
            <span className="agent-dialog-eyebrow">{props.mode === "new" ? "New Model Profile" : "Edit Model Profile"}</span>
            <h2 id="model-profile-title">{props.mode === "new" ? "Configure a model once." : displayName || "Model Profile"}</h2>
            <p>Profiles are Node-owned policies reused by Agents. Credential values remain write-only.</p>
          </div>
          <label className="model-profile-enabled">
            <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
            <span>Enabled</span>
          </label>
        </header>

        <form onSubmit={(event) => void submit(event)}>
          {loadingProfile ? <p className="model-profile-loading">Loading Model Profile…</p> : (
            <>
              <div className="agent-dialog-fields model-profile-fields">
                <label>
                  <span>Profile name</span>
                  <input
                    ref={displayNameRef}
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                    placeholder="Coding"
                    maxLength={80}
                  />
                  <small>A unique name shown to people and Agents on this Node.</small>
                </label>
                <label>
                  <span>Provider</span>
                  <select value={providerId} onChange={(event) => {
                    setProviderId(event.target.value);
                    setApiKey("");
                    setApiBase("");
                  }}>
                    {props.providers.map((item) => <option key={item.id} value={item.id}>{item.displayName}</option>)}
                  </select>
                  <small>{provider ? `${provider.runtime} runtime · ${provider.credentialMode.replace("_", " ")}` : "Select a provider."}</small>
                </label>
                <label className="agent-dialog-full-row">
                  <span>Model</span>
                  {catalog?.authoritative ? (
                    <select value={model} onChange={(event) => setModel(event.target.value)} disabled={loadingProvider}>
                      {catalog.items.map((item) => <option key={item.id} value={item.id}>{item.displayName || item.id}</option>)}
                    </select>
                  ) : (
                    <input value={model} onChange={(event) => setModel(event.target.value)} placeholder={catalog?.defaultModel ?? provider?.defaultModel ?? "provider/model"} list="model-profile-catalog" spellCheck={false} />
                  )}
                  <datalist id="model-profile-catalog">
                    {catalog?.items.map((item) => <option key={item.id} value={item.id} />)}
                  </datalist>
                  <small>{loadingProvider ? "Loading provider catalog…" : catalog?.authoritative ? "Select a model exposed by this provider." : "Enter a provider model ID; suggestions come from the Node catalog."}</small>
                </label>
              </div>

              <section className="model-profile-access" aria-label="Provider access">
                <div>
                  <span className="agent-dialog-eyebrow">Provider access</span>
                  <strong>{provider?.displayName ?? "Provider"}</strong>
                </div>
                {provider?.credentialMode === "api_key" ? (
                  <label>
                    <span>API key</span>
                    <input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={credentialCanBeReused ? "Saved securely · leave blank to keep" : "Required"} />
                    <small>{credentialCanBeReused ? "A new value rotates the protected credential." : "Stored by the Node and never returned to Desktop."}</small>
                  </label>
                ) : provider?.credentialMode === "oauth" ? (
                  <div className="model-profile-oauth">
                    <span className={`model-profile-auth-state ${auth?.state === "authenticated" ? "ready" : ""}`}>{auth?.state?.replace("_", " ") ?? "checking"}</span>
                    {auth?.session?.userCode ? <strong className="model-profile-auth-code">{auth.session.userCode}</strong> : null}
                    {auth?.session?.verificationUrl ? <button type="button" className="secondary" onClick={() => void props.onOpenExternal(auth.session!.verificationUrl!)}>Open sign-in page</button> : null}
                    <button type="button" className="secondary" onClick={() => void (auth?.state === "authenticated" ? refreshAuth() : beginAuth())} disabled={authWorking}>
                      {authWorking ? "Checking…" : auth?.state === "authenticated" ? "Recheck" : "Sign in"}
                    </button>
                  </div>
                ) : <p>No credential is required for this provider.</p>}
              </section>

              <details className="model-profile-advanced">
                <summary>Advanced configuration</summary>
                <div className="agent-dialog-fields model-profile-advanced-fields">
                  {props.mode === "edit" && props.profileId ? (
                    <div className="agent-dialog-full-row model-profile-technical-id">
                      <span>Internal ID</span>
                      <code>{props.profileId}</code>
                      <small>Generated by the Node and kept stable for Agent and fallback references.</small>
                    </div>
                  ) : null}
                  <label>
                    <span>Execution</span>
                    <select value={executionLocation} onChange={(event) => setExecutionLocation(event.target.value === "local" ? "local" : "remote")}>
                      <option value="remote">Remote model</option>
                      <option value="local">Local model</option>
                    </select>
                  </label>
                  <label>
                    <span>Context window</span>
                    <input type="number" min="1" step="1" value={contextWindowTokens} onChange={(event) => setContextWindowTokens(event.target.value)} placeholder="Optional tokens" />
                  </label>
                  {supportsApiBase ? (
                    <label className="agent-dialog-full-row">
                      <span>API Base</span>
                      <input value={apiBase} onChange={(event) => setApiBase(event.target.value)} placeholder="Provider default" spellCheck={false} />
                      <small>Optional HTTP(S) endpoint override for this Profile only.</small>
                    </label>
                  ) : null}
                  <fieldset className="agent-dialog-full-row model-profile-capabilities">
                    <legend>Capabilities</legend>
                    <div>{CAPABILITIES.map((item) => (
                      <label key={item.id} className={capabilities.includes(item.id) ? "selected" : ""}>
                        <input type="checkbox" checked={capabilities.includes(item.id)} onChange={() => toggleCapability(item.id)} />
                        <span>{item.label}</span>
                      </label>
                    ))}</div>
                  </fieldset>
                  <label>
                    <span>Input / 1M tokens (USD)</span>
                    <input inputMode="decimal" value={inputCost} onChange={(event) => setInputCost(event.target.value)} placeholder="Optional" />
                  </label>
                  <label>
                    <span>Output / 1M tokens (USD)</span>
                    <input inputMode="decimal" value={outputCost} onChange={(event) => setOutputCost(event.target.value)} placeholder="Optional" />
                  </label>
                  {fallbackOptions.length ? (
                    <fieldset className="agent-dialog-full-row model-profile-fallbacks">
                      <legend>Fallback Profiles</legend>
                      <div>{fallbackOptions.map((item) => (
                        <label key={item.id}>
                          <input type="checkbox" checked={fallbackProfileIds.includes(item.id)} onChange={(event) => setFallbackProfileIds((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} />
                          <span>{item.displayName}</span><small>{item.provider} · {item.model}</small>
                        </label>
                      ))}</div>
                    </fieldset>
                  ) : null}
                </div>
              </details>

              {error ? <p className="agent-dialog-error" role="alert">{error}</p> : apiKeyMissing ? (
                <p className="agent-dialog-error">Enter an API key for this provider.</p>
              ) : oauthMissing ? (
                <p className="agent-dialog-note">Sign in before saving this OAuth-backed Profile.</p>
              ) : (
                <p className="agent-dialog-note">Changes affect new runs. Existing runs keep their current model.</p>
              )}
            </>
          )}

          <footer className="agent-dialog-actions">
            <button type="button" className="agent-dialog-cancel" onClick={props.onCancel} disabled={saving}>Cancel</button>
            <button type="submit" className="agent-dialog-create" disabled={!canSave}>{saving ? "Saving…" : props.mode === "new" ? "Create Profile" : "Save changes"}</button>
          </footer>
        </form>
      </section>
    </div>
  );
}
