import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  summarizeActivityGroups,
  type ActivityEntry,
  type ActivityGroup,
} from "../lib/activity-presentation";
import { ShellIcon } from "./workspace/ContextSidebar";

export type ActivityNarrativeItem =
  | { id: string; kind: "commentary"; text: string }
  | { id: string; kind: "activity"; groups: ActivityGroup[] };

function totalActivityCount(groups: ActivityGroup[]): number {
  return groups.reduce((total, group) => total + group.count, 0);
}

function technicalDetails(groups: ActivityGroup[]): string {
  return groups
    .flatMap((group) => group.entries.map((entry) => entry.rawDetail))
    .filter(Boolean)
    .join("\n\n");
}

function phaseTitle(groups: ActivityGroup[]): string {
  const keys = new Set(groups.map((group) => group.key));
  const labels: string[] = [];
  if (keys.has("goal")) labels.push("Checked task state");
  if (keys.has("capabilities")) labels.push("Prepared tools");
  if (keys.has("web-search") || keys.has("web-read")) labels.push("Researched the web");
  if (keys.has("file-read") || keys.has("file-change")) labels.push("Worked with files");
  if (keys.has("command")) labels.push("Ran commands");
  if (!labels.length) {
    labels.push("Used connected tools");
  }
  const visible = labels.slice(0, 2);
  return labels.length > visible.length
    ? `${visible.join(", ")} +${labels.length - visible.length}`
    : visible.join(", ");
}

