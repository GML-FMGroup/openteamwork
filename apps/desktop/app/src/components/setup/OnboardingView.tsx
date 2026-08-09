import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type {
  ConnectionSettings,
  ConnectionTestState,
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
  connectionTestState: ConnectionTestState;
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

type SetupStepId = "node" | "agent" | "hello";

const SETUP_STEP_ORDER: SetupStepId[] = ["node", "agent", "hello"];

const CONNECTION_STATE_LABELS: Record<ConnectionTestState, string> = {
  untested: "Not tested",
  testing: "Testing…",
  connected: "Connected",
  failed: "Connection failed",
};

function SetupStep({
  active,
  complete,
  description,
  index,
  label,
  onSelect,
}: {
  active: boolean;
  complete: boolean;
  description: string;
  index: string;
  label: string;
  onSelect?: () => void;
}) {
  return (
    <li className={`${complete ? "complete" : "pending"} ${active ? "active" : ""}`}>
      <button type="button" onClick={onSelect} disabled={!onSelect} aria-current={active ? "step" : undefined} aria-label={label}>
        <span className="onboarding-step-indicator" aria-hidden="true">{complete ? "✓" : index}</span>
        <span className="onboarding-step-copy" aria-hidden="true">
          <strong>{label}</strong>
          <small>{description}</small>
        </span>
      </button>
    </li>
  );
}

function CodexAuthControl({
  auth,
  loading,
  error,
  onBegin,
  onRefresh,
  onOpenPage,
}: {
  auth: ProviderAuthStatus | null;
  loading: boolean;
  error: string | null;
  onBegin: () => void;
  onRefresh: () => void;
  onOpenPage: () => void;
}) {
  return (
    <div className="onboarding-auth-card full-row">
      <div className="onboarding-auth-copy">
        <span className={`auth-state-dot ${auth?.state === "authenticated" ? "is-ready" : ""}`} aria-hidden="true" />
        <div>
          <strong>{auth?.state === "authenticated" ? "Authenticated on this Node" : auth?.state === "pending" ? "Waiting for sign-in" : "ChatGPT sign-in required"}</strong>
          <p>{auth?.state === "authenticated"
            ? "OpenPPX will use this Node's Codex CLI login. Credentials stay on the Node."
            : "Use device-code sign-in so this also works when the Node is on another machine."}</p>
        </div>
      </div>
      {auth?.session?.userCode && auth.state === "pending" ? (
        <div className="onboarding-device-code">
          <span>ONE-TIME CODE</span>
          <strong>{auth.session.userCode}</strong>
          <button type="button" className="secondary" onClick={onOpenPage}>Open sign-in page</button>
        </div>
      ) : (
        <button
          type="button"
          className="secondary"
          onClick={auth?.state === "authenticated" ? onRefresh : onBegin}
          disabled={loading}
        >
          {loading ? "Checking…" : auth?.state === "authenticated" ? "Recheck" : "Sign in with ChatGPT"}
        </button>
      )}
      {error ? <p className="onboarding-auth-error" role="alert">{error}</p> : null}
    </div>
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

/** Scroll-aware three-step setup surface driven by Actions from the connected Node. */
export function OnboardingView({
  platform,
  status,
  form,
  connection,
  connectionTestState,
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
  const [editingSavedConfiguration, setEditingSavedConfiguration] = useState(false);
  const [configurationDirty, setConfigurationDirty] = useState(false);
  const shellRef = useRef<HTMLElement | null>(null);
  const sectionRefs = useRef<Record<SetupStepId, HTMLElement | null>>({ node: null, agent: null, hello: null });
  const pendingScrollStep = useRef<SetupStepId | null>(null);
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
    if (configured) {
      setActiveStep("hello");
      setEditingSavedConfiguration(false);
    }
  }, [configured]);

  useEffect(() => {
    if (configured && !editingSavedConfiguration) return undefined;
    const shell = shellRef.current;
    if (!shell) return undefined;

    function updateVisibleStep(): void {
      if (!shell || shell.scrollTop <= 1) {
        setActiveStep("node");
        return;
      }
      const shellRect = shell.getBoundingClientRect();
      const activationLine = shellRect.top + Math.min(Math.max(shellRect.height * 0.22, 120), 200);
      let visibleStep: SetupStepId = "node";
      for (const step of SETUP_STEP_ORDER) {
        const section = sectionRefs.current[step];
        if (section && section.getBoundingClientRect().top <= activationLine) visibleStep = step;
      }
      const reachedBottom = shell.clientHeight > 0
        && shell.scrollHeight > shell.clientHeight
        && shell.scrollTop + shell.clientHeight >= shell.scrollHeight - 4;
      setActiveStep(reachedBottom ? "hello" : visibleStep);
    }

    updateVisibleStep();
    shell.addEventListener("scroll", updateVisibleStep, { passive: true });
    window.addEventListener("resize", updateVisibleStep);
    return () => {
      shell.removeEventListener("scroll", updateVisibleStep);
      window.removeEventListener("resize", updateVisibleStep);
    };
  }, [configured, editingSavedConfiguration]);

  useEffect(() => {
    if (!editingSavedConfiguration || pendingScrollStep.current === null) return;
    const step = pendingScrollStep.current;
    pendingScrollStep.current = null;
    setActiveStep(step);
    scrollToStep(step);
  }, [editingSavedConfiguration]);

  function updateForm(patch: Partial<SetupForm>, configuration = true): void {
    if (configuration) setConfigurationDirty(true);
    setForm((current) => ({ ...current, ...patch }));
  }

  function updateConnection(patch: Partial<ConnectionSettings>): void {
    setConfigurationDirty(true);
    setConnection((current) => ({ ...current, ...patch }));
  }

  function selectStep(step: SetupStepId): void {
    setActiveStep(step);
    if (configured && !editingSavedConfiguration) {
      if (step === "hello") return;
      pendingScrollStep.current = step;
      setEditingSavedConfiguration(true);
      return;
    }
    scrollToStep(step);
  }

  function scrollToStep(step: SetupStepId): void {
    const section = sectionRefs.current[step];
    if (!section || typeof section.scrollIntoView !== "function") return;
    const reduceMotion = typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    section.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  }

  const authControl = usesNodeCodexAuth ? (
    <CodexAuthControl
      auth={providerAuth}
      loading={providerAccessLoading}
      error={providerAccessError}
      onBegin={onBeginProviderAuth}
      onRefresh={onRefreshProviderAuth}
      onOpenPage={onOpenProviderAuthPage}
    />
  ) : null;

  return (
    <main ref={shellRef} className={`onboarding-shell platform-${platform}`}>
      <header className="onboarding-brand">
        <span className="onboarding-mark" aria-hidden="true">P</span>
        <span>OpenPPX</span>
      </header>
      <div className="onboarding-layout">
        <aside className="onboarding-intro">
          <p className="eyebrow">{editingSavedConfiguration ? "CONFIGURATION" : configured ? "VERIFICATION" : "FIRST RUN"}</p>
          <h1>{editingSavedConfiguration ? "Edit your saved configuration." : configured ? "Verify your saved agent." : "Set up your first agent."}</h1>
          <p>{editingSavedConfiguration
            ? "Review the Node and Agent, then verify one real conversation. Resource IDs remain fixed after creation."
            : configured
              ? "Your configuration is saved. Verify one real conversation before entering the workspace."
              : "Configure a Node and its first Agent, then verify one real conversation."}</p>
          <ol className="onboarding-steps">
            <SetupStep active={activeStep === "node"} complete={status.steps.node === "complete"} index="1" label="Node" description="Connection & identity" onSelect={() => selectStep("node")} />
            <SetupStep active={activeStep === "agent"} complete={status.steps.agent === "complete" && status.steps.model === "complete"} index="2" label="Agent" description="Workspace & model" onSelect={() => selectStep("agent")} />
            <SetupStep active={activeStep === "hello"} complete={status.steps.hello === "verified"} index="3" label="First Hello" description="Real model check" onSelect={() => selectStep("hello")} />
          </ol>
        </aside>

        <form
          className="onboarding-form"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit(shouldApplyConfiguration);
          }}
        >
          {!configured || editingSavedConfiguration ? (
            <section ref={(element) => { sectionRefs.current.node = element; }} className="onboarding-section" data-setup-step="node">
              <div className="onboarding-section-heading">
                <div><span>01</span><h2>Node</h2></div>
                <span className="onboarding-connection-state" aria-live="polite">
                  <small className={`onboarding-state-badge ${connectionTestState}`}>
                    {CONNECTION_STATE_LABELS[connectionTestState]}
                  </small>
                </span>
              </div>
              <div className="onboarding-field-grid two-columns">
                <label>
                  <span>Run location</span>
                  <select
                    value={connection.targetType}
                    disabled={testingConnection || savingConnection}
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
                    disabled={testingConnection || savingConnection}
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
                      disabled={testingConnection || savingConnection}
                      onChange={(event) => updateConnection({ accessToken: event.target.value })}
                    />
                  </label>
                ) : null}
                <label className="full-row">
                  <span>Node name</span>
                  <input value={form.nodeName} onChange={(event) => updateForm({ nodeName: event.target.value })} />
                </label>
              </div>
              <div className="onboarding-inline-actions">
                <button type="button" className="secondary" onClick={onTestConnection} disabled={testingConnection || savingConnection}>
                  {testingConnection ? "Testing…" : "Test"}
                </button>
                <button type="button" className="secondary" onClick={onSaveConnection} disabled={testingConnection || savingConnection}>
                  {savingConnection ? "Connecting…" : "Use connection"}
                </button>
                {connectionFeedback ? (
                  <p
                    className={`onboarding-connection-feedback ${connectionTestState === "failed" ? "failed" : "success"}`}
                    role={connectionTestState === "failed" ? "alert" : "status"}
                  >
                    {connectionFeedback}
                  </p>
                ) : null}
              </div>
            </section>
          ) : null}

          {!configured || editingSavedConfiguration ? (
            <section ref={(element) => { sectionRefs.current.agent = element; }} className="onboarding-section" data-setup-step="agent">
              <div className="onboarding-section-heading"><div><span>02</span><h2>Agent</h2></div></div>
              <div className="onboarding-field-grid two-columns">
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

                <div className="onboarding-agent-model full-row">
                  <div className="onboarding-subsection-heading">
                    <div><span>MODEL</span><strong>Default model</strong></div>
                    <small>This Agent can use a different Model Profile from other Agents.</small>
                  </div>
                  <div className="onboarding-field-grid two-columns">
                    <label>
                      <span>Provider</span>
                      <select
                        value={form.provider}
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
                          disabled={providerAccessLoading || providerModels.items.length === 0}
                          onChange={(event) => updateForm({ model: event.target.value })}
                        >
                          {providerModels.items.map((item) => (
                            <option value={item.id} key={item.id}>{item.displayName}</option>
                          ))}
                        </select>
                      ) : (
                        <input value={form.model} onChange={(event) => updateForm({ model: event.target.value })} spellCheck={false} />
                      )}
                    </label>
                    {provider?.credentialMode === "api_key" ? (
                      <label className="full-row">
                        <span>API key</span>
                        <input type="password" autoComplete="new-password" value={form.apiKey} onChange={(event) => updateForm({ apiKey: event.target.value })} placeholder={status.steps.credential === "available" ? "Saved securely; leave blank to keep it" : "Stored in the system credential store"} />
                      </label>
                    ) : authControl ?? (provider?.credentialMode === "oauth" ? (
                      <p className="onboarding-provider-note full-row">This provider uses credentials already available on the Node.</p>
                    ) : null)}
                  </div>
                </div>
              </div>
            </section>
          ) : null}

          <section ref={(element) => { sectionRefs.current.hello = element; }} className="onboarding-section" data-setup-step="hello">
              <div className="onboarding-section-heading"><div><span>03</span><h2>First Hello</h2></div></div>
              <div className="onboarding-field-grid two-columns">
                {configured ? (
                  <div className="onboarding-verification-summary full-row">
                    <div><span>Agent</span><strong>{form.agentName}</strong><small>ID: {form.agentId}</small></div>
                    <div><span>Model</span><strong>{form.model}</strong><small>{provider?.displayName ?? form.provider}</small></div>
                    <div><span>Node</span><strong>{form.nodeName}</strong></div>
                  </div>
                ) : null}
                {configured && usesNodeCodexAuth ? authControl : null}
                {configured && provider?.credentialMode === "api_key" ? (
                  <p className="onboarding-provider-note full-row">The Agent will reuse its saved model credential. Open Agent to update it.</p>
                ) : null}
                <label className="full-row"><span>First message</span><input value={form.hello} onChange={(event) => updateForm({ hello: event.target.value }, false)} /></label>
              </div>
          </section>

          {configurationDiagnostic ? <div className="onboarding-error" role="alert">{configurationDiagnostic}</div> : null}
          {error ? <div className="onboarding-error" role="alert">{error}</div> : null}
          <footer className="onboarding-submit">
            <p>{configurationDiagnostic
              ? "Repair the invalid Node configuration, then retry."
              : configured && configurationDirty
                ? "Your changes will be saved before OpenPPX verifies the Agent."
              : configured
                ? "Saved configuration will not be changed. OpenPPX will create one Session and verify the Agent."
                : "A real Agent response is required before the workspace opens."}</p>
            <button type="submit" disabled={!canSubmit || submitting}>
              {submitting
                ? (configured ? "Verifying…" : "Setting up…")
                : configured && configurationDirty
                  ? "Save & verify"
                  : configured
                    ? "Verify & open workspace"
                    : "Set up & say Hello"}
            </button>
          </footer>
        </form>
      </div>
    </main>
  );
}
