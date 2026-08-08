import type { Dispatch, SetStateAction } from "react";
import type {
  ClientDiagnostics,
  ConnectionSettings,
  DesktopPlatform,
  ModelCatalogResult,
  ProviderAuthStatus,
  SetupForm,
  SetupStatusResult,
} from "../../types";

interface OnboardingViewProps {
  platform: DesktopPlatform;
  status: SetupStatusResult;
  form: SetupForm;
  connection: ConnectionSettings;
  diagnostics: ClientDiagnostics | null;
  submitting: boolean;
  testingConnection: boolean;
  savingConnection: boolean;
  error: string | null;
  providerModels: ModelCatalogResult | null;
  providerAuth: ProviderAuthStatus | null;
  providerAccessLoading: boolean;
  providerAccessError: string | null;
  connectionFeedback: string | null;
  setForm: Dispatch<SetStateAction<SetupForm>>;
  setConnection: Dispatch<SetStateAction<ConnectionSettings>>;
  onTestConnection: () => void;
  onSaveConnection: () => void;
  onSubmit: () => void;
  onBeginProviderAuth: () => void;
  onRefreshProviderAuth: () => void;
  onOpenProviderAuthPage: () => void;
}

function SetupStep({ complete, label }: { complete: boolean; label: string }) {
  return (
    <li className={complete ? "complete" : "pending"}>
      <span aria-hidden="true">{complete ? "✓" : ""}</span>
      {label}
    </li>
  );
}

function setupDiagnosticMessage(status: SetupStatusResult): string | null {
  const diagnostic = status.diagnostic;
  if (!diagnostic) return null;
  const issue = diagnostic.issues[0];
  const label = diagnostic.component === "model"
    ? "Model profile"
    : diagnostic.component === "hello"
      ? "Setup verification"
      : diagnostic.component[0].toUpperCase() + diagnostic.component.slice(1);
  const field = issue?.path.length ? ` (${issue.path.join(".")})` : "";
  return `${label} configuration is invalid${field}. ${issue?.message ?? "Configuration could not be read."} Repair the Node configuration and retry.`;
}