function formatElapsedTime(startedAt?: string, endedAt?: string): string | null {
  if (!startedAt || !endedAt) return null;
  const startedAtMs = Date.parse(startedAt);
  const endedAtMs = Date.parse(endedAt);
  if (!Number.isFinite(startedAtMs) || !Number.isFinite(endedAtMs)) return null;
  const elapsedSeconds = Math.max(0, Math.floor((endedAtMs - startedAtMs) / 1000));
  if (elapsedSeconds < 1) return "<1s";
  if (elapsedSeconds < 60) return `${elapsedSeconds}s`;
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = elapsedSeconds % 60;
  return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

function ActivityActionDisclosure({ entry }: { entry: ActivityEntry }) {
  const [open, setOpen] = useState(false);
  const label = entry.status === "running" ? entry.runningLabel : entry.label;
  if (!entry.details.length) {
    return (
      <div className={`activity-semantic-row ${entry.status}`}>
        <span className={`activity-semantic-marker ${entry.status}`} />
        <span className="activity-semantic-copy">
          <strong>{label}</strong>
          {entry.detail ? <small>{entry.detail}</small> : null}
        </span>
      </div>
    );
  }

  return (
    <details className={`activity-action ${entry.status}`} open={open}>
      <summary
        aria-label={`${open ? "Collapse" : "Expand"} ${label}`}
        aria-expanded={open}
        role="button"
        onClick={(event) => {
          event.preventDefault();
          setOpen((current) => !current);
        }}
      >
        <span className={`activity-semantic-marker ${entry.status}`} />
        <span className="activity-semantic-copy">
          <strong>{label}</strong>
          {entry.detail ? <small>{entry.detail}</small> : null}
        </span>
        <span className="activity-action-chevron" aria-hidden><ShellIcon name="expand" /></span>
      </summary>
      {open ? (
        <dl className="activity-action-details">
          {entry.details.map((item, index) => (
            <div className={`activity-action-detail ${item.kind}`} key={`${item.label}:${index}`}>
              <dt>{item.label}</dt>
              <dd className={item.kind === "command" || item.kind === "file" || item.kind === "url" ? "monospace" : undefined}>
                <span>{item.value}</span>
                {item.href ? (
                  <button
                    type="button"
                    onClick={() => void window.ppxClient.openExternalUrl(item.href!)}
                  >Open source</button>
                ) : null}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </details>
  );
}

function ActivityPhaseDisclosure({ groups }: { groups: ActivityGroup[] }) {
  const [open, setOpen] = useState(false);
  const hasFailure = groups.some((group) => group.status === "failed");
  const hasRunning = groups.some((group) => group.status === "running");
  return (
    <details className="activity-phase" open={open}>
      <summary
        aria-label={`${open ? "Collapse" : "Expand"} ${phaseTitle(groups)}`}
        aria-expanded={open}
        role="button"
        onClick={(event) => {
          event.preventDefault();
          setOpen((current) => !current);
        }}
      >
        <span className={`activity-phase-icon ${hasFailure ? "failed" : hasRunning ? "running" : "completed"}`} aria-hidden>
          <ShellIcon name="settings" />
        </span>
        <span className="activity-phase-copy">
          <strong>{phaseTitle(groups)}</strong>
          <small>{summarizeActivityGroups(groups)}</small>
        </span>
        <span className="activity-phase-chevron" aria-hidden><ShellIcon name="expand" /></span>
      </summary>
      {open ? (
        <div className="activity-semantic-list">
          {groups.flatMap((group) => group.entries.map((entry) => (
            <ActivityActionDisclosure entry={entry} key={entry.id} />
          )))}
        </div>
      ) : null}
    </details>
  );
}

/** Compact, progressively disclosed presentation for one Agent work turn. */
export function ActivityDisclosure({
  groups,
  narrative,
  streaming,
  startedAt,
  endedAt,
}: {
  groups: ActivityGroup[];
  narrative?: ActivityNarrativeItem[];
  streaming: boolean;
  startedAt?: string;
  endedAt?: string;
}) {
  const [open, setOpen] = useState(streaming);
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const [observedEndedAt, setObservedEndedAt] = useState(endedAt);
  const wasStreaming = useRef(streaming);

  useEffect(() => {
    if (streaming) {
      setOpen(true);
      setObservedEndedAt(undefined);
    } else if (wasStreaming.current) {
      setOpen(false);
      setObservedEndedAt(endedAt ?? new Date().toISOString());
    } else if (endedAt) {
      setObservedEndedAt(endedAt);
    }
    wasStreaming.current = streaming;
  }, [endedAt, streaming]);

  useEffect(() => {
    if (!open) setTechnicalOpen(false);
  }, [open]);

  const narrativeItems = narrative?.length
    ? narrative
    : [{ id: "activity", kind: "activity" as const, groups }];
  const allGroups = narrativeItems.flatMap((item) => item.kind === "activity" ? item.groups : []);
  if (!allGroups.length) return null;
  const count = totalActivityCount(allGroups);
  const summary = summarizeActivityGroups(allGroups);
  const hasFailure = allGroups.some((group) => group.status === "failed");
  const technical = technicalDetails(allGroups);
  const elapsed = formatElapsedTime(startedAt, observedEndedAt);
  const title = streaming
    ? "Working"
    : elapsed
      ? `Worked for ${elapsed}`
      : hasFailure
        ? "Work completed with issues"
        : "Work completed";

  return (
    <details className={`activity-disclosure ${streaming ? "running" : "complete"}`} open={open}>
      <summary
        aria-label={`${streaming ? "Running" : "Completed"} ${count} ${count === 1 ? "action" : "actions"}`}
        aria-expanded={open}
        role="button"
        onClick={(event) => {
          event.preventDefault();
          setOpen((current) => !current);
        }}
      >
        <span className={`activity-disclosure-marker ${hasFailure ? "failed" : streaming ? "running" : "completed"}`} />
        <span className="activity-disclosure-copy">
          <strong aria-live={streaming ? "polite" : undefined}>{title}</strong>
          {!open ? <small>{summary}</small> : null}
        </span>
        <span className="activity-disclosure-chevron" aria-hidden><ShellIcon name="expand" /></span>
      </summary>
      {open ? <div className="activity-disclosure-body">
        <div className="activity-narrative" aria-live={streaming ? "polite" : undefined}>
          {narrativeItems.map((item) => item.kind === "commentary" ? (
            <div className="run-commentary" key={item.id}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
            </div>
          ) : (
            <ActivityPhaseDisclosure groups={item.groups} key={item.id} />
          ))}
        </div>
        {technical ? (
          <details className="activity-technical" open={technicalOpen}>
            <summary
              onClick={(event) => {
                event.preventDefault();
                setTechnicalOpen((current) => !current);
              }}
            >Technical details</summary>
            {technicalOpen ? <pre>{technical}</pre> : null}
          </details>
        ) : null}
      </div> : null}
    </details>
  );
}
