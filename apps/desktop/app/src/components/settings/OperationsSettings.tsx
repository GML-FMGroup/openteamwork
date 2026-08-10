import { useEffect, useMemo, useState } from "react";
import type {
  AgentProfile,
  CronCreateInput,
  HeartbeatConfiguration,
  OperationsAuditItem,
  OperationsDashboard,
  OperationsTaskControlAction,
  OperationsTaskItem,
  RuntimeState,
  RuntimeStatus,
} from "../../types";
type OperationsSection = "overview" | "tasks" | "automations" | "usage" | "audit";

interface OperationsSettingsProps {
  runtime: RuntimeStatus;
  agents: AgentProfile[];
  selectedAgentId: string;
  userId: string;
  onRuntimeAction: () => void;
  onStopRuntime: () => void;
}

interface CronDraft {
  name: string;
  message: string;
  kind: "every" | "cron" | "at";
  everyMinutes: string;
  cronExpression: string;
  timezone: string;
  at: string;
}

const EMPTY_CRON: CronDraft = {
  name: "",
  message: "",
  kind: "every",
  everyMinutes: "60",
  cronExpression: "0 9 * * *",
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  at: "",
};

function runtimeActionLabel(state: RuntimeState): string {
  if (state === "stopped") return "Start";
  if (state === "healthy") return "Restart";
  return "Retry";
}

