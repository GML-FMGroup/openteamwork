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

export type AuthoritativeRunStatus = "running" | "completed" | "failed" | "cancelled";

/** Return whether the Node-owned outer Run has reached a terminal lifecycle state. */
export function isTerminalRunStatus(value: unknown): value is Exclude<AuthoritativeRunStatus, "running"> {
  return value === "completed" || value === "failed" || value === "cancelled";
}

/** Return the latest durable assistant subturn for a specific outer Run. */
export function findLatestPersistedRunMessage(messages: ChatMessage[], runId: string): ChatMessage | null {
  return messages.filter((message) => (
    message.role === "assistant"
    && message.runId === runId
  )).at(-1) ?? null;
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
