import { useState } from "react";
import {
  currentActivityLabel,
  summarizeActivityGroups,
  type ActivityGroup,
} from "../lib/activity-presentation";
import { ShellIcon } from "./workspace/ContextSidebar";

function totalActivityCount(groups: ActivityGroup[]): number {
  return groups.reduce((total, group) => total + group.count, 0);
}

function technicalDetails(groups: ActivityGroup[]): string {
  return groups
    .flatMap((group) => group.entries.map((entry) => entry.rawDetail))
    .filter(Boolean)
    .join("\n\n");
}

/** Compact, progressively disclosed presentation for one Agent work turn. */
export function ActivityDisclosure({
  groups,
  streaming,
}: {
  groups: ActivityGroup[];
  streaming: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (!groups.length) return null;
  const count = totalActivityCount(groups);
  const summary = summarizeActivityGroups(groups);
  const hasFailure = groups.some((group) => group.status === "failed");
  const technical = technicalDetails(groups);

  return (
    <details className={`activity-disclosure ${streaming ? "running" : "complete"}`} open={open}>
      <summary
        aria-label={`${streaming ? "Running" : "Completed"} ${count} ${count === 1 ? "action" : "actions"}`}
        onClick={(event) => {
          event.preventDefault();
          setOpen((current) => !current);
        }}
      >
        <span className={`activity-disclosure-marker ${hasFailure ? "failed" : streaming ? "running" : "completed"}`} />
        <span className="activity-disclosure-copy">
          <strong>{currentActivityLabel(groups, streaming)}</strong>
          <small>{summary}</small>
        </span>
        <span className="activity-disclosure-chevron" aria-hidden><ShellIcon name="expand" /></span>
      </summary>
      {open ? <div className="activity-disclosure-body">
        <div className="activity-semantic-list">
          {groups.map((group) => (
            <div className={`activity-semantic-row ${group.status}`} key={group.key}>
              <span className={`activity-semantic-marker ${group.status}`} />
              <span className="activity-semantic-copy">
                <strong>{group.label}</strong>
                {group.count === 1 && group.entries[0]?.detail ? <small>{group.entries[0].detail}</small> : null}
              </span>
              <span className="activity-semantic-count">{group.countLabel}</span>
            </div>
          ))}
        </div>
        {technical ? (
          <details className="activity-technical">
            <summary>Technical details</summary>
            <pre>{technical}</pre>
          </details>
        ) : null}
      </div> : null}
    </details>
  );
}
