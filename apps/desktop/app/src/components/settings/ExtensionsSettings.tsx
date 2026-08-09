import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type {
  AgentProfile,
  AppConnectionDetail,
  AppCredentialSpec,
  AppToolSpec,
  ExtensionDetail,
  ExtensionHealthHistory,
  ExtensionPreview,
  ExtensionPresentation,
  ExtensionProbeResult,
  ExtensionSourceRef,
  ExtensionSummary,
  ExtensionStarter,
  InstallableExtensionKind,
  McpServerResource,
  McpOAuthStatus,
  McpValueBinding,
  PluginHookStatus,
  PluginMarketplaceEntry,
  PluginMarketplaceSource,
  PluginMarketplaceSourceSpec,
} from "../../types";
import { ExtensionIcon } from "./ExtensionIcon";

interface ExtensionsSettingsProps {
  kind: ExtensionSummary["kind"];
  extensions: ExtensionSummary[];
  agents: AgentProfile[];
  selectedAgentId: string;
  loading: boolean;
  error: string | null;
  mutationId: string | null;
  onRefresh: () => void;
  onSetEnabled: (extension: ExtensionSummary, enabled: boolean) => void;
}

interface SourceDialogState {
  kind: InstallableExtensionKind;
  extension: ExtensionSummary | null;
  starter: ExtensionStarter | null;
}

interface MarketplaceDialogState {
  marketplace: PluginMarketplaceSource | null;
}

interface McpDialogState {
  extension: ExtensionDetail | null;
  starter: ExtensionStarter | null;
}

interface AppConnectionDialogState {
  app: ExtensionDetail;
  connection: AppConnectionDetail | null;
}

interface StarterMcpCredential {
  name: string;
  label: string;
  required: boolean;
}

interface StarterMcpTemplate {
  serverId: string;
  displayName: string;
  presentation: ExtensionPresentation;
  risk: "low" | "medium" | "high";
  credentials: StarterMcpCredential[];
  transport: Record<string, unknown>;
}

const KIND_ORDER: ExtensionSummary["kind"][] = ["plugin", "app", "mcp", "skill"];
const STARTER_FOLD = 8;

function title(value: string): string {
  return value === "mcp" ? "MCP" : `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`;
}

function plural(kind: ExtensionSummary["kind"]): string {
  if (kind === "mcp") return "MCP Servers";
  return `${title(kind)}s`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}

function asMcpResource(value: unknown): McpServerResource | undefined {
  const candidate = asRecord(value);
  const spec = asRecord(candidate.spec);
  const transport = asRecord(spec.transport);
  return candidate.kind === "McpServer" && typeof transport.type === "string"
    ? candidate as unknown as McpServerResource
    : undefined;
}

function splitLines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function parseKeyValueLines(value: string, label: string): Record<string, string> {
  return Object.fromEntries(splitLines(value).map((line) => {
    const separator = line.indexOf("=");
    if (separator < 1) throw new Error(`${label} must use NAME=value, one per line.`);
    return [line.slice(0, separator).trim(), line.slice(separator + 1)];
  }));
}

function parseBindings(value: string, label: string): Record<string, McpValueBinding> {
  return Object.fromEntries(Object.entries(parseKeyValueLines(value, label)).map(([name, rawValue]) => {
    if (rawValue.startsWith("@secret:")) {
      const secretName = rawValue.slice("@secret:".length).trim();
      if (!secretName) throw new Error(`${label} secret references require a name.`);
      return [name, { kind: "secret", secretRef: { store: "system", name: secretName } }];
    }
    if (rawValue.startsWith("@env:")) {
      const environmentName = rawValue.slice("@env:".length).trim();
      if (!/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(environmentName)) {
        throw new Error(`${label} environment references require a valid variable name.`);
      }
      return [name, { kind: "environment", name: environmentName }];
    }
    return [name, { kind: "literal", value: rawValue }];
  }));
}

function bindingLines(bindings: Record<string, McpValueBinding>): string {
  return Object.entries(bindings).map(([name, binding]) => {
    if (binding.kind === "secret") return `${name}=@secret:${binding.secretRef.name}`;
    if (binding.kind === "environment") return `${name}=@env:${binding.name}`;
    return `${name}=${binding.value}`;
  }).join("\n");
}

function sourceDescription(extension: ExtensionSummary): string {
  const trust = extension.source.trust === "third_party" ? "third-party" : extension.source.trust;
  return `${extension.version || "unversioned"} · ${trust} · ${extension.risk} risk`;
}

function starterButtonLabel(starter: ExtensionStarter): string {
  if (starter.installMode === "unavailable") return "Details";
  if (starter.installMode === "reference") return "Set up";
  if (starter.availability === "needs_auth") return "Connect";
  if (starter.availability === "needs_dependency") return "Set up";
  return "Use";
}

function starterStatus(starter: ExtensionStarter): string {
  if (starter.availability === "needs_auth") return "Authentication required";
  if (starter.availability === "needs_dependency") return "Dependency required";
  if (starter.availability === "planned") return "Not available yet";
  return "Ready to add";
}

function starterListStatus(starter: ExtensionStarter): string | null {
  if (starter.availability === "needs_dependency") return "Dependency required";
  if (starter.availability === "planned") return "Not available yet";
  return null;
}

/** Return the durable App identity owned by a directly installable catalog entry. */
function starterAppDefinitionId(starter: ExtensionStarter): string | null {
  if (starter.kind !== "app" || starter.installMode !== "direct_app") return null;
  const definition = asRecord(starter.template.definition);
  const metadata = asRecord(definition.metadata);
  return typeof metadata.name === "string" ? metadata.name : null;
}

/** Treat an App as configured only after the Node reports a saved connection. */
function isConfiguredExtension(extension: ExtensionSummary): boolean {
  return extension.kind !== "app" || extension.status !== "installed";
}

function starterMcpTemplate(starter: ExtensionStarter | null): StarterMcpTemplate | null {
  if (!starter || starter.installMode !== "direct_mcp") return null;
  const template = asRecord(starter.template);
  const transport = asRecord(template.transport);
  if (
    typeof template.serverId !== "string" ||
    typeof template.displayName !== "string" ||
    !["low", "medium", "high"].includes(String(template.risk)) ||
    typeof transport.type !== "string"
  ) return null;
  const credentials = recordArray(template.credentials).map((item) => ({
    name: String(item.name ?? ""),
    label: String(item.label ?? item.name ?? "Credential"),
    required: item.required !== false,
  })).filter((item) => item.name);
  return {
    serverId: template.serverId,
    displayName: template.displayName,
    presentation: starter.presentation,
    risk: template.risk as StarterMcpTemplate["risk"],
    credentials,
    transport,
  };
}

function starterBindingLines(bindings: unknown): string {
  const values = asRecord(bindings);
  return Object.entries(values).map(([name, raw]) => {
    const binding = asRecord(raw);
    if (binding.kind === "secret_input") return `${name}=@secret:${String(binding.field ?? name)}`;
    return `${name}=${String(binding.value ?? "")}`;
  }).join("\n");
}

