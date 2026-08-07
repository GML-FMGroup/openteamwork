import type { SessionSummary } from "../types";

interface IndexedSession {
  index: number;
  session: SessionSummary;
  updatedAtMs: number | null;
}

function parsedTimestamp(value: string): number | null {
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? null : timestamp;
}

/**
 * Return a stable, non-mutating Session order with the most recently active Session first.
 *
 * ISO timestamps are parsed into instants before comparison so UTC (`Z`) and explicit local
 * offsets (for example `+08:00`) sort correctly. Invalid timestamps are retained at the end.
 */
export function sortSessionsByRecency(sessions: readonly SessionSummary[]): SessionSummary[] {
  return sessions
    .map<IndexedSession>((session, index) => ({
      index,
      session,
      updatedAtMs: parsedTimestamp(session.updatedAt),
    }))
    .sort((left, right) => {
      if (left.updatedAtMs === null && right.updatedAtMs === null) {
        return left.index - right.index;
      }
      if (left.updatedAtMs === null) {
        return 1;
      }
      if (right.updatedAtMs === null) {
        return -1;
      }
      return right.updatedAtMs - left.updatedAtMs || left.index - right.index;
    })
    .map(({ session }) => session);
}