/** First-run control surface driven entirely by setup Actions from the connected Node. */
export function OnboardingView({
  platform,
  status,
  form,
  connection,
  diagnostics,
  submitting,
  testingConnection,
  savingConnection,
  error,
  providerModels,
  providerAuth,
  providerAccessLoading,
  providerAccessError,
  connectionFeedback,
  setForm,
  setConnection,
  onTestConnection,
  onSaveConnection,
  onSubmit,
  onBeginProviderAuth,
  onRefreshProviderAuth,
  onOpenProviderAuthPage,
}: OnboardingViewProps) {
  const provider = status.providers.find((item) => item.id === form.provider);
  const usesNodeCodexAuth = provider?.id === "openai_codex";
  const configured = status.state === "configured";
  const configurationDiagnostic = setupDiagnosticMessage(status);
  const canSubmit = Boolean(
    !status.diagnostic &&
    form.nodeId.trim() &&
      form.nodeName.trim() &&
      form.agentId.trim() &&
      form.agentName.trim() &&
      form.workspace.trim() &&
      form.profileId.trim() &&
      form.model.trim() &&
      form.hello.trim() &&
      (!usesNodeCodexAuth || providerAuth?.state === "authenticated") &&
      (provider?.credentialMode !== "api_key" || form.apiKey.trim() || status.steps.credential === "available"),
  );

  return (
    <main className={`onboarding-shell platform-${platform}`}>
      <header className="onboarding-brand">
        <span className="onboarding-mark" aria-hidden="true">P</span>
        <span>OpenPPX</span>
      </header>
      <div className="onboarding-layout">
        <aside className="onboarding-intro">
          <p className="eyebrow">FIRST RUN</p>
          <h1>Set up your agent workspace.</h1>
          <p>Connect a Node, choose a model, and verify one real conversation before entering the workspace.</p>
          <ol className="onboarding-steps">
            <SetupStep complete={status.steps.node === "complete"} label="Node" />
            <SetupStep complete={status.steps.agent === "complete"} label="Agent" />
            <SetupStep complete={status.steps.model === "complete"} label="Model" />
            <SetupStep complete={status.steps.hello === "verified"} label="First Hello" />
          </ol>
        </aside>

        <form
          className="onboarding-form"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          <section className="onboarding-section">
            <div className="onboarding-section-heading">
              <div><span>01</span><h2>Connection</h2></div>
              <span className="onboarding-connection-state" aria-live="polite">
                {diagnostics?.clientApiHealthy ? (
                  <>
                    <small className="onboarding-state-badge connected">Connected</small>
                    {!configured ? <small className="onboarding-state-badge setup-required">Setup required</small> : null}
                  </>
                ) : <small className="onboarding-state-badge unavailable">Check connection</small>}
              </span>
            </div>
            <div className="onboarding-field-grid two-columns">
              <label>
                <span>Run location</span>
                <select
                  value={connection.targetType}
                  onChange={(event) => setConnection((current) => ({
                    ...current,
                    targetType: event.target.value === "lan" ? "lan" : "local",
                    accessToken: "",
                  }))}
                >
                  <option value="local">This computer</option>
                  <option value="lan">OpenPPX Node on LAN</option>
                </select>
              </label>
              <label>
                <span>Node URL</span>
                <input
                  value={connection.clientApiBaseUrl}
                  onChange={(event) => setConnection((current) => ({ ...current, clientApiBaseUrl: event.target.value }))}
                  spellCheck={false}
                />
              </label>
              {connection.targetType === "lan" ? (
                <label className="full-row">
                  <span>Access token</span>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={connection.accessToken ?? ""}
                    onChange={(event) => setConnection((current) => ({ ...current, accessToken: event.target.value }))}
                  />
                </label>
              ) : null}
            </div>
            <div className="onboarding-inline-actions">
              <button type="button" className="secondary" onClick={onTestConnection} disabled={testingConnection || savingConnection}>
                {testingConnection ? "Testing…" : "Test"}
              </button>
              <button type="button" className="secondary" onClick={onSaveConnection} disabled={testingConnection || savingConnection}>
                {savingConnection ? "Connecting…" : "Use connection"}
              </button>
              {connectionFeedback ? <small>{connectionFeedback}</small> : null}
            </div>
          </section>

          <section className="onboarding-section">
            <div className="onboarding-section-heading"><div><span>02</span><h2>Workspace</h2></div></div>
            <div className="onboarding-field-grid two-columns">
              <label><span>Node name</span><input value={form.nodeName} onChange={(event) => setForm((current) => ({ ...current, nodeName: event.target.value }))} /></label>
              <label><span>Agent name</span><input value={form.agentName} onChange={(event) => setForm((current) => ({ ...current, agentName: event.target.value }))} /></label>
              <label className="full-row"><span>Workspace folder</span><input value={form.workspace} onChange={(event) => setForm((current) => ({ ...current, workspace: event.target.value }))} spellCheck={false} /></label>
              <label className="full-row compact-field">
                <span>Privilege</span>
                <select value={form.privilegeLevel} onChange={(event) => setForm((current) => ({ ...current, privilegeLevel: event.target.value as SetupForm["privilegeLevel"] }))}>
                  <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="root">Root</option>
                </select>
              </label>
            </div>
          </section>

          <section className="onboarding-section">
            <div className="onboarding-section-heading"><div><span>03</span><h2>Model</h2></div></div>
            <div className="onboarding-field-grid two-columns">
              <label>
                <span>Provider</span>
                <select
                  value={form.provider}
                  onChange={(event) => {
                    const next = status.providers.find((item) => item.id === event.target.value);
                    setForm((current) => ({
                      ...current,
                      provider: event.target.value,
                      model: next?.defaultModel ?? "",
                      apiKey: "",
                    }));
                  }}
                >
                  {status.providers.map((item) => <option value={item.id} key={item.id}>{item.displayName}</option>)}
                </select>
              </label>
              <label>
                <span>Model</span>
                {providerModels?.authoritative ? (
                  <select
                    value={form.model}
                    disabled={providerAccessLoading || providerModels.items.length === 0}
                    onChange={(event) => setForm((current) => ({ ...current, model: event.target.value }))}
                  >
                    {providerModels.items.map((item) => (
                      <option value={item.id} key={item.id}>{item.displayName}</option>
                    ))}
                  </select>
                ) : (
                  <input value={form.model} onChange={(event) => setForm((current) => ({ ...current, model: event.target.value }))} spellCheck={false} />
                )}
              </label>
              {provider?.credentialMode === "api_key" ? (
                <label className="full-row">
                  <span>API key</span>
                  <input type="password" autoComplete="new-password" value={form.apiKey} onChange={(event) => setForm((current) => ({ ...current, apiKey: event.target.value }))} placeholder={status.steps.credential === "available" ? "Saved securely; leave blank to keep it" : "Stored in the system credential store"} />
                </label>
              ) : usesNodeCodexAuth ? (
                <div className="onboarding-auth-card full-row">
                  <div className="onboarding-auth-copy">
                    <span className={`auth-state-dot ${providerAuth?.state === "authenticated" ? "is-ready" : ""}`} aria-hidden="true" />
                    <div>
                      <strong>{providerAuth?.state === "authenticated" ? "Authenticated on this Node" : providerAuth?.state === "pending" ? "Waiting for sign-in" : "ChatGPT sign-in required"}</strong>
                      <p>{providerAuth?.state === "authenticated"
                        ? "OpenPPX will use this Node's Codex CLI login. Credentials stay on the Node."
                        : "Use device-code sign-in so this also works when the Node is on another machine."}</p>
                    </div>
                  </div>
                  {providerAuth?.session?.userCode && providerAuth.state === "pending" ? (
                    <div className="onboarding-device-code">
                      <span>ONE-TIME CODE</span>
                      <strong>{providerAuth.session.userCode}</strong>
                      <button type="button" className="secondary" onClick={onOpenProviderAuthPage}>Open sign-in page</button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="secondary"
                      onClick={providerAuth?.state === "authenticated" ? onRefreshProviderAuth : onBeginProviderAuth}
                      disabled={providerAccessLoading}
                    >
                      {providerAccessLoading ? "Checking…" : providerAuth?.state === "authenticated" ? "Recheck" : "Sign in with ChatGPT"}
                    </button>
                  )}
                  {providerAccessError ? <p className="onboarding-auth-error" role="alert">{providerAccessError}</p> : null}
                </div>
              ) : provider?.credentialMode === "oauth" ? (
                <p className="onboarding-provider-note full-row">This provider uses credentials already available on the Node.</p>
              ) : null}
              <label className="full-row"><span>First message</span><input value={form.hello} onChange={(event) => setForm((current) => ({ ...current, hello: event.target.value }))} /></label>
            </div>
          </section>

          {configurationDiagnostic ? <div className="onboarding-error" role="alert">{configurationDiagnostic}</div> : null}
          {error ? <div className="onboarding-error" role="alert">{error}</div> : null}
          <footer className="onboarding-submit">
            <p>{configurationDiagnostic
              ? "Repair the invalid Node configuration, then retry."
              : configured
                ? "Configuration is saved. Retry the real model check to finish."
                : "A real model response is required before the workspace opens."}</p>
            <button type="submit" disabled={!canSubmit || submitting}>
              {submitting ? "Setting up…" : configured ? "Retry setup & Hello" : "Set up & say Hello"}
            </button>
          </footer>
        </form>
      </div>
    </main>
  );
}
