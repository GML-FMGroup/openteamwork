import { useCallback, useEffect, useRef } from "react";


interface IdleSessionOptions {
  enabled: boolean;
  initialExpiresAtMs?: number;
  hasActiveRuns: boolean;
  recordActivity: () => Promise<{ expiresAtMs: number }>;
  onExpire: () => void | Promise<void>;
  activityThrottleMs?: number;
}

const DEFAULT_ACTIVITY_THROTTLE_MS = 30_000;
const SESSION_IDLE_MS = 60 * 60 * 1000;

/** Keep one authenticated Desktop session alive only from input or owned Run work. */
export function useIdleSession({
  enabled,
  initialExpiresAtMs,
  hasActiveRuns,
  recordActivity,
  onExpire,
  activityThrottleMs = DEFAULT_ACTIVITY_THROTTLE_MS,
}: IdleSessionOptions): void {
  const deadlineRef = useRef(0);
  const expiryTimerRef = useRef<number | null>(null);
  const activityTimerRef = useRef<number | null>(null);
  const lastRecordedAtRef = useRef(0);
  const recordingRef = useRef(false);
  const expiredRef = useRef(false);
  const activeRunsRef = useRef(hasActiveRuns);
  const previousActiveRunsRef = useRef(hasActiveRuns);
  const enabledRef = useRef(enabled);
  const recordActivityRef = useRef(recordActivity);
  const onExpireRef = useRef(onExpire);

  enabledRef.current = enabled;
  activeRunsRef.current = hasActiveRuns;
  recordActivityRef.current = recordActivity;
  onExpireRef.current = onExpire;

  const clearExpiryTimer = useCallback((): void => {
    if (expiryTimerRef.current !== null) {
      window.clearTimeout(expiryTimerRef.current);
      expiryTimerRef.current = null;
    }
  }, []);

  const expire = useCallback((): void => {
    if (!enabledRef.current || activeRunsRef.current || expiredRef.current) return;
    expiredRef.current = true;
    clearExpiryTimer();
    void onExpireRef.current();
  }, [clearExpiryTimer]);

  const scheduleExpiry = useCallback((): void => {
    clearExpiryTimer();
    if (
      !enabledRef.current
      || activeRunsRef.current
      || expiredRef.current
      || deadlineRef.current <= 0
    ) {
      return;
    }
    const remainingMs = deadlineRef.current - Date.now();
    if (remainingMs <= 0) {
      expire();
      return;
    }
    expiryTimerRef.current = window.setTimeout(expire, remainingMs);
  }, [clearExpiryTimer, expire]);

  const flushActivity = useCallback(async (): Promise<void> => {
    if (!enabledRef.current || expiredRef.current || recordingRef.current) return;
    recordingRef.current = true;
    try {
      const result = await recordActivityRef.current();
      if (!Number.isSafeInteger(result.expiresAtMs) || result.expiresAtMs <= Date.now()) {
        expire();
        return;
      }
      deadlineRef.current = result.expiresAtMs;
      lastRecordedAtRef.current = Date.now();
      scheduleExpiry();
    } catch {
      if (deadlineRef.current > 0 && deadlineRef.current <= Date.now()) expire();
    } finally {
      recordingRef.current = false;
    }
  }, [expire, scheduleExpiry]);

  const queueActivity = useCallback((): void => {
    if (!enabledRef.current || expiredRef.current || activityTimerRef.current !== null) return;
    const waitMs = Math.max(
      0,
      lastRecordedAtRef.current + Math.max(0, activityThrottleMs) - Date.now(),
    );
    activityTimerRef.current = window.setTimeout(() => {
      activityTimerRef.current = null;
      void flushActivity();
    }, waitMs);
  }, [activityThrottleMs, flushActivity]);

  useEffect(() => {
    if (!enabled) {
      expiredRef.current = false;
      deadlineRef.current = 0;
      lastRecordedAtRef.current = 0;
      clearExpiryTimer();
      if (activityTimerRef.current !== null) {
        window.clearTimeout(activityTimerRef.current);
        activityTimerRef.current = null;
      }
      return;
    }
    if (Number.isSafeInteger(initialExpiresAtMs) && Number(initialExpiresAtMs) > 0) {
      deadlineRef.current = Number(initialExpiresAtMs);
    }
    expiredRef.current = false;
    scheduleExpiry();
  }, [clearExpiryTimer, enabled, initialExpiresAtMs, scheduleExpiry]);

  useEffect(() => {
    const wasActive = previousActiveRunsRef.current;
    previousActiveRunsRef.current = hasActiveRuns;
    if (!enabled) return;
    if (hasActiveRuns) {
      clearExpiryTimer();
      return;
    }
    if (wasActive) {
      // The server advances the exact owning login before it closes the Run.
      // Mirror that deadline immediately so a transient sync failure cannot
      // sign the user out at the stale pre-Run deadline.
      deadlineRef.current = Date.now() + SESSION_IDLE_MS;
      scheduleExpiry();
      void flushActivity();
      return;
    }
    scheduleExpiry();
  }, [clearExpiryTimer, enabled, flushActivity, hasActiveRuns, scheduleExpiry]);

  useEffect(() => {
    if (!enabled) return;
    const onInput = (): void => queueActivity();
    const onVisibility = (): void => {
      if (document.visibilityState === "visible") {
        if (!activeRunsRef.current && deadlineRef.current <= Date.now()) expire();
        else scheduleExpiry();
      }
    };
    window.addEventListener("pointerdown", onInput, { passive: true });
    window.addEventListener("keydown", onInput);
    window.addEventListener("wheel", onInput, { passive: true });
    window.addEventListener("touchstart", onInput, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pointerdown", onInput);
      window.removeEventListener("keydown", onInput);
      window.removeEventListener("wheel", onInput);
      window.removeEventListener("touchstart", onInput);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, expire, queueActivity, scheduleExpiry]);

  useEffect(() => () => {
    clearExpiryTimer();
    if (activityTimerRef.current !== null) window.clearTimeout(activityTimerRef.current);
  }, [clearExpiryTimer]);
}