function formatTime(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const date = typeof value === "number" ? new Date(value) : new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function formatCount(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function scheduleLabel(schedule: OperationsDashboard["cron"]["items"][number]["schedule"]): string {
  if (schedule.kind === "every") return `Every ${Math.round((schedule.everySeconds ?? 0) / 60)} min`;
  if (schedule.kind === "at") return `Once · ${formatTime(schedule.atMs)}`;
  return `${schedule.cronExpr ?? "Cron"}${schedule.tz ? ` · ${schedule.tz}` : ""}`;
}

function auditOutcome(item: OperationsAuditItem): string {
  return item.outcomeCode ?? item.decisionCode;
}

/** Full operator surface for durable Tasks, automation, usage, and audit facts. */
export function OperationsSettings(props: OperationsSettingsProps) {
  const [section, setSection] = useState<OperationsSection>("overview");
  const [dashboard, setDashboard] = useState<OperationsDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [taskOutput, setTaskOutput] = useState<string | null>(null);
  const [taskInput, setTaskInput] = useState("");
  const [showCronForm, setShowCronForm] = useState(false);
  const [cronDraft, setCronDraft] = useState<CronDraft>(EMPTY_CRON);
  const [editingCronId, setEditingCronId] = useState<string | null>(null);
  const [showHeartbeatForm, setShowHeartbeatForm] = useState(false);
  const [heartbeatDraft, setHeartbeatDraft] = useState<HeartbeatConfiguration>({
    enabled: false,
    everySeconds: 1800,
    prompt: "Review current tasks and report only information that needs operator attention.",
    activeHours: { start: null, end: null, timezone: "user" },
  });
  const [auditQuery, setAuditQuery] = useState("");
  const [auditOutcomeFilter, setAuditOutcomeFilter] = useState("all");

  async function refresh(): Promise<void> {
    setLoading(true);
    setError(null);
    try {
      const next = await window.ppxClient.getOperationsDashboard();
      setDashboard(next);
      if (!showHeartbeatForm && next.heartbeat.configuration) setHeartbeatDraft(next.heartbeat.configuration);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const selectedTask = dashboard?.tasks.items.find((item) => item.taskId === selectedTaskId) ?? null;
  const filteredAudit = useMemo(() => {
    const query = auditQuery.trim().toLowerCase();
    return (dashboard?.audit ?? []).filter((item) => {
      const matchesText = !query || `${item.actionId} ${item.actorId} ${auditOutcome(item)}`.toLowerCase().includes(query);
      const matchesOutcome = auditOutcomeFilter === "all" || (auditOutcomeFilter === "succeeded" ? item.ok === true : auditOutcomeFilter === "failed" ? item.ok === false : item.ok === null);
      return matchesText && matchesOutcome;
    });
  }, [auditOutcomeFilter, auditQuery, dashboard?.audit]);

  async function runTaskAction(
    task: OperationsTaskItem,
    action: OperationsTaskControlAction | "inspect_output",
    content = "",
  ): Promise<void> {
    const key = `${task.taskId}:${action}`;
    setWorkingId(key);
    setError(null);
    try {
      if (action === "inspect_output") {
        const result = await window.ppxClient.getOperationsTaskOutput(task.taskId);
        setTaskOutput(String(result.output ?? result.message ?? "No retained output."));
        setSelectedTaskId(task.taskId);
      } else {
        await window.ppxClient.controlOperationsTask({
          taskId: task.taskId,
          action,
          ...(action === "send_input" ? { content: content.trim() } : {}),
        });
        if (action === "send_input") setTaskInput("");
        await refresh();
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkingId(null);
    }
  }

  async function createCron(): Promise<void> {
    const selectedAgent = props.agents.find((agent) => agent.id === props.selectedAgentId) ?? props.agents[0];
    if (!selectedAgent) {
      setError("Create an Agent before adding an automation.");
      return;
    }
    let schedule: CronCreateInput["schedule"];
    if (cronDraft.kind === "every") {
      const minutes = Number(cronDraft.everyMinutes);
      if (!Number.isFinite(minutes) || minutes <= 0) {
        setError("Interval must be greater than zero.");
        return;
      }
      schedule = { kind: "every", everySeconds: Math.round(minutes * 60) };
    } else if (cronDraft.kind === "at") {
      const atMs = new Date(cronDraft.at).getTime();
      if (!Number.isFinite(atMs)) {
        setError("Choose a valid run time.");
        return;
      }
      schedule = { kind: "at", atMs };
    } else {
      schedule = { kind: "cron", cronExpression: cronDraft.cronExpression.trim(), timezone: cronDraft.timezone.trim() || "UTC" };
    }
    setWorkingId("cron:create");
    setError(null);
    try {
      const input: CronCreateInput = {
        name: cronDraft.name.trim(),
        agentId: selectedAgent.id,
        userId: props.userId,
        message: cronDraft.message.trim(),
        schedule,
        deleteAfterRun: cronDraft.kind === "at",
      };
      if (editingCronId) await window.ppxClient.updateOperationsCron({ ...input, jobId: editingCronId });
      else await window.ppxClient.createOperationsCron(input);
      setCronDraft(EMPTY_CRON);
      setEditingCronId(null);
      setShowCronForm(false);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkingId(null);
    }
  }

  function editCron(job: OperationsDashboard["cron"]["items"][number]): void {
    setEditingCronId(job.id);
    setCronDraft({
      name: job.name,
      message: job.message,
      kind: job.schedule.kind,
      everyMinutes: String(Math.max(1, Math.round((job.schedule.everySeconds ?? 60) / 60))),
      cronExpression: job.schedule.cronExpr ?? "0 9 * * *",
      timezone: job.schedule.tz ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? "UTC",
      at: job.schedule.atMs ? new Date(job.schedule.atMs).toISOString().slice(0, 16) : "",
    });
    setShowCronForm(true);
  }

  async function saveHeartbeat(): Promise<void> {
    setWorkingId("heartbeat:configure");
    setError(null);
    try {
      await window.ppxClient.configureOperationsHeartbeat(heartbeatDraft);
      setShowHeartbeatForm(false);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkingId(null);
    }
  }

  async function mutateCron(jobId: string, operation: "enable" | "disable" | "run" | "remove"): Promise<void> {
    setWorkingId(`cron:${jobId}:${operation}`);
    setError(null);
    try {
      if (operation === "run") await window.ppxClient.runOperationsCron(jobId);
      else if (operation === "remove") await window.ppxClient.removeOperationsCron(jobId);
      else await window.ppxClient.setOperationsCronEnabled(jobId, operation === "enable");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkingId(null);
    }
  }

  async function runHeartbeat(): Promise<void> {
    setWorkingId("heartbeat");
    setError(null);
    try {
      await window.ppxClient.runOperationsHeartbeat();
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkingId(null);
    }
  }

  return (
    <div className="operations-workspace">
      <nav className="operations-subnav" aria-label="Operations sections">
        {(["overview", "tasks", "automations", "usage", "audit"] as const).map((item) => (
          <button key={item} className={section === item ? "active" : ""} onClick={() => setSection(item)}>
            {item[0].toUpperCase() + item.slice(1)}
            {item === "tasks" && dashboard?.tasks.items.length ? <span>{dashboard.tasks.items.length}</span> : null}
          </button>
        ))}
        <button className="operations-refresh" onClick={() => void refresh()} disabled={loading}>{loading ? "Refreshing" : "Refresh"}</button>
      </nav>

      {error ? <p className="settings-inline-error settings-operations-error">{error}</p> : null}

      {section === "overview" ? (
        <>
          <section className="settings-card settings-card-runtime">
            <div className="settings-runtime-copy"><h3>Runtime</h3><p>{props.runtime.summary}</p></div>
            <div className="runtime-actions settings-runtime-actions"><button onClick={props.onRuntimeAction}>{runtimeActionLabel(props.runtime.state)}</button><button className="secondary" onClick={props.onStopRuntime}>Stop</button></div>
          </section>
          <section className="settings-card operations-metrics">
            <div><span>Node</span><strong className={`operations-state ${dashboard?.overview.state ?? "unavailable"}`}>{dashboard?.overview.state ?? "unavailable"}</strong></div>
            <div><span>Durable tasks</span><strong>{formatCount(dashboard?.overview.tasks.total ?? 0)}</strong></div>
            <div><span>Automations</span><strong>{formatCount(dashboard?.overview.automation.cronJobs ?? 0)}</strong></div>
            <div><span>Tokens</span><strong>{formatCount(dashboard?.usage.totalTokens ?? 0)}</strong></div>
          </section>
          <section className="settings-card settings-card-health">
            <div className="settings-card-heading"><div><h3>Node health</h3><p>Runtime, storage, credentials, Extensions, and isolation.</p></div></div>
            <div className="operations-component-list">
              {dashboard?.overview.components.map((component) => (
                <article className="operations-component" key={component.component}>
                  <span className={`operations-component-dot ${component.state}`} />
                  <div><span>{component.component}</span><p>{component.reason}</p>{component.remediation ? <small>{component.remediation}</small> : null}</div>
                  <em>{component.state}</em>
                </article>
              )) ?? <p className="extension-empty">Health information is not available yet.</p>}
            </div>
          </section>
        </>
      ) : null}

      {section === "tasks" ? (
        <section className="settings-card operations-list-card">
          <div className="settings-card-heading"><div><h3>Durable Tasks</h3><p>Inspect progress, checkpoints, errors, output, and supported controls.</p></div></div>
          <div className="operations-task-list">
            {dashboard?.tasks.items.length ? dashboard.tasks.items.map((task) => (
              <article className={`operations-task-row ${selectedTaskId === task.taskId ? "selected" : ""}`} key={task.taskId} onClick={() => { setSelectedTaskId(task.taskId); setTaskOutput(null); setTaskInput(""); }}>
                <span className={`operations-task-status ${task.status}`} />
                <div className="operations-task-copy"><strong>{task.title || task.kind}</strong><p>{task.progressSummary || task.terminalSummary || task.lastError || "No progress update."}</p><small>{task.kind} · {formatTime(task.updatedAtMs)}</small></div>
                <span className="operations-task-state">{task.status.replaceAll("_", " ")}</span>
              </article>
            )) : <p className="extension-empty">No durable Tasks have been recorded.</p>}
          </div>
          {selectedTask ? (
            <div className="operations-task-detail">
              <div><strong>{selectedTask.title || selectedTask.taskId}</strong><p>{selectedTask.lastError || selectedTask.terminalSummary || selectedTask.progressSummary}</p></div>
              <div className="extension-actions operations-task-actions">
                {selectedTask.actions.filter((action) => action.enabled && action.action !== "send_input").map((action) => (
                  <button className="secondary" key={action.action} disabled={workingId === `${selectedTask.taskId}:${action.action}`} onClick={(event) => { event.stopPropagation(); void runTaskAction(selectedTask, action.action); }}>
                    {workingId === `${selectedTask.taskId}:${action.action}` ? "Working" : action.label || action.action.replaceAll("_", " ")}
                  </button>
                ))}
              </div>
              {selectedTask.actions.some((action) => action.enabled && action.action === "send_input") ? (
                <div className="operations-task-input">
                  <label className="settings-field">
                    <span>Task input</span>
                    <textarea
                      aria-label="Task input"
                      placeholder="Provide the information this Task is waiting for"
                      value={taskInput}
                      onChange={(event) => setTaskInput(event.target.value)}
                    />
                  </label>
                  <button
                    disabled={!taskInput.trim() || workingId === `${selectedTask.taskId}:send_input`}
                    onClick={() => void runTaskAction(selectedTask, "send_input", taskInput)}
                  >
                    {workingId === `${selectedTask.taskId}:send_input` ? "Sending" : "Send input"}
                  </button>
                </div>
              ) : null}
              {taskOutput ? <pre className="operations-output">{taskOutput}</pre> : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {section === "automations" ? (
        <>
          <section className="settings-card operations-heartbeat-card">
            <div><h3>Heartbeat</h3><p>{dashboard?.heartbeat.enabled ? `Every ${Math.round((dashboard.heartbeat.intervalMs ?? 0) / 60_000)} min` : "Interval automation is disabled"}</p><small>Last run: {formatTime(dashboard?.heartbeat.lastRunAtMs)} · {dashboard?.heartbeat.lastStatus ?? "never"}</small></div>
            <div className="extension-actions"><button className="secondary" onClick={() => setShowHeartbeatForm((current) => !current)}>{showHeartbeatForm ? "Cancel" : "Configure"}</button><button className="secondary" onClick={() => void runHeartbeat()} disabled={workingId === "heartbeat"}>{workingId === "heartbeat" ? "Running" : "Run now"}</button></div>
          </section>
          {showHeartbeatForm ? (
            <section className="settings-card operations-heartbeat-form">
              <div className="settings-card-heading"><div><h3>Heartbeat policy</h3><p>Changes are persisted to NodeConfig and take effect after a Node restart.</p></div></div>
              <div className="operations-cron-form">
                <label className="settings-field"><span>State</span><select value={heartbeatDraft.enabled ? "enabled" : "disabled"} onChange={(event) => setHeartbeatDraft((current) => ({ ...current, enabled: event.target.value === "enabled" }))}><option value="enabled">Enabled</option><option value="disabled">Disabled</option></select></label>
                <label className="settings-field"><span>Every (minutes)</span><input type="number" min="1" value={Math.round(heartbeatDraft.everySeconds / 60)} onChange={(event) => setHeartbeatDraft((current) => ({ ...current, everySeconds: Math.max(30, Math.round(Number(event.target.value || 1) * 60)) }))} /></label>
                <label className="settings-field"><span>Active from</span><input type="time" value={heartbeatDraft.activeHours.start ?? ""} onChange={(event) => setHeartbeatDraft((current) => ({ ...current, activeHours: { ...current.activeHours, start: event.target.value || null } }))} /></label>
                <label className="settings-field"><span>Active until</span><input type="time" value={heartbeatDraft.activeHours.end ?? ""} onChange={(event) => setHeartbeatDraft((current) => ({ ...current, activeHours: { ...current.activeHours, end: event.target.value || null } }))} /></label>
                <label className="settings-field"><span>Timezone</span><input value={heartbeatDraft.activeHours.timezone} onChange={(event) => setHeartbeatDraft((current) => ({ ...current, activeHours: { ...current.activeHours, timezone: event.target.value } }))} /></label>
                <label className="settings-field operations-cron-message"><span>Heartbeat instruction</span><textarea value={heartbeatDraft.prompt} onChange={(event) => setHeartbeatDraft((current) => ({ ...current, prompt: event.target.value }))} /></label>
                <div className="extension-actions"><button onClick={() => void saveHeartbeat()} disabled={!heartbeatDraft.prompt.trim() || workingId === "heartbeat:configure"}>{workingId === "heartbeat:configure" ? "Saving" : "Save policy"}</button></div>
              </div>
            </section>
          ) : null}
          <section className="settings-card operations-list-card">
            <div className="settings-card-heading"><div><h3>Schedules</h3><p>Agent-scoped recurring and one-time work.</p></div><button onClick={() => { if (showCronForm) { setShowCronForm(false); setEditingCronId(null); setCronDraft(EMPTY_CRON); } else setShowCronForm(true); }}>{showCronForm ? "Cancel" : "New schedule"}</button></div>
            {showCronForm ? (
              <div className="operations-cron-form">
                <label className="settings-field"><span>Name</span><input value={cronDraft.name} onChange={(event) => setCronDraft((current) => ({ ...current, name: event.target.value }))} /></label>
                <label className="settings-field"><span>Schedule type</span><select value={cronDraft.kind} onChange={(event) => setCronDraft((current) => ({ ...current, kind: event.target.value as CronDraft["kind"] }))}><option value="every">Interval</option><option value="cron">Cron expression</option><option value="at">One time</option></select></label>
                {cronDraft.kind === "every" ? <label className="settings-field"><span>Every (minutes)</span><input type="number" min="1" value={cronDraft.everyMinutes} onChange={(event) => setCronDraft((current) => ({ ...current, everyMinutes: event.target.value }))} /></label> : null}
                {cronDraft.kind === "cron" ? <><label className="settings-field"><span>Cron expression</span><input value={cronDraft.cronExpression} onChange={(event) => setCronDraft((current) => ({ ...current, cronExpression: event.target.value }))} /></label><label className="settings-field"><span>Timezone</span><input value={cronDraft.timezone} onChange={(event) => setCronDraft((current) => ({ ...current, timezone: event.target.value }))} /></label></> : null}
                {cronDraft.kind === "at" ? <label className="settings-field"><span>Run at</span><input type="datetime-local" value={cronDraft.at} onChange={(event) => setCronDraft((current) => ({ ...current, at: event.target.value }))} /></label> : null}
                <label className="settings-field operations-cron-message"><span>Agent instruction</span><textarea value={cronDraft.message} onChange={(event) => setCronDraft((current) => ({ ...current, message: event.target.value }))} /></label>
                <div className="extension-actions"><button onClick={() => void createCron()} disabled={!cronDraft.name.trim() || !cronDraft.message.trim() || workingId === "cron:create"}>{workingId === "cron:create" ? "Saving" : editingCronId ? "Save schedule" : "Create schedule"}</button></div>
              </div>
            ) : null}
            <div className="operations-cron-list">
              {dashboard?.cron.items.length ? dashboard.cron.items.map((job) => (
                <article className="operations-cron-row" key={job.id}>
                  <span className={`operations-task-status ${job.enabled ? "running" : "paused"}`} />
                  <div><strong>{job.name}</strong><p>{scheduleLabel(job.schedule)}</p><small>{job.agentId ?? "No Agent"} · Next: {formatTime(job.state.nextRunAtMs)}</small></div>
                  <div className="extension-actions"><button className="secondary" onClick={() => editCron(job)}>Edit</button><button className="secondary" onClick={() => void mutateCron(job.id, "run")}>Run</button><button className="secondary" onClick={() => void mutateCron(job.id, job.enabled ? "disable" : "enable")}>{job.enabled ? "Disable" : "Enable"}</button><button className="danger secondary" onClick={() => void mutateCron(job.id, "remove")}>Remove</button></div>
                </article>
              )) : <p className="extension-empty">No schedules configured.</p>}
            </div>
          </section>
        </>
      ) : null}

      {section === "usage" ? (
        <>
          <section className="settings-card operations-metrics operations-usage-metrics">
            <div><span>Requests</span><strong>{formatCount(dashboard?.usage.requests ?? 0)}</strong></div><div><span>Input tokens</span><strong>{formatCount(dashboard?.usage.requestTokens ?? 0)}</strong></div><div><span>Output tokens</span><strong>{formatCount(dashboard?.usage.responseTokens ?? 0)}</strong></div><div><span>Total tokens</span><strong>{formatCount(dashboard?.usage.totalTokens ?? 0)}</strong></div>
          </section>
          <section className="settings-card operations-list-card"><div className="settings-card-heading"><div><h3>Recent model usage</h3><p>Node-local usage facts grouped by response.</p></div></div><div className="operations-usage-list">{dashboard?.usage.recent.length ? dashboard.usage.recent.map((item, index) => <article key={`${String(item.responseAt ?? "usage")}-${index}`}><div><strong>{String(item.model ?? "Unknown model")}</strong><p>{String(item.provider ?? "Unknown provider")} · {formatTime(item.responseAt as string | undefined)}</p></div><span>{formatCount(Number(item.totalTokens ?? 0))} tokens</span></article>) : <p className="extension-empty">No model usage has been recorded.</p>}</div></section>
        </>
      ) : null}

      {section === "audit" ? (
        <section className="settings-card operations-list-card">
          <div className="settings-card-heading"><div><h3>Action audit</h3><p>Redacted decisions and outcomes. Request and result payloads are never stored here.</p></div></div>
          <div className="operations-audit-filters"><input aria-label="Filter audit" placeholder="Filter by action or actor" value={auditQuery} onChange={(event) => setAuditQuery(event.target.value)} /><select aria-label="Filter outcome" value={auditOutcomeFilter} onChange={(event) => setAuditOutcomeFilter(event.target.value)}><option value="all">All outcomes</option><option value="succeeded">Succeeded</option><option value="failed">Failed</option><option value="pending">Pending</option></select></div>
          <div className="operations-audit-list">{filteredAudit.length ? filteredAudit.map((item) => <article className="operations-audit-row" key={item.id}><div><span>{item.actionId}</span><p>{item.actorId} · {formatTime(item.recordedAt)}</p></div><span className={`operations-audit-outcome ${item.ok === false ? "failed" : item.ok === true ? "succeeded" : "pending"}`}>{auditOutcome(item)}</span><small>{item.risk} risk</small></article>) : <p className="extension-empty">No matching audit activity.</p>}</div>
        </section>
      ) : null}
    </div>
  );
}
