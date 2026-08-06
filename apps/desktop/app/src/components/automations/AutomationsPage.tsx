import { useEffect, useMemo, useState } from "react";
import type {
  AgentProfile,
  AutomationCreateInput,
  AutomationDetail,
  AutomationRunSummary,
  AutomationStatus,
  AutomationSummary,
  AutomationTemplateSummary,
} from "../../types";
import { CollapsedSidebarTools } from "../workspace/ContextSidebar";

type StatusFilter = "all" | AutomationStatus;
type ScheduleMode = "none" | "every" | "cron" | "event";

interface AutomationsPageProps {
  agents: AgentProfile[];
  selectedAgentId: string;
  userId: string;
  sidebarCollapsed: boolean;
  canCreateSession: boolean;
  onRevealSidebar: () => void;
  onNewSession: () => void;
  onSearchSessions: () => void;
}

interface AutomationDraft {
  name: string;
  description: string;
  instructions: string;
  agentId: string;
  scheduleMode: ScheduleMode;
  everyMinutes: string;
  cronExpr: string;
  timezone: string;
  eventKey: string;
  outputRequirements: string;
  retryAttempts: string;
  timeoutMinutes: string;
  concurrencyMode: "skip" | "queue-one" | "parallel-with-limit";
  monitor: boolean;
}

const EMPTY_DRAFT: AutomationDraft = {
  name: "",
  description: "",
  instructions: "",
  agentId: "",
  scheduleMode: "none",
  everyMinutes: "60",
  cronExpr: "0 9 * * 1-5",
  timezone: "",
  eventKey: "",
  outputRequirements: "",
  retryAttempts: "1",
  timeoutMinutes: "30",
  concurrencyMode: "skip",
  monitor: false,
};

function scheduleLabel(automation: AutomationSummary): string {
  const trigger = automation.trigger;
  if (!trigger) return "Manual only";
  if (trigger.type === "local_event") return trigger.eventKey ? `Event · ${trigger.eventKey}` : "Local event";
  const schedule = trigger.schedule;
  if (!schedule) return "Schedule unavailable";
  if (schedule.kind === "every") {
    const minutes = Math.round((schedule.everySeconds ?? 0) / 60);
    return minutes >= 60 && minutes % 60 === 0 ? `Every ${minutes / 60}h` : `Every ${minutes}m`;
  }
  if (schedule.kind === "at") return schedule.atMs ? `Once · ${new Date(schedule.atMs).toLocaleString()}` : "Once";
  return `Cron · ${schedule.cronExpr}`;
}

