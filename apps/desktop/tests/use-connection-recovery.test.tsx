import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useConnectionRecovery } from "../app/src/hooks/use-connection-recovery";

afterEach(() => vi.useRealTimers());

describe("useConnectionRecovery", () => {
  it("reports one unavailable transition and then recovery", async () => {
    vi.useFakeTimers();
    const check = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const onUnavailable = vi.fn();
    const onRecovered = vi.fn();
    renderHook(() =>
      useConnectionRecovery({ active: true, check, onUnavailable, onRecovered, intervalMs: 100 }),
    );

    await act(async () => vi.advanceTimersByTimeAsync(100));
    expect(onUnavailable).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(100));
    expect(onUnavailable).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(100));
    expect(onRecovered).toHaveBeenCalledTimes(1);
  });

  it("returns to unavailable when recovery hydration fails", async () => {
    vi.useFakeTimers();
    const check = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const onUnavailable = vi.fn();
    renderHook(() =>
      useConnectionRecovery({
        active: true,
        check,
        onUnavailable,
        onRecovered: async () => {
          throw new Error("bootstrap failed");
        },
        intervalMs: 100,
      }),
    );

    await act(async () => vi.advanceTimersByTimeAsync(100));
    await act(async () => vi.advanceTimersByTimeAsync(100));
    expect(onUnavailable).toHaveBeenCalledTimes(2);
  });
});
