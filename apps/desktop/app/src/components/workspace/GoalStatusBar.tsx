import { useEffect, useRef, useState } from "react";
import type { GoalDetail, GoalTransitionOperation } from "../../types";

interface GoalStatusBarProps {
  goal: GoalDetail;
  mutation: "update" | GoalTransitionOperation | null;
  error: string | null;
  onUpdate: (objective: string) => Promise<boolean>;
  onTransition: (operation: GoalTransitionOperation) => Promise<boolean>;
}

const STATUS_COPY = {
  active: "Pursuing goal",
  waiting: "Waiting to continue",
  paused: "Goal paused",
  blocked: "Goal needs attention",
} as const;

function goalStatusCopy(status: GoalDetail["status"]): string {
  return status === "active" || status === "waiting" || status === "paused" || status === "blocked"
    ? STATUS_COPY[status]
    : "Goal finished";
}

function GoalIcon({ name }: { name: "goal" | "edit" | "pause" | "resume" | "cancel" }) {
  const paths = {
    goal: <><circle cx="12" cy="12" r="7" /><circle cx="12" cy="12" r="2.5" /><path d="M12 2v3M22 12h-3" /></>,
    edit: <><path d="m4 20 4.1-1 10.7-10.7a2.1 2.1 0 0 0-3-3L5.1 16 4 20Z" /><path d="m14.7 6.3 3 3" /></>,
    pause: <><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></>,
    resume: <path d="m8 5 10 7-10 7V5Z" />,
    cancel: <><path d="M5 5l14 14M19 5 5 19" /><circle cx="12" cy="12" r="10" /></>,
  } as const;
  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

/** Compact, durable Goal status and controls integrated with the chat composer. */
export function GoalStatusBar({ goal, mutation, error, onUpdate, onTransition }: GoalStatusBarProps) {
  const [editing, setEditing] = useState(false);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [objective, setObjective] = useState(goal.objective);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const busy = mutation !== null;
  const canPause = goal.status === "active";
  const canResume = goal.status === "waiting" || goal.status === "paused" || goal.status === "blocked";

  useEffect(() => {
    setObjective(goal.objective);
    setEditing(false);
    setConfirmingCancel(false);
  }, [goal.goalId, goal.objective]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  async function saveObjective(): Promise<void> {
    if (!objective.trim() || busy) return;
    if (await onUpdate(objective)) setEditing(false);
  }

  return (
    <section className={`goal-status-bar ${goal.status}`} aria-label="Current Goal">
      {editing ? (
        <div className="goal-status-editor">
          <span className="goal-status-symbol"><GoalIcon name="goal" /></span>
          <input
            ref={inputRef}
            value={objective}
            maxLength={16_384}
            aria-label="Goal objective"
            disabled={busy}
            onChange={(event) => setObjective(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") { event.preventDefault(); void saveObjective(); }
              if (event.key === "Escape") { setObjective(goal.objective); setEditing(false); }
            }}
          />
          <button type="button" className="goal-text-action" disabled={busy || !objective.trim()} onClick={() => void saveObjective()}>
            {mutation === "update" ? "Saving…" : "Save"}
          </button>
          <button type="button" className="goal-text-action quiet" disabled={busy} onClick={() => { setObjective(goal.objective); setEditing(false); }}>Cancel</button>
        </div>
      ) : confirmingCancel ? (
        <div className="goal-cancel-confirmation" role="alertdialog" aria-label="Cancel this Goal?">
          <span><strong>Cancel this Goal?</strong><small>This stops the current work and cannot be resumed.</small></span>
          <button type="button" className="goal-text-action quiet" disabled={busy} onClick={() => setConfirmingCancel(false)}>Keep</button>
          <button type="button" className="goal-text-action danger" disabled={busy} onClick={() => void onTransition("cancel")}>
            {mutation === "cancel" ? "Cancelling…" : "Cancel Goal"}
          </button>
        </div>
      ) : (
        <>
          <div className="goal-status-copy">
            <span className="goal-status-symbol"><GoalIcon name="goal" /></span>
            <strong>{goalStatusCopy(goal.status)}</strong>
            <span className="goal-status-objective" title={goal.objective}>{goal.objective}</span>
          </div>
          <div className="goal-status-actions">
            <button type="button" disabled={busy} onClick={() => setEditing(true)} title="Edit Goal">
              <GoalIcon name="edit" /><span>Edit</span>
            </button>
            {canPause ? (
              <button type="button" disabled={busy} onClick={() => void onTransition("pause")} title="Pause Goal">
                <GoalIcon name="pause" /><span>{mutation === "pause" ? "Pausing…" : "Pause"}</span>
              </button>
            ) : null}
            {canResume ? (
              <button type="button" disabled={busy} onClick={() => void onTransition("resume")} title="Resume Goal">
                <GoalIcon name="resume" /><span>{mutation === "resume" ? "Resuming…" : "Resume"}</span>
              </button>
            ) : null}
            <button type="button" disabled={busy} onClick={() => setConfirmingCancel(true)} title="Cancel Goal">
              <GoalIcon name="cancel" /><span>Cancel</span>
            </button>
          </div>
        </>
      )}
      {error ? <p className="goal-status-error" role="status">{error}</p> : null}
    </section>
  );
}