function timeLabel(value: number | null | undefined): string {
  if (!value) return "—";
  const delta = value - Date.now();
  const minutes = Math.round(Math.abs(delta) / 60_000);
  if (minutes < 1) return delta >= 0 ? "now" : "just now";
  if (minutes < 60) return delta >= 0 ? `in ${minutes}m` : `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return delta >= 0 ? `in ${hours}h` : `${hours}h ago`;
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function draftFromTemplate(template: AutomationTemplateSummary, agentId: string): AutomationDraft {
  const schedule = template.recommendedSchedule;
  const kind = schedule.kind === "every" || schedule.kind === "cron" ? schedule.kind : "none";
  return {
    ...EMPTY_DRAFT,
    name: template.name,
    description: template.description,
    instructions: template.instructions,
    agentId,
    scheduleMode: kind,
    everyMinutes: String(Math.max(1, Number(schedule.everySeconds ?? 3600) / 60)),
    cronExpr: String(schedule.cronExpr ?? "0 9 * * 1-5"),
    timezone: String(schedule.timezone ?? ""),
    eventKey: "",
    outputRequirements: template.outputRequirements.join("\n"),
    monitor: template.behavior === "monitor",
  };
}

function createInput(draft: AutomationDraft, userId: string): AutomationCreateInput {
  const schedule = draft.scheduleMode === "every"
    ? { kind: "every", everySeconds: Math.round(Number(draft.everyMinutes) * 60), timezone: draft.timezone }
    : draft.scheduleMode === "cron"
      ? { kind: "cron", cronExpr: draft.cronExpr.trim(), timezone: draft.timezone }
      : null;
  const localEvent = draft.scheduleMode === "event"
    ? {
        eventKey: draft.eventKey.trim(),
        inputSchema: { type: "object", properties: {}, additionalProperties: true },
      }
    : null;
  return {
    userId,
    agentId: draft.agentId,
    name: draft.name.trim(),
    description: draft.description.trim(),
    instructions: draft.instructions.trim(),
    outputRequirements: draft.outputRequirements.split("\n").map((item) => item.trim()).filter(Boolean),
    contextMode: draft.monitor ? "rolling" : "isolated",
    schedule,
    localEvent,
    retryPolicy: { maxAttempts: Number(draft.retryAttempts), backoffSeconds: 30 },
    budgetPolicy: { timeoutSeconds: Number(draft.timeoutMinutes) * 60 },
    concurrencyPolicy: { mode: draft.concurrencyMode, limit: 1 },
    monitorPolicy: { enabled: draft.monitor, notifyOnChangeOnly: true, stopWhenContains: "" },
    permissionsConfirmed: false,
  };
}

/** User-facing Automation manager backed only by formal Automation Actions. */
export function AutomationsPage(props: AutomationsPageProps) {
  const [items, setItems] = useState<AutomationSummary[]>([]);
  const [templates, setTemplates] = useState<AutomationTemplateSummary[]>([]);
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<AutomationDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState<AutomationDetail | null>(null);
  const [history, setHistory] = useState<AutomationRunSummary[]>([]);
  const [workingId, setWorkingId] = useState<string | null>(null);

  async function refresh(): Promise<void> {
    setLoading(true);
    setError(null);
    try {
      const [automationResult, templateResult] = await Promise.all([
        window.ppxClient.listAutomations(),
        window.ppxClient.listAutomationTemplates(),
      ]);
      setItems(automationResult.automations);
      setTemplates(templateResult.templates);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return items.filter((item) => (filter === "all" || item.status === filter)
      && (!normalized || `${item.name} ${item.description} ${item.agentId}`.toLowerCase().includes(normalized)));
  }, [filter, items, query]);

  async function openDetail(item: AutomationSummary): Promise<void> {
    setWorkingId(item.automationId);
    setError(null);
    try {
      const [detail, historyResult] = await Promise.all([
        window.ppxClient.getAutomation(item.automationId),
        window.ppxClient.getAutomationHistory(item.automationId),
      ]);
      setSelected(detail);
      setHistory(Array.isArray(historyResult.runs) ? historyResult.runs as AutomationRunSummary[] : []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkingId(null);
    }
  }

  async function saveDraft(): Promise<void> {
    if (!draft || !draft.name.trim() || !draft.instructions.trim() || !draft.agentId) return;
    setSaving(true);
    setError(null);
    try {
      await window.ppxClient.createAutomation(createInput(draft, props.userId));
      setDraft(null);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  async function transition(operation: "pause" | "resume" | "delete"): Promise<void> {
    if (!selected) return;
    if (operation === "delete" && !window.confirm(`Delete “${selected.name}”? Run history remains auditable, but the Automation cannot run again.`)) return;
    setWorkingId(selected.automationId);
    try {
      await window.ppxClient.transitionAutomation(operation, selected.automationId, selected.revision);
      setSelected(null);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkingId(null);
    }
  }

  async function runNow(): Promise<void> {
    if (!selected) return;
    setWorkingId(selected.automationId);
    try {
      await window.ppxClient.runAutomation(selected.automationId);
      await openDetail(selected);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkingId(null);
    }
  }

  const defaultAgentId = props.selectedAgentId || props.agents[0]?.id || "";
  return (
    <section className="settings-shell automations-shell">
      <header className="column-topbar automation-topbar">
        <div className="topbar-copy">
          {props.sidebarCollapsed ? (
            <CollapsedSidebarTools
              canCreateSession={props.canCreateSession}
              onRevealSidebar={props.onRevealSidebar}
              onNewSession={props.onNewSession}
              onSearchSessions={props.onSearchSessions}
            />
          ) : null}
          <strong>Automations</strong>
          <span className="topbar-location">Independent ADK runs</span>
        </div>
        <button className="automation-primary-action" onClick={() => setDraft({ ...EMPTY_DRAFT, agentId: defaultAgentId })}>
          New automation
        </button>
      </header>
      <main className="automation-page">
        <div className="automation-page-heading">
          <div>
            <h1>Automations</h1>
            <p>Recurring tasks and local monitors that run as independent, auditable Agent sessions.</p>
          </div>
          <button className="automation-secondary-action" onClick={() => void refresh()} disabled={loading}>Refresh</button>
        </div>

        {templates.length > 0 ? (
          <section className="automation-template-section">
            <span className="automation-section-label">Start from a template</span>
            <div className="automation-template-grid">
              {templates.map((template) => (
                <button key={template.templateId} className="automation-template-card" onClick={() => setDraft(draftFromTemplate(template, defaultAgentId))}>
                  <span className="automation-template-icon" aria-hidden="true">{template.behavior === "monitor" ? "◎" : "↻"}</span>
                  <strong>{template.name}</strong>
                  <span>{template.description}</span>
                  <small>{template.behavior === "monitor" ? "Monitor" : "Scheduled task"}</small>
                </button>
              ))}
            </div>
          </section>
        ) : null}

        <section className="automation-list-section">
          <div className="automation-list-tools">
            <div className="automation-filter-tabs" role="tablist" aria-label="Automation status">
              {(["all", "active", "paused", "blocked"] as StatusFilter[]).map((status) => (
                <button key={status} className={filter === status ? "active" : ""} onClick={() => setFilter(status)}>
                  {status[0].toUpperCase() + status.slice(1)}
                </button>
              ))}
            </div>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search automations" aria-label="Search automations" />
          </div>
          {error ? <div className="automation-error" role="alert">{error}</div> : null}
          {loading ? <div className="automation-empty">Loading automations…</div> : visibleItems.length === 0 ? (
            <div className="automation-empty">
              <strong>{items.length === 0 ? "No automations yet" : "No matching automations"}</strong>
              <span>{items.length === 0 ? "Choose a reviewed template or create one from scratch." : "Try another status or search term."}</span>
            </div>
          ) : (
            <div className="automation-list">
              {visibleItems.map((item) => (
                <button key={item.automationId} className="automation-row" onClick={() => void openDetail(item)} disabled={workingId === item.automationId}>
                  <span className={`automation-status-mark ${item.status}`} aria-hidden="true" />
                  <span className="automation-row-copy">
                    <strong>{item.name}</strong>
                    <small>{item.description || "No description"}</small>
                  </span>
                  <span className="automation-row-meta">
                    <span>{scheduleLabel(item)}</span>
                    <small>{item.latestRun ? `${item.latestRun.status} · ${timeLabel(item.latestRun.endedAtMs ?? item.latestRun.createdAtMs)}` : "Never run"}</small>
                  </span>
                  <span className="automation-row-next">
                    <small>Next</small>
                    <strong>{timeLabel(item.trigger?.nextRunAtMs)}</strong>
                  </span>
                  <span className="automation-row-chevron" aria-hidden="true">›</span>
                </button>
              ))}
            </div>
          )}
        </section>
      </main>

      {draft ? (
        <div className="agent-dialog-backdrop" role="presentation">
          <div className="agent-dialog automation-editor" role="dialog" aria-modal="true" aria-labelledby="automation-editor-title">
            <header className="agent-dialog-header">
              <span className="agent-dialog-eyebrow">New automation</span>
              <h2 id="automation-editor-title">Define the work, then the schedule</h2>
              <p>Each occurrence creates a separate ADK Session and keeps its own execution evidence.</p>
            </header>
            <form onSubmit={(event) => { event.preventDefault(); void saveDraft(); }}>
              <div className="agent-dialog-fields automation-editor-fields">
                <label><span>Name</span><input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} required /></label>
                <label><span>Agent</span><select value={draft.agentId} onChange={(event) => setDraft({ ...draft, agentId: event.target.value })} required>{props.agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
                <label className="agent-dialog-full-row"><span>Description</span><input value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
                <label className="agent-dialog-full-row"><span>Instructions</span><textarea value={draft.instructions} onChange={(event) => setDraft({ ...draft, instructions: event.target.value })} rows={5} required /></label>
                <label><span>Trigger</span><select value={draft.scheduleMode} onChange={(event) => setDraft({ ...draft, scheduleMode: event.target.value as ScheduleMode })}><option value="none">Manual only</option><option value="every">Repeat interval</option><option value="cron">Cron expression</option><option value="event">Local event</option></select></label>
                {draft.scheduleMode === "every" ? <label><span>Every (minutes)</span><input type="number" min="1" value={draft.everyMinutes} onChange={(event) => setDraft({ ...draft, everyMinutes: event.target.value })} /></label> : null}
                {draft.scheduleMode === "cron" ? <label><span>Cron expression</span><input value={draft.cronExpr} onChange={(event) => setDraft({ ...draft, cronExpr: event.target.value })} /></label> : null}
                {draft.scheduleMode === "event" ? <label><span>Event key</span><input value={draft.eventKey} onChange={(event) => setDraft({ ...draft, eventKey: event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })} placeholder="source-item-changed" required /></label> : null}
                {draft.scheduleMode !== "none" && draft.scheduleMode !== "event" ? <label><span>Timezone (optional)</span><input value={draft.timezone} onChange={(event) => setDraft({ ...draft, timezone: event.target.value })} placeholder="System timezone" /></label> : null}
                <label><span>On overlap</span><select value={draft.concurrencyMode} onChange={(event) => setDraft({ ...draft, concurrencyMode: event.target.value as AutomationDraft["concurrencyMode"] })}><option value="skip">Skip occurrence</option><option value="queue-one">Queue one</option><option value="parallel-with-limit">Allow parallel</option></select></label>
                <label><span>Retry attempts</span><input type="number" min="1" max="5" value={draft.retryAttempts} onChange={(event) => setDraft({ ...draft, retryAttempts: event.target.value })} /></label>
                <label><span>Timeout (minutes)</span><input type="number" min="1" max="1440" value={draft.timeoutMinutes} onChange={(event) => setDraft({ ...draft, timeoutMinutes: event.target.value })} /></label>
                <label className="agent-dialog-full-row"><span>Expected outputs (one per line)</span><textarea value={draft.outputRequirements} onChange={(event) => setDraft({ ...draft, outputRequirements: event.target.value })} rows={3} /></label>
                <label className="automation-check-row agent-dialog-full-row"><input type="checkbox" checked={draft.monitor} onChange={(event) => setDraft({ ...draft, monitor: event.target.checked })} /><span>Monitor mode · stay quiet when the observed result does not change.</span></label>
              </div>
              <div className="agent-dialog-actions">
                <button type="button" className="agent-dialog-cancel" onClick={() => setDraft(null)}>Cancel</button>
                <button type="submit" className="agent-dialog-create" disabled={saving || !draft.name.trim() || !draft.instructions.trim() || !draft.agentId}>{saving ? "Creating…" : "Create automation"}</button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      {selected ? (
        <div className="agent-dialog-backdrop" role="presentation">
          <div className="agent-dialog automation-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="automation-detail-title">
            <header className="agent-dialog-header automation-detail-header">
              <div><span className="agent-dialog-eyebrow">{selected.status} · revision {selected.revision}</span><h2 id="automation-detail-title">{selected.name}</h2><p>{selected.description || selected.instructions}</p></div>
              <button className="automation-close" onClick={() => setSelected(null)} aria-label="Close">×</button>
            </header>
            <div className="automation-detail-body">
              <dl className="automation-facts">
                <div><dt>Schedule</dt><dd>{scheduleLabel(selected)}</dd></div>
                <div><dt>Agent</dt><dd>{props.agents.find((agent) => agent.id === selected.agentId)?.name ?? selected.agentId}</dd></div>
                <div><dt>Next run</dt><dd>{timeLabel(selected.trigger?.nextRunAtMs)}</dd></div>
                <div><dt>Readiness</dt><dd>{selected.readiness.ready ? "Ready" : selected.readiness.reasons[0]?.message ?? "Blocked"}</dd></div>
              </dl>
              <section><h3>Instructions</h3><p>{selected.instructions}</p></section>
              <section><h3>Recent runs</h3>{history.length === 0 ? <p className="automation-muted">This Automation has not run yet.</p> : <div className="automation-history">{history.slice(0, 8).map((run) => <div key={run.automationRunId}><span className={`automation-status-mark ${run.status}`} /><strong>{run.status}</strong><span>Attempt {run.attempt}</span><small>{timeLabel(run.endedAtMs ?? run.createdAtMs)}</small></div>)}</div>}</section>
            </div>
            <footer className="automation-detail-actions">
              <button className="automation-danger-action" onClick={() => void transition("delete")}>Delete</button>
              <span />
              <button className="automation-secondary-action" onClick={() => void transition(selected.status === "paused" ? "resume" : "pause")}>{selected.status === "paused" ? "Resume" : "Pause"}</button>
              <button className="automation-primary-action" onClick={() => void runNow()} disabled={workingId === selected.automationId || !selected.readiness.ready}>{workingId === selected.automationId ? "Starting…" : "Run now"}</button>
            </footer>
          </div>
        </div>
      ) : null}
    </section>
  );
}
