import type { AgentProfile } from "../types";

/** Normalize one Client API Agent projection for Desktop presentation. */
export function normalizeAgentProfile(payload: Record<string, unknown>): AgentProfile {
  return {
    id: String(payload.id ?? ""),
    name: String(payload.name ?? payload.id ?? ""),
    description: String(payload.description ?? "Local openppx agent"),
    enabled: payload.enabled !== false,
    status: (String(payload.status ?? "healthy") as AgentProfile["status"]) || "healthy",
    tags: Array.isArray(payload.tags) ? payload.tags.map((tag) => String(tag)) : [],
  };
}

/** Project only enabled Agents into the workspace and Session surface. */
export function normalizeWorkspaceAgents(items: Array<Record<string, unknown>>): AgentProfile[] {
  return items.map((item) => normalizeAgentProfile(item)).filter((agent) => agent.enabled);
}
