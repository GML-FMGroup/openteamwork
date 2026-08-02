import { useCallback, useMemo, useState } from "react";
import type { SessionSummary } from "../types";

interface ActiveRunState {
  sessionId: string;
  runId?: string;
}

/** Track active Runs by Session without resurrecting a Run that already finished. */
export function useActiveRuns() {
  const [runsBySession, setRunsBySession] = useState<Record<string, ActiveRunState>>({});

  const begin = useCallback((sessionId: string) => {
    setRunsBySession((current) => ({ ...current, [sessionId]: { sessionId } }));
  }, []);

  const attachRunId = useCallback((sessionId: string, runId: string) => {
    setRunsBySession((current) => {
      if (!Object.hasOwn(current, sessionId)) {
        return current;
      }
      return { ...current, [sessionId]: { sessionId, runId } };
    });
  }, []);

  const finish = useCallback((sessionId: string) => {
    setRunsBySession((current) => {
      if (!Object.hasOwn(current, sessionId)) {
        return current;
      }
      const next = { ...current };
      delete next[sessionId];
      return next;
    });
  }, []);

  const sessionIds = useMemo(() => Object.keys(runsBySession), [runsBySession]);
  const isSessionRunning = useCallback(
    (sessionId: string) => Object.hasOwn(runsBySession, sessionId),
    [runsBySession],
  );
  const isAgentRunning = useCallback(
    (agentId: string, sessions: SessionSummary[]) =>
      sessions.some((session) => session.agentId === agentId && Object.hasOwn(runsBySession, session.id)),
    [runsBySession],
  );
  const runIdForSession = useCallback(
    (sessionId: string) => runsBySession[sessionId]?.runId,
    [runsBySession],
  );

  return {
    begin,
    attachRunId,
    finish,
    sessionIds,
    isSessionRunning,
    isAgentRunning,
    runIdForSession,
  };
}
