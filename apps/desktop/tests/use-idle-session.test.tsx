import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useIdleSession } from "../app/src/hooks/use-idle-session";


describe("useIdleSession", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-20T00:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("expires once when the authoritative idle deadline elapses", async () => {
    const onExpire = vi.fn(async () => undefined);
    renderHook(() => useIdleSession({
      enabled: true,
      initialExpiresAtMs: Date.now() + 60 * 60 * 1000,
      hasActiveRuns: false,
      recordActivity: vi.fn(),
      onExpire,
    }));

    await act(async () => vi.advanceTimersByTimeAsync(60 * 60 * 1000));

    expect(onExpire).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(60 * 60 * 1000));
    expect(onExpire).toHaveBeenCalledTimes(1);
  });

  it("records deliberate input and replaces the local deadline with the server result", async () => {
    const recordActivity = vi.fn(async () => ({
      expiresAtMs: Date.now() + 60 * 60 * 1000,
    }));
    const onExpire = vi.fn(async () => undefined);
    renderHook(() => useIdleSession({
      enabled: true,
      initialExpiresAtMs: Date.now() + 60 * 60 * 1000,
      hasActiveRuns: false,
      recordActivity,
      onExpire,
      activityThrottleMs: 30_000,
    }));

    await act(async () => vi.advanceTimersByTimeAsync(45 * 60 * 1000));
    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "a" })));
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(recordActivity).toHaveBeenCalledTimes(1);

    await act(async () => vi.advanceTimersByTimeAsync(59 * 60 * 1000));
    expect(onExpire).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(60 * 1000));
    expect(onExpire).toHaveBeenCalledTimes(1);
  });

  it("does not expire during an active Run and restarts the hour when it finishes", async () => {
    const recordActivity = vi.fn(async () => ({
      expiresAtMs: Date.now() + 60 * 60 * 1000,
    }));
    const onExpire = vi.fn(async () => undefined);
    const { rerender } = renderHook(
      ({ hasActiveRuns }) => useIdleSession({
        enabled: true,
        initialExpiresAtMs: Date.parse("2026-08-20T01:00:00.000Z"),
        hasActiveRuns,
        recordActivity,
        onExpire,
      }),
      { initialProps: { hasActiveRuns: true } },
    );

    await act(async () => vi.advanceTimersByTimeAsync(2 * 60 * 60 * 1000));
    expect(onExpire).not.toHaveBeenCalled();

    rerender({ hasActiveRuns: false });
    await act(async () => Promise.resolve());
    expect(recordActivity).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(59 * 60 * 1000));
    expect(onExpire).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(60 * 1000));
    expect(onExpire).toHaveBeenCalledTimes(1);
  });

  it("keeps the post-Run hour when the deadline sync has a transient failure", async () => {
    const recordActivity = vi.fn(async () => Promise.reject(new Error("offline")));
    const onExpire = vi.fn(async () => undefined);
    const { rerender } = renderHook(
      ({ hasActiveRuns }) => useIdleSession({
        enabled: true,
        initialExpiresAtMs: Date.parse("2026-08-20T01:00:00.000Z"),
        hasActiveRuns,
        recordActivity,
        onExpire,
      }),
      { initialProps: { hasActiveRuns: true } },
    );

    await act(async () => vi.advanceTimersByTimeAsync(2 * 60 * 60 * 1000));
    rerender({ hasActiveRuns: false });
    await act(async () => Promise.resolve());

    expect(recordActivity).toHaveBeenCalledTimes(1);
    expect(onExpire).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(59 * 60 * 1000));
    expect(onExpire).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(60 * 1000));
    expect(onExpire).toHaveBeenCalledTimes(1);
  });
});