/** Dense, task-oriented control surface for all four OpenPPX extension domains. */
export function ExtensionsSettings(props: ExtensionsSettingsProps) {
  const tab = props.kind;
  const [query, setQuery] = useState("");
  const [detail, setDetail] = useState<ExtensionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [sourceDialog, setSourceDialog] = useState<SourceDialogState | null>(null);
  const [mcpDialog, setMcpDialog] = useState<McpDialogState | null>(null);
  const [appConnectionDialog, setAppConnectionDialog] = useState<AppConnectionDialogState | null>(null);
  const [starters, setStarters] = useState<ExtensionStarter[]>([]);
  const [startersLoading, setStartersLoading] = useState(false);
  const [starterDetail, setStarterDetail] = useState<ExtensionStarter | null>(null);
  const [starterMutationId, setStarterMutationId] = useState<string | null>(null);
  const [showAllStarters, setShowAllStarters] = useState(false);
  const [marketplaces, setMarketplaces] = useState<PluginMarketplaceSource[]>([]);
  const [marketplaceEntries, setMarketplaceEntries] = useState<PluginMarketplaceEntry[]>([]);
  const [marketplaceLoading, setMarketplaceLoading] = useState(false);
  const [marketplaceDialog, setMarketplaceDialog] = useState<MarketplaceDialogState | null>(null);

  const counts = useMemo(() => Object.fromEntries(KIND_ORDER.map((kind) => [
    kind,
    props.extensions.filter((item) => item.kind === kind).length,
  ])) as Record<ExtensionSummary["kind"], number>, [props.extensions]);
  const filtered = useMemo(() => props.extensions.filter((item) => {
    if (item.kind !== tab) return false;
    const needle = query.trim().toLowerCase();
    return !needle || `${item.displayName} ${item.description} ${item.id}`.toLowerCase().includes(needle);
  }), [props.extensions, query, tab]);
  const configured = useMemo(() => filtered.filter(isConfiguredExtension), [filtered]);
  const availableApps = useMemo(
    () => tab === "app" ? filtered.filter((item) => !isConfiguredExtension(item)) : [],
    [filtered, tab],
  );
  const visibleAvailableAppIds = useMemo(
    () => new Set(availableApps.map((item) => item.id)),
    [availableApps],
  );
  const filteredStarters = useMemo(() => starters.filter((item) => {
    const needle = query.trim().toLowerCase();
    const matchesQuery = !needle || `${item.displayName} ${item.description} ${item.category} ${item.developer} ${item.requirements.join(" ")}`.toLowerCase().includes(needle);
    const installedAppId = starterAppDefinitionId(item);
    return matchesQuery && (!installedAppId || !visibleAvailableAppIds.has(installedAppId));
  }), [query, starters, visibleAvailableAppIds]);
  const visibleStarters = useMemo(
    () => filteredStarters.slice(0, showAllStarters || query.trim() ? undefined : STARTER_FOLD),
    [filteredStarters, query, showAllStarters],
  );

  useEffect(() => {
    let active = true;
    setStartersLoading(true);
    setLocalError(null);
    window.ppxClient.listExtensionStarters(tab)
      .then((result) => { if (active) setStarters(result.starters); })
      .catch((error) => { if (active) setLocalError(errorMessage(error)); })
      .finally(() => { if (active) setStartersLoading(false); });
    return () => { active = false; };
  }, [tab]);

  async function loadPluginMarketplaces(): Promise<void> {
    setMarketplaceLoading(true);
    try {
      const [sourceResult, entryResult] = await Promise.all([
        window.ppxClient.listPluginMarketplaces(),
        window.ppxClient.listPluginMarketplaceEntries(),
      ]);
      setMarketplaces(sourceResult.marketplaces);
      setMarketplaceEntries(entryResult.entries);
    } catch (error) {
      setLocalError(errorMessage(error));
    } finally {
      setMarketplaceLoading(false);
    }
  }

  useEffect(() => {
    if (tab === "plugin") void loadPluginMarketplaces();
  }, [tab]);

  useEffect(() => {
    setShowAllStarters(false);
  }, [tab]);

  async function openDetail(extension: ExtensionSummary): Promise<void> {
    setDetailLoading(true);
    setLocalError(null);
    try {
      setDetail((await window.ppxClient.getExtension(extension.kind, extension.id)).extension);
    } catch (error) {
      setLocalError(errorMessage(error));
    } finally {
      setDetailLoading(false);
    }
  }

  async function openAvailableApp(extension: ExtensionSummary): Promise<void> {
    setDetailLoading(true);
    setLocalError(null);
    try {
      const app = (await window.ppxClient.getExtension("app", extension.id)).extension;
      setAppConnectionDialog({ app, connection: null });
    } catch (error) {
      setLocalError(errorMessage(error));
    } finally {
      setDetailLoading(false);
    }
  }

  async function refreshDetail(kind: ExtensionSummary["kind"], extensionId: string): Promise<void> {
    props.onRefresh();
    try {
      setDetail((await window.ppxClient.getExtension(kind, extensionId)).extension);
    } catch {
      setDetail(null);
    }
  }

  function addForTab(): void {
    if (tab === "plugin" || tab === "skill") setSourceDialog({ kind: tab, extension: null, starter: null });
    if (tab === "mcp") setMcpDialog({ extension: null, starter: null });
  }

  async function openStarter(starter: ExtensionStarter): Promise<void> {
    if (starter.installMode === "direct_app") {
      setStarterMutationId(starter.id);
      setLocalError(null);
      try {
        const installed = await window.ppxClient.installAppStarter(starter.id);
        props.onRefresh();
        const appId = String(installed.id ?? "");
        if (!appId) throw new Error("The App starter did not return an installed App identity.");
        const app = (await window.ppxClient.getExtension("app", appId)).extension;
        setAppConnectionDialog({ app, connection: null });
      } catch (error) {
        setLocalError(errorMessage(error));
      } finally {
        setStarterMutationId(null);
      }
      return;
    }
    if (starter.installMode === "direct_mcp") {
      setMcpDialog({ extension: null, starter });
      return;
    }
    if (starter.installMode === "source" && (starter.kind === "plugin" || starter.kind === "skill")) {
      setSourceDialog({ kind: starter.kind, extension: null, starter });
      return;
    }
    setStarterDetail(starter);
  }

  function openMarketplaceEntry(entry: PluginMarketplaceEntry): void {
    if (!entry.source) return;
    setSourceDialog({
      kind: "plugin",
      extension: null,
      starter: {
        id: `${entry.marketplaceId}-${entry.pluginId}`,
        kind: "plugin",
        runtimeKind: "plugin",
        displayName: entry.displayName,
        description: entry.description,
        category: entry.category,
        developer: entry.developer,
        availability: "ready",
        installMode: "source",
        auth: "none",
        requirements: [],
        note: `From ${entry.marketplaceId}. Preview verifies the exact package before installation.`,
        featured: false,
        provenance: { project: entry.marketplaceId },
        presentation: { icon: "plugin", brandColor: null },
        template: { source: entry.source },
      },
    });
  }

  const canAdd = tab === "plugin" || tab === "skill" || tab === "mcp";

  return (
    <section className="extensions-console" aria-label="Extensions settings">
      <header className="extensions-console-header">
        <div>
          <h3>{plural(tab)}</h3>
          <p>Add capabilities to this Node, then choose which Agents can use them.</p>
        </div>
        <div className="extensions-header-actions">
          <button className="secondary settings-quiet-button" onClick={props.onRefresh} disabled={props.loading}>{props.loading ? "Refreshing" : "Refresh"}</button>
          {canAdd ? <button onClick={addForTab}>Add {tab === "mcp" ? "MCP server" : title(tab)}</button> : null}
        </div>
      </header>

      {props.error || localError ? <p className="settings-inline-error">{localError ?? props.error}</p> : null}

      <div className="extension-browser">
          <div className="extension-toolbar">
            <label className="extension-search"><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Search ${plural(tab).toLowerCase()}`} /></label>
            <p>{tab === "app" ? "App definitions come from trusted Plugins or the Node. Open an App to manage connections." : `${counts[tab]} configured on this Node.`}</p>
          </div>
          {configured.length ? (
            <div className="extension-resource-group">
              <div className="extension-group-label">Configured · {configured.length}</div>
              <div className="extension-resource-list">
                {configured.map((extension) => {
                  const enabled = props.selectedAgentId ? extension.enabledAgentIds.includes(props.selectedAgentId) : false;
                  const pending = props.mutationId === `${extension.kind}:${extension.id}`;
                  return (
                    <article className="extension-resource-row" key={`${extension.kind}:${extension.id}`}>
                      <button className="extension-resource-open" onClick={() => void openDetail(extension)}>
                        <ExtensionIcon presentation={extension.presentation} kind={extension.kind} label={extension.displayName} />
                        <span className="extension-resource-copy"><strong>{extension.displayName}</strong><span>{extension.description || `Open ${title(extension.kind)} details`}</span><small>{sourceDescription(extension)}</small></span>
                      </button>
                      <span className={`extension-state ${extension.readiness.ready ? "ready" : "blocked"}`}><i />{extension.readiness.ready ? extension.status : "Needs attention"}</span>
                      {extension.kind !== "app" ? (
                        <button className="secondary extension-row-action" disabled={!props.selectedAgentId || extension.status === "builtin" || pending} onClick={() => props.onSetEnabled(extension, !enabled)}>
                          {pending ? "Applying" : extension.status === "builtin" ? "Built in" : enabled ? "Disable" : "Enable"}
                        </button>
                      ) : <button className="secondary extension-row-action" onClick={() => void openDetail(extension)}>Connections</button>}
                    </article>
                  );
                })}
              </div>
            </div>
          ) : null}
      </div>

      {tab === "plugin" ? (
        <section className="plugin-marketplace-section" aria-label="Plugin marketplaces">
          <header>
            <div><h4>Marketplaces</h4><p>Add local or Git catalogs. Packages still require preview and exact-digest confirmation.</p></div>
            <button className="secondary" onClick={() => setMarketplaceDialog({ marketplace: null })}>Add source</button>
          </header>
          {marketplaces.length ? <div className="plugin-marketplace-sources">{marketplaces.map((marketplace) => (
            <article key={marketplace.id}>
              <button className="plugin-marketplace-open" onClick={() => setMarketplaceDialog({ marketplace })}>
                <strong>{marketplace.displayName}</strong>
                <span>{marketplace.type === "git" ? `${marketplace.locator} · ${marketplace.ref}` : marketplace.locator}</span>
                <small>{marketplace.ready ? `${marketplace.entryCount} packages · refreshed ${marketplace.refreshedAt ? new Date(marketplace.refreshedAt).toLocaleString() : "now"}` : "Refresh required"}</small>
              </button>
              <button className="secondary" disabled={marketplaceLoading} onClick={() => void (async () => {
                try { await window.ppxClient.refreshPluginMarketplace(marketplace.id, marketplace.revision); await loadPluginMarketplaces(); }
                catch (error) { setLocalError(errorMessage(error)); }
              })()}>{marketplaceLoading ? "Working…" : "Refresh"}</button>
            </article>
          ))}</div> : <div className="extension-empty-state compact"><span>No marketplace sources</span><p>Add a repository or local catalog to discover portable Plugins.</p></div>}
          {marketplaceEntries.length ? <div className="plugin-marketplace-entries">{marketplaceEntries.map((entry) => (
            <article key={`${entry.marketplaceId}:${entry.pluginId}`}>
              <ExtensionIcon presentation={{ icon: "plugin", brandColor: null }} kind="plugin" label={entry.displayName} />
              <div><strong>{entry.displayName}</strong><span>{entry.description}</span><small>{entry.developer} · {entry.sourceKind}</small></div>
              <button className="secondary" disabled={!entry.ready || !entry.source} onClick={() => openMarketplaceEntry(entry)}>{entry.ready ? "Preview" : "Unavailable"}</button>
            </article>
          ))}</div> : null}
        </section>
      ) : null}

      <section className="extension-starter-section" aria-label={`Available ${plural(tab)}`}>
        <div className="extension-starter-content">
          <div className="extension-starter-heading">
            <h4>Available</h4>
          </div>
          {availableApps.length || visibleStarters.length ? (
            <>
            <div className="extension-starter-list">
              {availableApps.map((extension) => (
                <article className="extension-starter-row" key={`available:${extension.id}`}>
                  <button className="extension-starter-copy" onClick={() => void openAvailableApp(extension)}>
                    <ExtensionIcon presentation={extension.presentation} kind={extension.kind} label={extension.displayName} />
                    <span><strong>{extension.displayName}</strong><small>{extension.description}</small></span>
                  </button>
                  <button className="secondary extension-starter-action" disabled={detailLoading} onClick={() => void openAvailableApp(extension)}>{detailLoading ? "Opening…" : "Connect"}</button>
                </article>
              ))}
              {visibleStarters.map((starter) => {
                const listStatus = starterListStatus(starter);
                return (
                  <article className="extension-starter-row" key={starter.id}>
                    <button className="extension-starter-copy" onClick={() => void openStarter(starter)}>
                      <ExtensionIcon presentation={starter.presentation} kind={starter.kind} label={starter.displayName} />
                      <span><strong>{starter.displayName}</strong><small>{starter.description}</small></span>
                    </button>
                    {listStatus ? <span className={`starter-availability ${starter.availability}`}><i />{listStatus}</span> : null}
                    <button className="secondary extension-starter-action" disabled={starterMutationId === starter.id} onClick={() => void openStarter(starter)}>{starterMutationId === starter.id ? "Installing…" : starterButtonLabel(starter)}</button>
                  </article>
                );
              })}
            </div>
            {!query.trim() && !showAllStarters && filteredStarters.length > visibleStarters.length ? (
              <div className="extension-show-all">
                <span>{filteredStarters.length - visibleStarters.length} more · </span>
                <button aria-label={`Show all ${filteredStarters.length}`} onClick={() => setShowAllStarters(true)}>show all</button>
              </div>
            ) : null}
            </>
          ) : startersLoading
            ? <div className="extension-starter-loading">Loading catalog…</div>
            : <div className="extension-starter-loading">No available starters match this search.</div>}
        </div>
      </section>

      {detailLoading ? <div className="extension-detail-loading">Loading extension…</div> : null}
      {detail ? (
        <ExtensionDetailDialog
          detail={detail}
          selectedAgentId={props.selectedAgentId}
          onClose={() => setDetail(null)}
          onEditSource={() => {
            if (detail.kind === "plugin" || detail.kind === "skill") setSourceDialog({ kind: detail.kind, extension: detail, starter: null });
            setDetail(null);
          }}
          onEditMcp={() => { setMcpDialog({ extension: detail, starter: null }); setDetail(null); }}
          onEditConnection={(connection) => { setAppConnectionDialog({ app: detail, connection }); setDetail(null); }}
          onAddConnection={() => { setAppConnectionDialog({ app: detail, connection: null }); setDetail(null); }}
          onSetEnabled={(enabled) => { props.onSetEnabled(detail, enabled); setDetail(null); }}
          onRemove={async () => {
            await window.ppxClient.removeExtension({ kind: detail.kind as "plugin" | "mcp" | "skill", extensionId: detail.id, expectedRevision: detail.revision });
            setDetail(null);
            props.onRefresh();
          }}
          onConnectionEnabled={async (connection, enabled) => {
            if (!props.selectedAgentId) return;
            await window.ppxClient.setAppConnectionAgentEnabled({ connectionId: connection.id, agentId: props.selectedAgentId, expectedRevision: connection.revision, enabled });
            await refreshDetail("app", detail.id);
          }}
          onRemoveConnection={async (connection) => {
            await window.ppxClient.removeAppConnection({ connectionId: connection.id, expectedRevision: connection.revision });
            await refreshDetail("app", detail.id);
          }}
        />
      ) : null}
      {sourceDialog ? <ExtensionSourceDialog state={sourceDialog} onClose={() => setSourceDialog(null)} onSaved={() => { setSourceDialog(null); props.onRefresh(); }} /> : null}
      {mcpDialog ? <McpServerDialog state={mcpDialog} agents={props.agents} initialAgentId={props.selectedAgentId} onClose={() => setMcpDialog(null)} onSaved={() => { setMcpDialog(null); props.onRefresh(); }} /> : null}
      {appConnectionDialog ? <AppConnectionDialog state={appConnectionDialog} onClose={() => setAppConnectionDialog(null)} onSaved={() => { setAppConnectionDialog(null); props.onRefresh(); }} /> : null}
      {starterDetail ? <StarterDetailDialog starter={starterDetail} onClose={() => setStarterDetail(null)} /> : null}
      {marketplaceDialog ? <PluginMarketplaceDialog state={marketplaceDialog} onClose={() => setMarketplaceDialog(null)} onSaved={() => { setMarketplaceDialog(null); void loadPluginMarketplaces(); }} onRemoved={() => { setMarketplaceDialog(null); void loadPluginMarketplaces(); }} /> : null}
    </section>
  );
}

function PluginMarketplaceDialog(props: {
  state: MarketplaceDialogState;
  onClose: () => void;
  onSaved: () => void;
  onRemoved: () => void;
}) {
  const existing = props.state.marketplace;
  const [marketplaceId, setMarketplaceId] = useState(existing?.id ?? "");
  const [displayName, setDisplayName] = useState(existing?.displayName ?? "");
  const [sourceType, setSourceType] = useState<PluginMarketplaceSourceSpec["type"]>(existing?.type ?? "git");
  const [locator, setLocator] = useState(existing?.locator ?? "");
  const [ref, setRef] = useState(existing?.ref ?? "HEAD");
  const [working, setWorking] = useState(false);
  const [removeArmed, setRemoveArmed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault(); setWorking(true); setError(null);
    try {
      await window.ppxClient.savePluginMarketplace({
        marketplaceId: marketplaceId.trim(),
        spec: { displayName: displayName.trim(), type: sourceType, locator: locator.trim(), ref: sourceType === "git" ? ref.trim() || "HEAD" : "HEAD" },
        expectedRevision: existing?.revision ?? null,
      });
      props.onSaved();
    } catch (nextError) { setError(errorMessage(nextError)); }
    finally { setWorking(false); }
  }

  async function remove(): Promise<void> {
    if (!existing) return;
    setWorking(true); setError(null);
    try { await window.ppxClient.removePluginMarketplace(existing.id, existing.revision); props.onRemoved(); }
    catch (nextError) { setError(errorMessage(nextError)); setWorking(false); }
  }

  return (
    <DialogShell title={existing ? `Edit ${existing.displayName}` : "Add marketplace"} eyebrow="Plugin marketplace" description="Catalog metadata is cached by the Node. Installing an entry still uses the normal preview and confirmation boundary." busy={working} onClose={props.onClose}>
      <form onSubmit={(event) => void save(event)}>
        <div className="agent-dialog-fields extension-dialog-fields">
          <label><span>Source ID</span><input value={marketplaceId} onChange={(event) => setMarketplaceId(event.target.value)} disabled={Boolean(existing)} placeholder="team-plugins" spellCheck={false} /></label>
          <label><span>Name</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Team Plugins" /></label>
          <label><span>Source</span><select value={sourceType} onChange={(event) => setSourceType(event.target.value as PluginMarketplaceSourceSpec["type"])}><option value="git">Git repository</option><option value="local">Local directory</option></select></label>
          <label><span>{sourceType === "git" ? "Repository URL" : "Directory"}</span><input value={locator} onChange={(event) => setLocator(event.target.value)} placeholder={sourceType === "git" ? "https://github.com/org/plugins" : "/path/to/marketplace"} spellCheck={false} /></label>
          {sourceType === "git" ? <label className="agent-dialog-full-row"><span>Git ref</span><input value={ref} onChange={(event) => setRef(event.target.value)} placeholder="main, tag, or commit" spellCheck={false} /><small>The Node resolves this ref to one immutable commit during refresh.</small></label> : null}
        </div>
        {error ? <p className="agent-dialog-error">{error}</p> : null}
        <footer className="agent-dialog-actions">
          {existing ? removeArmed ? <button type="button" className="danger-button" disabled={working} onClick={() => void remove()}>Confirm remove</button> : <button type="button" className="secondary danger-quiet" disabled={working} onClick={() => setRemoveArmed(true)}>Remove source</button> : null}
          <span />
          <button type="button" className="agent-dialog-cancel" onClick={props.onClose} disabled={working}>Cancel</button>
          <button className="agent-dialog-create" disabled={working || !marketplaceId.trim() || !displayName.trim() || !locator.trim()}>{working ? "Saving…" : existing ? "Save changes" : "Add source"}</button>
        </footer>
      </form>
    </DialogShell>
  );
}

function StarterDetailDialog(props: { starter: ExtensionStarter; onClose: () => void }) {
  const sourceUrl = typeof props.starter.provenance.sourceUrl === "string" ? props.starter.provenance.sourceUrl : "";
  return (
    <DialogShell title={props.starter.displayName} eyebrow={`${title(props.starter.kind)} starter`} description={props.starter.description} busy={false} onClose={props.onClose}>
      <section className="starter-detail">
        <div className="starter-detail-status"><span className={`starter-availability ${props.starter.availability}`}><i />{starterStatus(props.starter)}</span><span>{props.starter.runtimeKind === props.starter.kind ? title(props.starter.kind) : `${title(props.starter.runtimeKind)}-backed`}</span></div>
        {props.starter.requirements.length ? <div><h4>Requirements</h4><ul>{props.starter.requirements.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
        {props.starter.note ? <div><h4>Availability</h4><p>{props.starter.note}</p></div> : null}
        <div><h4>Source</h4><p>{props.starter.provenance.project ?? props.starter.developer} · {props.starter.provenance.license ?? "External"}</p></div>
      </section>
      <footer className="agent-dialog-actions"><button type="button" className="agent-dialog-cancel" onClick={props.onClose}>Close</button>{sourceUrl ? <button type="button" className="agent-dialog-create" onClick={() => void window.ppxClient.openExternalUrl(sourceUrl)}>Open source</button> : null}</footer>
    </DialogShell>
  );
}

function ExtensionSourceDialog(props: { state: SourceDialogState; onClose: () => void; onSaved: () => void }) {
  const initialSource = asRecord(asRecord(props.state.starter?.template).source);
  const [sourceType, setSourceType] = useState<ExtensionSourceRef["type"]>((initialSource.type as ExtensionSourceRef["type"] | undefined) ?? "git");
  const [locator, setLocator] = useState(String(initialSource.locator ?? ""));
  const [version, setVersion] = useState(String(initialSource.version ?? ""));
  const [revision, setRevision] = useState(String(initialSource.revision ?? ""));
  const [provider, setProvider] = useState(String(initialSource.provider ?? ""));
  const [subpath, setSubpath] = useState(String(initialSource.subpath ?? ""));
  const [preview, setPreview] = useState<ExtensionPreview | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const source: ExtensionSourceRef = {
    type: sourceType,
    locator: locator.trim(),
    ...(version.trim() ? { version: version.trim() } : {}),
    ...(revision.trim() ? { revision: revision.trim() } : {}),
    ...(provider.trim() ? { provider: provider.trim() } : {}),
    ...(subpath.trim() ? { subpath: subpath.trim() } : {}),
  };
  const previewData = asRecord(preview?.preview);

  async function runPreview(): Promise<void> {
    setWorking(true); setError(null); setPreview(null);
    try { setPreview(await window.ppxClient.previewExtension({ kind: props.state.kind, source })); }
    catch (nextError) { setError(errorMessage(nextError)); }
    finally { setWorking(false); }
  }

  async function install(): Promise<void> {
    if (!preview) return;
    setWorking(true); setError(null);
    try {
      await window.ppxClient.installExtension({ kind: props.state.kind, source, expectedDigest: String(preview.preview.digest), expectedRevision: props.state.extension?.revision ?? null });
      props.onSaved();
    } catch (nextError) { setError(errorMessage(nextError)); }
    finally { setWorking(false); }
  }

  return (
    <DialogShell title={props.state.extension ? `Update ${props.state.extension.displayName}` : props.state.starter ? `Add ${props.state.starter.displayName}` : `Add ${title(props.state.kind)}`} eyebrow={`${title(props.state.kind)} source`} description={props.state.starter?.note || "Preview the exact artifact and risk before the Node installs it."} busy={working} onClose={props.onClose}>
      <div className="agent-dialog-fields extension-dialog-fields">
        <label><span>Source</span><select value={sourceType} onChange={(event) => { setSourceType(event.target.value as ExtensionSourceRef["type"]); setPreview(null); }}><option value="git">Git repository</option><option value="npm">npm package</option><option value="local_directory">Local directory</option><option value="local_archive">Local archive</option><option value="catalog">Catalog</option></select></label>
        <label><span>{sourceType === "git" ? "Repository URL" : sourceType === "npm" ? "Package" : sourceType === "catalog" ? "Catalog entry" : "Path"}</span><input value={locator} onChange={(event) => { setLocator(event.target.value); setPreview(null); }} placeholder={sourceType === "git" ? "https://github.com/org/repository" : sourceType === "npm" ? "@scope/plugin" : sourceType === "catalog" ? "publisher/package" : "/path/to/extension"} spellCheck={false} /></label>
        <label><span>Version</span><input value={version} onChange={(event) => { setVersion(event.target.value); setPreview(null); }} placeholder={sourceType === "npm" ? "Required exact version, e.g. 1.2.3" : "Optional release"} /></label>
        <label><span>Revision</span><input value={revision} onChange={(event) => { setRevision(event.target.value); setPreview(null); }} placeholder="Optional commit or tag" /></label>
        <details className="agent-dialog-full-row extension-advanced"><summary>Advanced source options</summary><div className="extension-inline-fields"><label><span>Provider</span><input value={provider} onChange={(event) => { setProvider(event.target.value); setPreview(null); }} placeholder="Optional" /></label><label><span>Subpath</span><input value={subpath} onChange={(event) => { setSubpath(event.target.value); setPreview(null); }} placeholder="Optional folder" /></label></div></details>
      </div>
      {preview ? <section className="extension-preview-card"><div><span>Verified preview</span><strong>{String(previewData.displayName ?? previewData.name ?? props.state.extension?.displayName ?? title(props.state.kind))}</strong></div><dl><div><dt>Risk</dt><dd>{String(preview.preview.risk)}</dd></div><div><dt>Digest</dt><dd><code>{String(preview.preview.digest).slice(0, 18)}…</code></dd></div></dl><p>{String(previewData.description ?? "The source was resolved. Confirm to install this exact digest.")}</p></section> : null}
      {error ? <p className="agent-dialog-error">{error}</p> : null}
      <footer className="agent-dialog-actions"><button type="button" className="agent-dialog-cancel" onClick={props.onClose} disabled={working}>Cancel</button>{preview ? <button type="button" className="agent-dialog-create" onClick={() => void install()} disabled={working}>{working ? "Installing…" : props.state.extension ? "Install update" : "Install"}</button> : <button type="button" className="agent-dialog-create" onClick={() => void runPreview()} disabled={working || !locator.trim()}>{working ? "Checking…" : "Preview"}</button>}</footer>
    </DialogShell>
  );
}

function McpServerDialog(props: { state: McpDialogState; agents: AgentProfile[]; initialAgentId: string; onClose: () => void; onSaved: () => void }) {
  const existing = props.state.extension;
  const resource = asMcpResource(existing?.details.resource);
  const starter = starterMcpTemplate(props.state.starter);
  const starterTransport = starter?.transport;
  const initialTransport = resource?.spec.transport;
  const initialTransportType = initialTransport?.type ?? (starterTransport?.type as McpServerResource["spec"]["transport"]["type"] | undefined) ?? "stdio";
  const [serverId, setServerId] = useState(resource?.metadata.name ?? starter?.serverId ?? "");
  const [displayName, setDisplayName] = useState(resource?.spec.displayName ?? starter?.displayName ?? "");
  const [description, setDescription] = useState(resource?.spec.description ?? props.state.starter?.description ?? "");
  const [transportType, setTransportType] = useState<McpServerResource["spec"]["transport"]["type"]>(initialTransportType);
  const [command, setCommand] = useState(initialTransport?.type === "stdio" ? initialTransport.command : String(starterTransport?.command ?? ""));
  const [args, setArgs] = useState(initialTransport?.type === "stdio" ? initialTransport.args.join("\n") : Array.isArray(starterTransport?.args) ? starterTransport.args.map(String).join("\n") : "");
  const [cwd, setCwd] = useState(initialTransport?.type === "stdio" ? initialTransport.cwd ?? "" : String(starterTransport?.cwd ?? ""));
  const [url, setUrl] = useState(
    initialTransport && initialTransport.type !== "stdio"
      ? initialTransport.url
      : String(starterTransport?.url ?? ""),
  );
  const [authMode, setAuthMode] = useState<"none" | "oauth">(
    initialTransport && initialTransport.type !== "stdio"
      ? initialTransport.auth ?? "none"
      : starterTransport?.auth === "oauth" ? "oauth" : "none",
  );
  const [bindings, setBindings] = useState(initialTransport
    ? bindingLines(initialTransport.type === "stdio" ? initialTransport.environment : initialTransport.headers)
    : starterBindingLines(initialTransportType === "stdio" ? starterTransport?.environment : starterTransport?.headers));
  const [queryBindings, setQueryBindings] = useState(
    initialTransport && initialTransport.type !== "stdio"
      ? bindingLines(initialTransport.query ?? {})
      : starterBindingLines(starterTransport?.query),
  );
  const [secretValues, setSecretValues] = useState("");
  const [starterSecretValues, setStarterSecretValues] = useState<Record<string, string>>({});
  const [toolFilter, setToolFilter] = useState(resource?.spec.policy.toolFilter.join("\n") ?? "");
  const [toolPrefix, setToolPrefix] = useState(resource?.spec.policy.toolNamePrefix ?? "");
  const [requireConfirmation, setRequireConfirmation] = useState(resource?.spec.policy.requireConfirmation ?? starter?.risk !== "low");
  const [progressEvents, setProgressEvents] = useState(resource?.spec.policy.progressEvents ?? true);
  const [longTaskProxy, setLongTaskProxy] = useState(resource?.spec.policy.longTaskProxy ?? true);
  const [inlineBudgetMs, setInlineBudgetMs] = useState(String(resource?.spec.policy.inlineBudgetMs ?? 1500));
  const [risk, setRisk] = useState<McpServerResource["spec"]["risk"]>(resource?.spec.risk ?? starter?.risk ?? "medium");
  const [enabledAgentIds, setEnabledAgentIds] = useState<string[]>(resource?.spec.enabledAgentIds ?? (props.initialAgentId ? [props.initialAgentId] : []));
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [oauthStatus, setOauthStatus] = useState<McpOAuthStatus | null>(null);
  const openedOAuthUrl = useRef("");
  const [pendingOAuth, setPendingOAuth] = useState<{
    serverId: string;
    revision: string;
    currentAgentIds: string[];
    desiredAgentIds: string[];
  } | null>(null);

  useEffect(() => {
    if (!pendingOAuth) return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const reconcileAndFinish = async (status: McpOAuthStatus): Promise<void> => {
      setOauthStatus(status);
      if (status.authorizeUrl && openedOAuthUrl.current !== status.authorizeUrl) {
        openedOAuthUrl.current = status.authorizeUrl;
        await window.ppxClient.openExternalUrl(status.authorizeUrl);
      }
      if (status.status === "error") {
        setPendingOAuth(null);
        setError(status.error || "Sign-in could not be completed.");
        return;
      }
      if (status.status !== "connected") {
        timer = setTimeout(() => void poll(), 1_000);
        return;
      }
      let revision = pendingOAuth.revision;
      const current = new Set(pendingOAuth.currentAgentIds);
      const desired = new Set(pendingOAuth.desiredAgentIds);
      for (const agentId of pendingOAuth.currentAgentIds.filter((item) => !desired.has(item))) {
        const result = await window.ppxClient.setExtensionAgentEnabled({
          kind: "mcp", extensionId: pendingOAuth.serverId, agentId, expectedRevision: revision, enabled: false,
        });
        revision = result.revision;
      }
      for (const agentId of pendingOAuth.desiredAgentIds.filter((item) => !current.has(item))) {
        const result = await window.ppxClient.setExtensionAgentEnabled({
          kind: "mcp", extensionId: pendingOAuth.serverId, agentId, expectedRevision: revision, enabled: true,
        });
        revision = result.revision;
      }
      if (!cancelled) props.onSaved();
    };
    const poll = async (): Promise<void> => {
      try {
        const status = await window.ppxClient.getMcpOAuthStatus(pendingOAuth.serverId);
        if (!cancelled) await reconcileAndFinish(status);
      } catch (nextError) {
        if (!cancelled) {
          setPendingOAuth(null);
          setError(errorMessage(nextError));
        }
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [pendingOAuth, props.onSaved]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault(); setWorking(true); setError(null);
    try {
      const parsedBindings = parseBindings(bindings, transportType === "stdio" ? "Environment" : "Headers");
      const parsedQueryBindings = transportType === "stdio" ? {} : parseBindings(queryBindings, "Query parameters");
      const transport: McpServerResource["spec"]["transport"] = transportType === "stdio"
        ? { type: "stdio", command: command.trim(), args: splitLines(args), cwd: cwd.trim() || null, environment: parsedBindings }
        : { type: transportType, url: url.trim(), headers: parsedBindings, query: parsedQueryBindings, auth: authMode };
      const nextResource: McpServerResource = {
        apiVersion: "openppx.io/v1alpha1", kind: "McpServer", metadata: { name: serverId.trim(), labels: resource?.metadata.labels ?? {}, annotations: resource?.metadata.annotations ?? {} },
        spec: { displayName: displayName.trim(), description: description.trim(), presentation: resource?.spec.presentation ?? starter?.presentation ?? { icon: "mcp", brandColor: null }, transport, policy: { toolFilter: splitLines(toolFilter), toolNamePrefix: toolPrefix.trim() || null, requireConfirmation, runtimeHeaders: resource?.spec.policy.runtimeHeaders ?? {}, progressEvents, longTaskProxy, inlineBudgetMs: Number(inlineBudgetMs), jobProtocol: resource?.spec.policy.jobProtocol ?? null }, risk, enabledAgentIds, managedBy: null },
      };
      const resolvedSecretValues = starter
        ? Object.fromEntries(Object.entries(starterSecretValues).filter(([, value]) => value.trim()))
        : parseKeyValueLines(secretValues, "Secret values");
      for (const credential of starter?.credentials ?? []) {
        if (credential.required && !resolvedSecretValues[credential.name]) throw new Error(`${credential.label} is required.`);
      }
      const input = { resource: nextResource, secretValues: resolvedSecretValues, expectedRevision: existing?.revision ?? null };
      const saved = existing
        ? await window.ppxClient.updateMcpServer(input)
        : await window.ppxClient.createMcpServer(input);
      setSecretValues(""); setStarterSecretValues({});
      if (transportType !== "stdio" && authMode === "oauth") {
        const status = await window.ppxClient.getMcpOAuthStatus(nextResource.metadata.name);
        const nextStatus = status.status === "connected"
          ? status
          : await window.ppxClient.beginMcpOAuth(nextResource.metadata.name);
        if (nextStatus.authorizeUrl) {
          openedOAuthUrl.current = nextStatus.authorizeUrl;
          await window.ppxClient.openExternalUrl(nextStatus.authorizeUrl);
        }
        setOauthStatus(nextStatus);
        setPendingOAuth({
          serverId: nextResource.metadata.name,
          revision: String(saved.revision ?? existing?.revision ?? ""),
          currentAgentIds: resource?.spec.enabledAgentIds ?? [],
          desiredAgentIds: enabledAgentIds,
        });
        return;
      }
      props.onSaved();
    } catch (nextError) { setError(errorMessage(nextError)); }
    finally { setWorking(false); }
  }

  return (
    <DialogShell title={existing ? `Edit ${existing.displayName}` : props.state.starter ? `Set up ${props.state.starter.displayName}` : "Add MCP server"} eyebrow={props.state.starter?.kind === "app" ? "MCP-backed App" : "Direct MCP"} description={props.state.starter?.note || "Configure a local process or remote server. Secret values are write-only."} busy={working} onClose={props.onClose} wide>
      <form onSubmit={(event) => void submit(event)}>
        <div className="agent-dialog-fields extension-dialog-fields">
          <label><span>Server ID</span><input value={serverId} onChange={(event) => setServerId(event.target.value)} disabled={Boolean(existing)} placeholder="github-tools" spellCheck={false} /><small>{existing ? "Stable Node identifier." : "Lowercase letters, numbers, and hyphens."}</small></label>
          <label><span>Name</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="GitHub tools" /></label>
          <label className="agent-dialog-full-row"><span>Description</span><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What this server lets Agents do" /></label>
          <label><span>Transport</span><select value={transportType} onChange={(event) => setTransportType(event.target.value as typeof transportType)}><option value="stdio">Local process (stdio)</option><option value="streamable_http">Streamable HTTP</option><option value="sse">Server-sent events</option></select></label>
          <label><span>Risk</span><select value={risk} onChange={(event) => setRisk(event.target.value as typeof risk)}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
          {transportType === "stdio" ? <><label className="agent-dialog-full-row"><span>Command</span><input value={command} onChange={(event) => setCommand(event.target.value)} placeholder="npx" spellCheck={false} /></label><label><span>Arguments</span><textarea value={args} onChange={(event) => setArgs(event.target.value)} placeholder={"-y\n@modelcontextprotocol/server-filesystem"} /></label><label><span>Working directory</span><input value={cwd} onChange={(event) => setCwd(event.target.value)} placeholder="Optional" spellCheck={false} /></label></> : <><label><span>Server URL</span><input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://mcp.example.com" spellCheck={false} /></label><label><span>Authentication</span><select value={authMode} onChange={(event) => setAuthMode(event.target.value as "none" | "oauth")}><option value="none">None or credentials below</option><option value="oauth">Sign in with OAuth</option></select></label></>}
          <label><span>{transportType === "stdio" ? "Environment" : "Headers"}</span><textarea value={bindings} onChange={(event) => setBindings(event.target.value)} placeholder={transportType === "stdio" ? "MODE=production\nTOKEN=@secret:api-token" : "Authorization=@secret:api-token"} /><small>Use NAME=value or NAME=@secret:alias.</small></label>
          {transportType !== "stdio" ? <label><span>Query parameters</span><textarea value={queryBindings} onChange={(event) => setQueryBindings(event.target.value)} placeholder="apiKey=@secret:api-key" /><small>Protected values are resolved only when the Node connects.</small></label> : null}
          {starter?.credentials.length ? starter.credentials.map((credential) => <label key={credential.name}><span>{credential.label}</span><input type="password" value={starterSecretValues[credential.name] ?? ""} onChange={(event) => setStarterSecretValues((current) => ({ ...current, [credential.name]: event.target.value }))} autoComplete="new-password" required={credential.required} /><small>Stored by the Node and never returned.</small></label>) : authMode !== "oauth" ? <label><span>New secret values</span><textarea value={secretValues} onChange={(event) => setSecretValues(event.target.value)} placeholder="api-token=secret value" autoComplete="off" /><small>Optional. Values are stored by the Node and never returned.</small></label> : null}
        </div>
        {oauthStatus ? <section className="extension-oauth-status" aria-live="polite"><span className={`extension-status-dot ${oauthStatus.status === "connected" ? "is-ready" : ""}`} /><div><strong>{oauthStatus.status === "connected" ? "Connected" : oauthStatus.status === "error" ? "Sign-in failed" : "Waiting for browser sign-in"}</strong><small>{oauthStatus.status === "connected" ? "Authorization is stored securely by the Node." : oauthStatus.error || "Complete the Granola sign-in in your browser. This window will update automatically."}</small></div></section> : null}
        <details className="extension-advanced"><summary>Agent access and tool policy</summary><div className="extension-advanced-body"><label><span>Tool allowlist</span><textarea value={toolFilter} onChange={(event) => setToolFilter(event.target.value)} placeholder="Empty allows all tools" /></label><label><span>Tool prefix</span><input value={toolPrefix} onChange={(event) => setToolPrefix(event.target.value)} placeholder="Optional" /></label><label><span>Inline budget (ms)</span><input type="number" min="100" max="60000" value={inlineBudgetMs} onChange={(event) => setInlineBudgetMs(event.target.value)} /></label><div className="extension-check-grid"><label><input type="checkbox" checked={requireConfirmation} onChange={(event) => setRequireConfirmation(event.target.checked)} /> Confirm risky calls</label><label><input type="checkbox" checked={progressEvents} onChange={(event) => setProgressEvents(event.target.checked)} /> Stream progress</label><label><input type="checkbox" checked={longTaskProxy} onChange={(event) => setLongTaskProxy(event.target.checked)} /> Long-task proxy</label></div><fieldset><legend>Enabled Agents</legend>{props.agents.map((agent) => <label key={agent.id}><input type="checkbox" checked={enabledAgentIds.includes(agent.id)} onChange={(event) => setEnabledAgentIds((current) => event.target.checked ? [...current, agent.id] : current.filter((id) => id !== agent.id))} /> {agent.name}</label>)}</fieldset></div></details>
        {error ? <p className="agent-dialog-error">{error}</p> : null}
        <footer className="agent-dialog-actions"><button type="button" className="agent-dialog-cancel" onClick={props.onClose} disabled={working}>Cancel</button><button className="agent-dialog-create" disabled={working || Boolean(pendingOAuth) || !serverId.trim() || !displayName.trim() || (transportType === "stdio" ? !command.trim() : !url.trim())}>{pendingOAuth ? "Waiting for sign-in…" : working ? "Saving…" : existing ? "Save changes" : "Add server"}</button></footer>
      </form>
    </DialogShell>
  );
}

function AppConnectionDialog(props: { state: AppConnectionDialogState; onClose: () => void; onSaved: () => void }) {
  const details = props.state.app.details;
  const credentials = recordArray(details.credentials) as unknown as AppCredentialSpec[];
  const tools = recordArray(details.tools) as unknown as AppToolSpec[];
  const existing = props.state.connection;
  const [connectionId, setConnectionId] = useState(existing?.id ?? `${props.state.app.id}-default`);
  const [displayName, setDisplayName] = useState(existing?.displayName ?? `${props.state.app.displayName} account`);
  const [credentialValues, setCredentialValues] = useState<Record<string, string>>({});
  const [enabledTools, setEnabledTools] = useState<string[]>(existing?.enabledTools ?? tools.filter((tool) => tool.enabledByDefault).map((tool) => tool.name));
  const [requireConfirmation, setRequireConfirmation] = useState(existing?.requiresConfirmation ?? true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault(); setWorking(true); setError(null);
    try {
      await window.ppxClient.saveAppConnection({ appId: props.state.app.id, connectionId: connectionId.trim(), displayName: displayName.trim(), enabledTools, requireConfirmation, credentialValues, expectedRevision: existing?.revision ?? null });
      setCredentialValues({}); props.onSaved();
    } catch (nextError) { setError(errorMessage(nextError)); }
    finally { setWorking(false); }
  }

  return (
    <DialogShell title={existing ? `Edit ${existing.displayName}` : `Connect ${props.state.app.displayName}`} eyebrow="App Connection" description="Credentials are stored by the Node and remain write-only." busy={working} onClose={props.onClose}>
      <form onSubmit={(event) => void submit(event)}>
        <div className="agent-dialog-fields extension-dialog-fields">
          <label><span>Connection ID</span><input value={connectionId} onChange={(event) => setConnectionId(event.target.value)} disabled={Boolean(existing)} spellCheck={false} /></label>
          <label><span>Name</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
          {credentials.map((credential) => <label key={credential.name}><span>{credential.label}{credential.required ? " *" : ""}</span><input type={credential.inputType === "email" ? "email" : credential.inputType === "text" ? "text" : "password"} autoComplete={credential.inputType === "email" ? "email" : "new-password"} value={credentialValues[credential.name] ?? ""} onChange={(event) => setCredentialValues((current) => ({ ...current, [credential.name]: event.target.value }))} placeholder={existing?.credentialRefs[credential.name] ? "Saved securely · leave blank to keep" : "Enter credential"} /></label>)}
        </div>
        {tools.length ? <section className="app-tool-picker"><header><strong>Available tools</strong><span>{enabledTools.length} enabled</span></header>{tools.map((tool) => <label key={tool.name}><input type="checkbox" checked={enabledTools.includes(tool.name)} onChange={(event) => setEnabledTools((current) => event.target.checked ? [...current, tool.name] : current.filter((name) => name !== tool.name))} /><span><strong>{tool.title || tool.name}</strong><small>{tool.description} · {tool.access} · {tool.risk} risk</small></span></label>)}</section> : null}
        <label className="extension-confirmation-row"><input type="checkbox" checked={requireConfirmation} onChange={(event) => setRequireConfirmation(event.target.checked)} /><span><strong>Ask before write actions</strong><small>Keep human confirmation in the loop for consequential tools.</small></span></label>
        {error ? <p className="agent-dialog-error">{error}</p> : null}
        <footer className="agent-dialog-actions"><button type="button" className="agent-dialog-cancel" onClick={props.onClose} disabled={working}>Cancel</button><button className="agent-dialog-create" disabled={working || !connectionId.trim() || !displayName.trim()}>{working ? "Saving…" : existing ? "Save changes" : "Connect"}</button></footer>
      </form>
    </DialogShell>
  );
}

function ExtensionDetailDialog(props: {
  detail: ExtensionDetail;
  selectedAgentId: string;
  onClose: () => void;
  onEditSource: () => void;
  onEditMcp: () => void;
  onEditConnection: (connection: AppConnectionDetail) => void;
  onAddConnection: () => void;
  onSetEnabled: (enabled: boolean) => void;
  onRemove: () => Promise<void>;
  onConnectionEnabled: (connection: AppConnectionDetail, enabled: boolean) => Promise<void>;
  onRemoveConnection: (connection: AppConnectionDetail) => Promise<void>;
}) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removeArmed, setRemoveArmed] = useState(false);
  const [mcpProbe, setMcpProbe] = useState<ExtensionProbeResult | null>(null);
  const [connectionProbes, setConnectionProbes] = useState<Record<string, ExtensionProbeResult>>({});
  const [healthHistories, setHealthHistories] = useState<Record<string, ExtensionHealthHistory>>({});
  const [hookStatus, setHookStatus] = useState<PluginHookStatus | null>(null);
  const detail = props.detail;
  const builtin = detail.status === "builtin";
  const enabled = builtin || Boolean(props.selectedAgentId && detail.enabledAgentIds.includes(props.selectedAgentId));
  const stateLabel = detail.readiness.ready ? (builtin ? "Ready" : detail.status) : "Needs attention";
  const visibleVersion = detail.version && detail.version !== "builtin" ? detail.version : null;
  const trustLabel = detail.source.trust === "builtin" ? "Built in" : detail.source.trust.replace("_", " ");
  const connections = recordArray(detail.details.connections) as unknown as AppConnectionDetail[];
  const connectionIds = connections.map((connection) => connection.id).sort().join("|");
  const capabilities = Array.isArray(detail.details.capabilities) ? detail.details.capabilities.map(String) : [];
  const dependencies = Array.isArray(detail.details.dependencies) ? detail.details.dependencies.map(String) : [];
  const resource = asMcpResource(detail.details.resource);
  const removable = detail.kind !== "app" && detail.status !== "builtin" && !detail.managedBy && detail.enabledAgentIds.length === 0;

  useEffect(() => {
    if (detail.kind !== "plugin") return undefined;
    let active = true;
    window.ppxClient.getPluginHookStatus(detail.id, detail.revision)
      .then((status) => { if (active) setHookStatus(status); })
      .catch((nextError) => { if (active) setError(errorMessage(nextError)); });
    return () => { active = false; };
  }, [detail.id, detail.kind, detail.revision]);

  useEffect(() => {
    const targets: Array<{ key: string; kind: "mcp" | "app_connection"; id: string }> = detail.kind === "mcp"
      ? [{ key: detail.id, kind: "mcp", id: detail.id }]
      : detail.kind === "app"
        ? connections.map((connection) => ({ key: connection.id, kind: "app_connection", id: connection.id }))
        : [];
    if (!targets.length) {
      setHealthHistories({});
      return undefined;
    }
    let active = true;
    Promise.all(targets.map(async (target) => [target.key, await window.ppxClient.getExtensionHealthHistory(target.kind, target.id, 6)] as const))
      .then((entries) => { if (active) setHealthHistories(Object.fromEntries(entries)); })
      .catch(() => { if (active) setHealthHistories({}); });
    return () => { active = false; };
  }, [connectionIds, detail.id, detail.kind, detail.revision]);

  async function run(action: () => Promise<void>): Promise<void> {
    setWorking(true); setError(null);
    try { await action(); }
    catch (nextError) { setError(errorMessage(nextError)); }
    finally { setWorking(false); }
  }

  async function testMcp(): Promise<void> {
    await run(async () => {
      setMcpProbe(await window.ppxClient.testMcpServer(detail.id));
      const history = await window.ppxClient.getExtensionHealthHistory("mcp", detail.id, 6);
      setHealthHistories((current) => ({
        ...current,
        [detail.id]: history,
      }));
    });
  }

  async function testConnection(connectionId: string): Promise<void> {
    await run(async () => {
      const result = await window.ppxClient.testAppConnection(connectionId);
      setConnectionProbes((current) => ({ ...current, [connectionId]: result }));
      const history = await window.ppxClient.getExtensionHealthHistory("app_connection", connectionId, 6);
      setHealthHistories((current) => ({
        ...current,
        [connectionId]: history,
      }));
    });
  }

  async function setHookTrust(trusted: boolean): Promise<void> {
    await run(async () => {
      setHookStatus(await window.ppxClient.setPluginHookTrust(detail.id, detail.revision, trusted));
    });
  }

  return (
    <DialogShell title={detail.displayName} eyebrow={title(detail.kind)} description={detail.description || `${title(detail.kind)} resource on this Node.`} busy={working} onClose={props.onClose} wide>
      <div className="extension-detail-status"><span className={`extension-state ${detail.readiness.ready ? "ready" : "blocked"}`}><i />{stateLabel}</span>{visibleVersion ? <span>{visibleVersion}</span> : null}<span>{trustLabel}</span><span>{detail.risk} risk</span></div>
      {detail.readiness.issues.length ? <section className="extension-issues"><strong>Needs attention</strong>{detail.readiness.issues.map((issue) => <p key={issue}>{issue}</p>)}</section> : null}
      {detail.kind === "mcp" && healthHistories[detail.id]?.items.length ? <ExtensionHealthHistoryPanel history={healthHistories[detail.id]} /> : null}
      {detail.kind === "mcp" && resource ? <section className="extension-detail-section"><header><strong>Server</strong><div className="extension-section-actions"><button className="secondary" disabled={working} onClick={() => void testMcp()}>{working ? "Testing…" : "Test connection"}</button><button className="secondary" onClick={props.onEditMcp}>Edit</button></div></header><dl className="extension-detail-grid"><div><dt>Transport</dt><dd>{resource.spec.transport.type.replace("_", " ")}</dd></div><div><dt>Tool prefix</dt><dd>{resource.spec.policy.toolNamePrefix || "None"}</dd></div><div><dt>Tool access</dt><dd>{resource.spec.policy.toolFilter.length ? `${resource.spec.policy.toolFilter.length} allowed` : "All tools"}</dd></div><div><dt>Long tasks</dt><dd>{resource.spec.policy.longTaskProxy ? "proxied" : "inline"}</dd></div></dl>{mcpProbe ? <ExtensionProbePanel result={mcpProbe} /> : <p className="extension-probe-hint">Run a live check to verify the server and discover its current tools.</p>}</section> : null}
      {detail.kind === "app" ? <section className="extension-detail-section"><header><div><strong>Connections</strong><p>Each connection owns its credentials, tool policy, and Agent access.</p></div><button onClick={props.onAddConnection}>Add connection</button></header>{connections.length ? <div className="app-connection-list">{connections.map((connection) => { const connectionEnabled = Boolean(props.selectedAgentId && connection.enabledAgentIds.includes(props.selectedAgentId)); const probe = connectionProbes[connection.id]; const health = healthHistories[connection.id]; const authType = String(detail.details.authType ?? "none"); return <article key={connection.id}><button className="app-connection-open" onClick={() => props.onEditConnection(connection)}><strong>{connection.displayName}</strong><span>{connection.ready ? "Ready" : connection.authState.replace("_", " ")}</span><small>{connection.enabledTools ? `${connection.enabledTools.length} tools` : "Default tools"}</small></button><div className="app-connection-actions"><button className="secondary" disabled={working} onClick={() => void testConnection(connection.id)}>{working ? "Testing…" : "Test"}</button><button className="secondary" disabled={working} onClick={() => props.onEditConnection(connection)}>{authType === "oauth" ? "Reauthorize" : authType === "secret" ? "Update credentials" : "Edit"}</button><button className="secondary" disabled={!props.selectedAgentId || working} onClick={() => void run(() => props.onConnectionEnabled(connection, !connectionEnabled))}>{connectionEnabled ? "Disable" : "Enable"}</button><button className="secondary danger-quiet" disabled={working || connection.enabledAgentIds.length > 0} onClick={() => void run(() => props.onRemoveConnection(connection))}>Remove</button></div>{probe ? <ExtensionProbePanel result={probe} compact /> : null}{health?.items.length ? <ExtensionHealthHistoryPanel history={health} compact /> : null}</article>; })}</div> : <div className="extension-empty-state compact"><span>No connections</span><p>Add an account before an Agent can use this App.</p></div>}</section> : null}
      {detail.kind === "plugin" && hookStatus ? <section className="extension-detail-section plugin-hook-section"><header><div><strong>Lifecycle Hooks</strong><p>Host commands execute only after you trust this exact Hook definition. Updating the Plugin revokes trust automatically.</p></div>{hookStatus.handlerCount > 0 ? <button className={hookStatus.trusted ? "secondary" : ""} disabled={working || hookStatus.executableCount === 0} onClick={() => void setHookTrust(!hookStatus.trusted)}>{working ? "Applying…" : hookStatus.trusted ? "Revoke trust" : "Trust exact Hooks"}</button> : null}</header><dl className="extension-detail-grid"><div><dt>Status</dt><dd>{hookStatus.handlerCount === 0 ? "No Hooks" : hookStatus.trusted ? "Trusted for this version" : "Not trusted"}</dd></div><div><dt>Handlers</dt><dd>{hookStatus.executableCount} executable · {hookStatus.unsupportedHandlers} skipped</dd></div></dl>{hookStatus.handlers.length ? <div className="plugin-hook-list">{hookStatus.handlers.map((handler, index) => <article key={`${handler.event}:${index}`}><div><strong>{handler.event}</strong><span>{handler.type} · {handler.timeout}s · {handler.supported ? "supported" : "skipped"}</span></div>{handler.command ? <code>{handler.command}</code> : <span>Prompt/agent Hooks are visible but are not executed by OpenPPX.</span>}</article>)}</div> : null}</section> : null}
      {capabilities.length || dependencies.length ? <section className="extension-detail-section"><strong>{capabilities.length ? "Capabilities" : "Dependencies"}</strong><div className="extension-chip-list">{(capabilities.length ? capabilities : dependencies).map((item) => <span key={item}>{item}</span>)}</div></section> : null}
      {detail.kind !== "app" ? <section className="extension-agent-access"><div><strong>Selected Agent</strong><p>{builtin ? "Built-in capabilities are available to every Agent." : props.selectedAgentId ? (enabled ? "This capability is available to the selected Agent." : "This capability is not available to the selected Agent.") : "Select an Agent in the sidebar to manage access."}</p></div><button className="secondary" disabled={!props.selectedAgentId || builtin || working} onClick={() => props.onSetEnabled(!enabled)}>{builtin ? "Always on" : enabled ? "Disable" : "Enable"}</button></section> : null}
      {error ? <p className="agent-dialog-error">{error}</p> : null}
      <footer className="agent-dialog-actions extension-detail-actions">
        {detail.kind === "plugin" || detail.kind === "skill" ? <button className="secondary" onClick={props.onEditSource}>Update source</button> : null}
        {removable ? (removeArmed ? <button className="danger-button" disabled={working} onClick={() => void run(props.onRemove)}>{working ? "Removing…" : "Confirm remove"}</button> : <button className="secondary danger-quiet" onClick={() => setRemoveArmed(true)}>Remove</button>) : null}
        <span />
        <button className="agent-dialog-create" onClick={props.onClose}>Done</button>
      </footer>
    </DialogShell>
  );
}

function ExtensionHealthHistoryPanel(props: { history: ExtensionHealthHistory; compact?: boolean }) {
  const { history } = props;
  const latest = history.summary.latest;
  if (!latest) return null;
  const lastSuccess = history.summary.lastSuccessAtMs
    ? new Date(history.summary.lastSuccessAtMs).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
    : "No successful check yet";
  return <section className={`extension-health-history${props.compact ? " compact" : ""}`}><header><div><strong>Connection history</strong><span>{history.summary.consecutiveFailures ? `${history.summary.consecutiveFailures} consecutive failures` : "Latest check healthy"}</span></div><time dateTime={new Date(latest.checkedAtMs).toISOString()}>{new Date(latest.checkedAtMs).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</time></header><dl><div><dt>Last success</dt><dd>{lastSuccess}</dd></div><div><dt>Latest latency</dt><dd>{latest.elapsedMs} ms</dd></div><div><dt>Latest result</dt><dd>{latest.ready ? "Ready" : latest.errorKind?.replaceAll("_", " ") || latest.status}</dd></div></dl>{latest.issues.length ? <p>{latest.issues.join(" · ").replaceAll("_", " ")}</p> : null}</section>;
}

function ExtensionProbePanel(props: { result: ExtensionProbeResult; compact?: boolean }) {
  const result = props.result;
  const summary = result.ready
    ? `Connected · ${result.toolCount} ${result.toolCount === 1 ? "tool" : "tools"} · ${result.elapsedMs} ms`
    : result.status === "blocked"
      ? "Connection check blocked"
      : result.status === "timeout"
        ? "Connection timed out"
        : "Connection failed";
  return <section className={`extension-probe-result${props.compact ? " compact" : ""}`} aria-live="polite"><header><span className={`extension-state ${result.ready ? "ready" : "blocked"}`}><i />{summary}</span><time dateTime={result.checkedAt}>{new Date(result.checkedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></header>{result.toolNames.length ? <div className="extension-chip-list">{result.toolNames.map((name) => <span key={name}>{name}</span>)}</div> : null}{result.issues.length ? <p>{result.issues.join(" · ").replaceAll("_", " ")}</p> : null}{result.message ? <p>{result.message}</p> : null}</section>;
}

function DialogShell(props: { title: string; eyebrow: string; description: string; busy: boolean; wide?: boolean; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void { if (event.key === "Escape" && !props.busy) props.onClose(); }
    window.addEventListener("keydown", onKeyDown); return () => window.removeEventListener("keydown", onKeyDown);
  }, [props.busy, props.onClose]);
  return <div className="agent-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !props.busy) props.onClose(); }}><section className={`agent-dialog extension-dialog${props.wide ? " wide" : ""}`} role="dialog" aria-modal="true"><header className="agent-dialog-header"><div><span className="agent-dialog-eyebrow">{props.eyebrow}</span><h2>{props.title}</h2><p>{props.description}</p></div><button className="extension-dialog-close" onClick={props.onClose} disabled={props.busy} aria-label="Close">×</button></header>{props.children}</section></div>;
}
