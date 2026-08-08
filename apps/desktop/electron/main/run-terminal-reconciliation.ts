import type { ChatMessage } from "../../app/src/types";

export interface PersistedTerminalRunMonitorOptions {
  reconcile: () => Promise<boolean>;
  signal?: AbortSignal;
  intervalMs?: number;
  wait?: (delayMs: number, signal?: AbortSignal) => Promise<void>;
}

function waitForDelay(delayMs: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const timeout = setTimeout(finish, delayMs);
    const onAbort = (): void => finish();

    function finish(): void {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }

    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Return the persisted terminal assistant message for a specific Run, if present. */
export function findPersistedTerminalRunMessage(messages: ChatMessage[], runId: string): ChatMessage | null {
  return messages.find((message) => (
    message.role === "assistant"
    && message.runId === runId
    && ["completed", "failed", "cancelled"].includes(message.status)
  )) ?? null;
}

/** Reconcile an active SSE Run against durable Session history until it reaches a terminal state. */
export async function monitorPersistedTerminalRun(
  options: PersistedTerminalRunMonitorOptions,
): Promise<boolean> {
  const intervalMs = Math.max(1, options.intervalMs ?? 3_000);
  const wait = options.wait ?? waitForDelay;
  while (!options.signal?.aborted) {
    await wait(intervalMs, options.signal);
    if (options.signal?.aborted) {
      return false;
    }
    if (await options.reconcile()) {
      return true;
    }
  }
  return false;
}
