import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
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
  onSubmit: (applyConfiguration: boolean) => void;
  onBeginProviderAuth: () => void;
  onRefreshProviderAuth: () => void;
  onOpenProviderAuthPage: () => void;
}

type SetupStepId = "node" | "agent" | "model" | "hello";

function SetupStep({
  active,
  complete,
  label,
  onSelect,
}: {
  active: boolean;
  complete: boolean;
  label: string;
  onSelect?: () => void;
}) {
  return (
    <li className={`${complete ? "complete" : "pending"} ${active ? "active" : ""}`}>
      <button type="button" onClick={onSelect} disabled={!onSelect} aria-current={active ? "step" : undefined}>
        <span aria-hidden="true">{complete ? "✓" : ""}</span>
        {label}
      </button>
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
  const [activeStep, setActiveStep] = useState<SetupStepId>(configured ? "hello" : "node");
  const [configurationDirty, setConfigurationDirty] = useState(false);
  const editingSavedConfiguration = configured && activeStep !== "hello";
  const verifyingSavedConfiguration = configured && activeStep === "hello";
  const configurationDiagnostic = setupDiagnosticMessage(status);
  const providerReady = Boolean(
    (!usesNodeCodexAuth || providerAuth?.state === "authenticated") &&
    (provider?.credentialMode !== "api_key" || form.apiKey.trim() || status.steps.credential === "available"),
  );
  const shouldApplyConfiguration = status.state !== "configured" || configurationDirty;
  const canSubmit = configured && !shouldApplyConfiguration
    ? Boolean(!status.diagnostic && form.agentId.trim() && form.model.trim() && form.hello.trim() && providerReady)
    : Boolean(
      !status.diagnostic &&
      form.nodeId.trim() &&
      form.nodeName.trim() &&
      form.agentId.trim() &&
      form.agentName.trim() &&
      form.workspace.trim() &&
      form.profileId.trim() &&
      form.model.trim() &&
      form.hello.trim() &&
      providerReady,
    );

  useEffect(() => {
    if (configured) setActiveStep("hello");
  }, [configured]);

  function updateForm(patch: Partial<SetupForm>, configuration = true): void {
    if (configuration) setConfigurationDirty(true);
    setForm((current) => ({ ...current, ...patch }));
  }

  function updateConnection(patch: Partial<ConnectionSettings>): void {
    setConfigurationDirty(true);
    setConnection((current) => ({ ...current, ...patch }));
  }

  return (
    <main className={`onboarding-shell platform-${platform}`}>
      <header className="onboarding-brand">
        <span className="onboarding-mark" aria-hidden="true">P</span>
        <span>OpenPPX</span>
      </header>
      <div className="onboarding-layout">
        <aside className="onboarding-intro">
          <p className="eyebrow">{editingSavedConfiguration ? "CONFIGURATION" : configured ? "VERIFICATION" : "FIRST RUN"}</p>
          <h1>{editingSavedConfiguration ? "Edit your saved configuration." : configured ? "Verify your saved agent." : "Set up your agent workspace."}</h1>
          <p>{editingSavedConfiguration
            ? "Review one focused section, then return to verification. Agent IDs remain fixed after creation."
            : configured
              ? "Your configuration is saved. Verify one real conversation before entering the workspace."
            : "Connect a Node, choose a model, and verify one real conversation before entering the workspace."}</p>
          <ol className="onboarding-steps">
            <SetupStep active={configured && activeStep === "node"} complete={status.steps.node === "complete"} label="Node" onSelect={configured ? () => setActiveStep("node") : undefined} />
            <SetupStep active={configured && activeStep === "agent"} complete={status.steps.agent === "complete"} label="Agent" onSelect={configured ? () => setActiveStep("agent") : undefined} />
            <SetupStep active={configured && activeStep === "model"} complete={status.steps.model === "complete"} label="Model" onSelect={configured ? () => setActiveStep("model") : undefined} />
            <SetupStep active={configured && activeStep === "hello"} complete={status.steps.hello === "verified"} label="First Hello" onSelect={configured ? () => setActiveStep("hello") : undefined} />
          </ol>
        </aside>

        <form
          className="onboarding-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (editingSavedConfiguration) {
              setActiveStep("hello");
              return;
            }
            onSubmit(shouldApplyConfiguration);
          }}
        >
          {!configured || activeStep === "node" ? (
            <section className="onboarding-section">
                <div className="onboarding-section-heading">
                  <div><span>01</span><h2>{configured ? "Node" : "Connection"}</h2></div>
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
                      onChange={(event) => updateConnection({
                        targetType: event.target.value === "lan" ? "lan" : "local",
                        accessToken: "",
                      })}
                    >
                      <option value="local">This computer</option>
                      <option value="lan">OpenPPX Node on LAN</option>
                    </select>
                  </label>
                  <label>
                    <span>Node URL</span>
                    <input
                      value={connection.clientApiBaseUrl}
                      onChange={(event) => updateConnection({ clientApiBaseUrl: event.target.value })}
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
                        onChange={(event) => updateConnection({ accessToken: event.target.value })}
                      />
                    </label>
                  ) : null}
                  {configured ? (
                    <label className="full-row">
                      <span>Node name</span>
                      <input value={form.nodeName} onChange={(event) => updateForm({ nodeName: event.target.value })} />
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
          ) : null}

          {!configured || activeStep === "agent" ? (
            <section className="onboarding-section">
                <div className="onboarding-section-heading"><div><span>02</span><h2>{configured ? "Agent" : "Workspace"}</h2></div></div>
                <div className="onboarding-field-grid two-columns">
                  {!configured ? <label><span>Node name</span><input value={form.nodeName} onChange={(event) => updateForm({ nodeName: event.target.value })} /></label> : null}
                  <label>
                    <span>Agent ID</span>
                    <input value={form.agentId} disabled aria-label="Agent ID" />
                  </label>
                  <label><span>Agent name</span><input value={form.agentName} onChange={(event) => updateForm({ agentName: event.target.value })} /></label>
                  <label className="full-row"><span>Workspace folder</span><input value={form.workspace} onChange={(event) => updateForm({ workspace: event.target.value })} spellCheck={false} /></label>
                  <label className="full-row compact-field">
                    <span>Privilege</span>
                    <select value={form.privilegeLevel} onChange={(event) => updateForm({ privilegeLevel: event.target.value as SetupForm["privilegeLevel"] })}>
                      <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="root">Root</option>
                    </select>
                  </label>
                </div>
            </section>
          ) : null}

          {!configured || activeStep === "model" || activeStep === "hello" ? (
          <section className="onboarding-section">
            <div className="onboarding-section-heading">
              <div><span>{verifyingSavedConfiguration ? "04" : "03"}</span><h2>{verifyingSavedConfiguration ? "Verification" : "Model"}</h2></div>
            </div>
            <div className="onboarding-field-grid two-columns">
              {verifyingSavedConfiguration ? (
                <div className="onboarding-verification-summary full-row">
                  <div><span>Agent</span><strong>{form.agentName}</strong><small>ID: {form.agentId}</small></div>
                  <div><span>Node</span><strong>{form.nodeName}</strong></div>
                </div>
              ) : null}
              <label>
                <span>Provider</span>
                <select
                  value={form.provider}
                  disabled={verifyingSavedConfiguration}
                  onChange={(event) => {
                    const next = status.providers.find((item) => item.id === event.target.value);
                    updateForm({
                      provider: event.target.value,
                      model: next?.defaultModel ?? "",
                      apiKey: "",
                    });
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
                    disabled={verifyingSavedConfiguration || providerAccessLoading || providerModels.items.length === 0}
                    onChange={(event) => updateForm({ model: event.target.value })}
                  >
                    {providerModels.items.map((item) => (
                      <option value={item.id} key={item.id}>{item.displayName}</option>
                    ))}
                  </select>
                ) : (
                  <input disabled={verifyingSavedConfiguration} value={form.model} onChange={(event) => updateForm({ model: event.target.value })} spellCheck={false} />
                )}
              </label>
              {provider?.credentialMode === "api_key" ? (
                verifyingSavedConfiguration ? (
                  <p className="onboarding-provider-note full-row">The saved credential will be reused for verification.</p>
                ) : (
                  <label className="full-row">
                    <span>API key</span>
                    <input type="password" autoComplete="new-password" value={form.apiKey} onChange={(event) => updateForm({ apiKey: event.target.value })} placeholder={status.steps.credential === "available" ? "Saved securely; leave blank to keep it" : "Stored in the system credential store"} />
                  </label>
                )
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
              {!configured || activeStep === "hello" ? (
                <label className="full-row"><span>First message</span><input value={form.hello} onChange={(event) => updateForm({ hello: event.target.value }, false)} /></label>
              ) : null}
            </div>
          </section>
          ) : null}

          {configurationDiagnostic ? <div className="onboarding-error" role="alert">{configurationDiagnostic}</div> : null}
          {error ? <div className="onboarding-error" role="alert">{error}</div> : null}
          <footer className="onboarding-submit">
            <p>{configurationDiagnostic
              ? "Repair the invalid Node configuration, then retry."
              : editingSavedConfiguration
                ? configurationDirty
                  ? "Changes are kept locally until you return to verification."
                  : "Review this saved section without changing the immutable resource IDs."
              : configured && configurationDirty
                ? "Your changes will be saved before OpenPPX verifies the model."
              : configured
                ? "Saved configuration will not be changed. OpenPPX will create one Session and verify the model."
                : "A real model response is required before the workspace opens."}</p>
            {editingSavedConfiguration ? (
              <button type="button" onClick={() => setActiveStep("hello")}>Back to verification</button>
            ) : (
              <button type="submit" disabled={!canSubmit || submitting}>
                {submitting
                  ? (configured ? "Verifying…" : "Setting up…")
                  : configured && configurationDirty
                    ? "Save & verify"
                    : configured
                      ? "Verify & open workspace"
                      : "Set up & say Hello"}
              </button>
            )}
          </footer>
        </form>
      </div>
    </main>
  );
}
