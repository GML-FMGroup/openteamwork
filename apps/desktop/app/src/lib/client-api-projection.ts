import type { RuntimeState, RuntimeStatus } from "../types";

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function normalizeRuntimeState(value: unknown): RuntimeState {
  const state = asString(value, "healthy");
  if (state === "stopped" || state === "starting" || state === "healthy" || state === "error") {
    return state;
  }
  return "healthy";
}

/** Normalize the Desktop-specific runtime status returned by the Client API host. */
export function normalizeClientApiRuntime(payload: unknown): RuntimeStatus | null {
  const runtime = asRecord(payload);
  const target = asRecord(runtime?.target);
  if (!runtime || !target) {
    return null;
  }
  return {
    target: {
      id: asString(target.id, "local-default"),
      type: asString(target.type) === "remote" ? "remote" : "local",
      name: asString(target.name, "This Mac"),
    },
    state: normalizeRuntimeState(runtime.state),
    summary: asString(runtime.summary),
    detail: asString(runtime.detail) || undefined,
    lastError: asString(runtime.lastError ?? runtime.last_error) || undefined,
  };
}
