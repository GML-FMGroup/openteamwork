import type { ChatMessage, MessagePart, MessageStatus } from "../types";

type StepPart = Extract<MessagePart, { type: "step_ref" }>;
type ToolResultPart = Extract<MessagePart, { type: "tool_result" }>;

export type ActivityStatus = "running" | "completed" | "failed";

export type ActivityDetailKind = "text" | "query" | "url" | "file" | "command" | "status" | "result";

export interface ActivityDetailItem {
  label: string;
  value: string;
  kind: ActivityDetailKind;
  href?: string;
}

export interface ActivityEntry {
  id: string;
  toolName: string;
  label: string;
  runningLabel: string;
  status: ActivityStatus;
  detail: string;
  details: ActivityDetailItem[];
  rawDetail: string;
  messageId: string;
}

export interface ActivityGroup {
  key: string;
  label: string;
  runningLabel: string;
  status: ActivityStatus;
  count: number;
  countLabel: string;
  messageId: string;
  entries: ActivityEntry[];
}

interface ToolPresentation {
  key: string;
  running: string;
  completed: string;
  failed: string;
  singular: string;
  plural: string;
  detailKeys?: string[];
}

interface PendingEntry {
  id: string;
  toolName: string;
  status: ActivityStatus;
  inputDetail: string;
  outputDetail: string;
  rawOutput: string;
  messageId: string;
}

const RESPONSE_KEYS = new Set([
  "data",
  "error",
  "goal",
  "message",
  "ok",
  "output",
  "result",
  "status",
  "summary",
]);

const INPUT_KEYS = new Set([
  "action",
  "command",
  "file_path",
  "goal",
  "name",
  "path",
  "pattern",
  "prompt",
  "query",
  "resource",
  "server",
  "task",
  "url",
]);

const TARGET_KEYS = [
  "query",
  "q",
  "pattern",
  "url",
  "path",
  "file_path",
  "command",
  "name",
  "goal",
  "objective",
  "task",
  "resource",
  "server",
  "action",
  "prompt",
];

function normalizeToolName(value: string): string {
  return value.trim().replace(/^functions[.:/]/i, "").toLowerCase();
}

function humanizeIdentifier(value: string): string {
  const words = value
    .replace(/^functions[.:/]/i, "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[._/-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  if (!words) return "Connected capability";
  return `${words[0].toUpperCase()}${words.slice(1)}`;
}

function parseDetail(value: string): Record<string, unknown> | null {
  const trimmed = value.trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  try {
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function parseInputDetail(value: string): Record<string, unknown> | null {
  const parsed = parseDetail(value);
  if (parsed) return parsed;
  const entries = value
    .split("\n")
    .map((line) => line.match(/^\s*([a-zA-Z][\w-]*)\s*:\s*(.+?)\s*$/))
    .filter((match): match is RegExpMatchArray => Boolean(match))
    .map((match) => [match[1], match[2]] as const);
  return entries.length ? Object.fromEntries(entries) : null;
}

function stringValue(record: Record<string, unknown> | null, keys: string[]): string {
  if (!record) return "";
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" || typeof value === "boolean") return String(value);
  }
  return "";
}

function compactText(value: string, limit = 88): string {
  const compact = value.replace(/\s+/g, " ").trim();
  if (compact.length <= limit) return compact;
  return `${compact.slice(0, Math.max(0, limit - 1))}…`;
}

function scalarText(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.length ? `${value.length} items` : "";
  if (value && typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return "";
    }
  }
  return "";
}

/** Remove credential-shaped values before activity details are placed in the DOM. */
export function redactActivityText(value: string): string {
  return value
    .replace(
      /(["']?(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|password|secret)["']?\s*[:=]\s*)["']?[^,"'\s}\]]+["']?/gi,
      "$1[redacted]",
    )
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [redacted]");
}

function looksLikeResponse(detail: string): boolean {
  const parsed = parseDetail(detail);
  if (!parsed) return false;
  const keys = Object.keys(parsed).map((key) => key.toLowerCase());
  return keys.some((key) => RESPONSE_KEYS.has(key)) && !keys.some((key) => INPUT_KEYS.has(key));
}

function mergedStatus(current: ActivityStatus, incoming: ActivityStatus): ActivityStatus {
  if (incoming === "running") return "running";
  if (incoming === "failed" || current === "failed") return "failed";
  return "completed";
}

function toolPresentation(toolName: string): ToolPresentation {
  const name = normalizeToolName(toolName);
  if (name === "get_goal") {
    return { key: "goal", running: "Checking the current task", completed: "Checked the current task", failed: "Could not check the current task", singular: "task check", plural: "task checks" };
  }
  if (name === "create_goal") {
    return { key: "goal", running: "Starting a long task", completed: "Started a long task", failed: "Could not start the long task", singular: "task update", plural: "task updates" };
  }
  if (name === "update_goal") {
    return { key: "goal", running: "Updating task progress", completed: "Updated task progress", failed: "Could not update task progress", singular: "task update", plural: "task updates" };
  }
  if (name === "list_skills" || name === "read_skill" || name === "load_skill") {
    return { key: "capabilities", running: "Preparing capabilities", completed: "Prepared capabilities", failed: "Could not prepare capabilities", singular: "capability", plural: "capabilities", detailKeys: ["name"] };
  }
  if (name === "web_search" || name.endsWith("_search")) {
    return { key: "web-search", running: "Searching the web", completed: "Searched the web", failed: "Some web searches failed", singular: "search", plural: "searches", detailKeys: ["query", "q", "pattern"] };
  }
  if (name === "web_fetch" || name === "fetch_url" || name === "open_url") {
    return { key: "web-read", running: "Reading sources", completed: "Read sources", failed: "Could not read some sources", singular: "source", plural: "sources", detailKeys: ["url"] };
  }
  if (name === "exec" || name === "run_shell" || name === "exec_command") {
    return { key: "command", running: "Running a local command", completed: "Ran a local command", failed: "Command blocked by security policy", singular: "command", plural: "commands", detailKeys: ["command"] };
  }
  if (name === "read_file" || name === "read_text_file") {
    return { key: "file-read", running: "Reading a file", completed: "Read a file", failed: "Could not read a file", singular: "file", plural: "files", detailKeys: ["path", "file_path"] };
  }
  if (name === "write_file" || name === "apply_patch" || name === "replace_in_file") {
    return { key: "file-change", running: "Updating files", completed: "Updated files", failed: "Could not update files", singular: "file", plural: "files", detailKeys: ["path", "file_path"] };
  }
  if (name.startsWith("list_")) {
    return { key: name, running: "Checking available items", completed: "Checked available items", failed: "Could not check available items", singular: "check", plural: "checks" };
  }
  if (name.startsWith("read_")) {
    return { key: name, running: "Reading information", completed: "Read information", failed: "Could not read information", singular: "action", plural: "actions" };
  }
  const humanName = humanizeIdentifier(name);
  return {
    key: name || "connected-capability",
    running: `Using ${humanName}`,
    completed: `Used ${humanName}`,
    failed: `Could not use ${humanName}`,
    singular: "action",
    plural: "actions",
  };
}

function activityDetail(entry: PendingEntry, presentation: ToolPresentation): string {
  const parsed = parseInputDetail(entry.inputDetail);
  const keys = presentation.detailKeys?.length ? presentation.detailKeys : TARGET_KEYS;
  let detail = stringValue(parsed, keys);
  if (presentation.key === "web-read" && detail) {
    try {
      detail = new URL(detail).hostname || detail;
    } catch {
      // Keep a safe compact form for non-URL source identifiers.
    }
  }
  return compactText(redactActivityText(detail));
}

function detailKindForKey(key: string, presentation: ToolPresentation): ActivityDetailKind {
  if (key === "query" || key === "q" || key === "pattern") return "query";
  if (key === "url" || presentation.key === "web-read") return "url";
  if (key === "path" || key === "file_path" || presentation.key === "file-read" || presentation.key === "file-change") return "file";
  if (key === "command" || presentation.key === "command") return "command";
  return "text";
}

function detailLabel(key: string, kind: ActivityDetailKind): string {
  if (kind === "query") return "Query";
  if (kind === "url") return "Source";
  if (kind === "file") return "File";
  if (kind === "command") return "Command";
  const labels: Record<string, string> = {
    action: "Action",
    goal: "Goal",
    name: "Capability",
    objective: "Objective",
    prompt: "Prompt",
    resource: "Resource",
    server: "Server",
    task: "Task",
  };
  return labels[key] ?? humanizeIdentifier(key);
}

function targetDetail(entry: PendingEntry, presentation: ToolPresentation): ActivityDetailItem | null {
  const parsed = parseInputDetail(entry.inputDetail);
  if (!parsed) return null;
  const keys = presentation.detailKeys?.length
    ? [...presentation.detailKeys, ...TARGET_KEYS]
    : TARGET_KEYS;
  for (const key of keys) {
    const raw = scalarText(parsed[key]);
    if (!raw) continue;
    const value = compactText(redactActivityText(raw), 220);
    if (!value) continue;
    const kind = detailKindForKey(key, presentation);
    return {
      label: detailLabel(key, kind),
      value,
      kind,
      href: kind === "url" && /^https?:\/\//i.test(value) ? value : undefined,
    };
  }
  return null;
}

function outcomeDetail(entry: PendingEntry, status: ActivityStatus): ActivityDetailItem | null {
  const source = entry.outputDetail || entry.rawOutput;
  if (!source.trim()) return null;
  const parsed = parseDetail(source);
  const failure = status === "failed";
  const keys = failure
    ? ["error", "message", "summary", "result", "output", "status"]
    : ["summary", "message", "status", "result", "output"];
  let value = "";
  if (parsed) {
    for (const key of keys) {
      value = scalarText(parsed[key]);
      if (value) break;
    }
  } else if (source !== entry.inputDetail) {
    value = source;
  }
  value = compactText(redactActivityText(value), 220);
  if (!value || /returned\s+\d+\s+fields?\.?$/i.test(value)) return null;
  return {
    label: failure ? "Error" : "Result",
    value,
    kind: "result",
  };
}

function activityDetails(
  entry: PendingEntry,
  presentation: ToolPresentation,
  status: ActivityStatus,
): ActivityDetailItem[] {
  const target = targetDetail(entry, presentation);
  const outcome = outcomeDetail(entry, status);
  if (!target && !outcome) return [];
  return [
    ...(target ? [target] : []),
    {
      label: "Status",
      value: status === "running" ? "Running" : status === "failed" ? "Failed" : "Completed",
      kind: "status" as const,
    },
    ...(outcome ? [outcome] : []),
  ];
}

function entryStatus(part: StepPart, messageStatus: MessageStatus): ActivityStatus {
  if (part.status === "running") return "running";
  if (part.status === "failed" || messageStatus === "failed") return "failed";
  return "completed";
}

function findLatestByTool(entries: PendingEntry[], toolName: string): PendingEntry | undefined {
  const normalized = normalizeToolName(toolName);
  return [...entries].reverse().find((entry) => normalizeToolName(entry.toolName) === normalized);
}

function attachResult(entry: PendingEntry, part: ToolResultPart, messageStatus: MessageStatus): void {
  entry.outputDetail = part.detail ?? part.summary;
  entry.rawOutput = part.rawText ?? part.detail ?? part.summary;
  const resultStatus: ActivityStatus = messageStatus === "failed"
    ? "failed"
    : part.status ?? "completed";
  entry.status = mergedStatus(entry.status, resultStatus);
}

function collectEntries(messages: ChatMessage[]): PendingEntry[] {
  const entries: PendingEntry[] = [];
  const byId = new Map<string, PendingEntry>();

  for (const message of messages) {
    message.parts.forEach((part, index) => {
      if (part.type === "step_ref") {
        const existingById = byId.get(part.stepId);
        if (existingById) {
          existingById.status = mergedStatus(existingById.status, entryStatus(part, message.status));
          if (looksLikeResponse(part.detail)) existingById.outputDetail = part.detail;
          else existingById.inputDetail = part.detail || existingById.inputDetail;
          existingById.messageId = message.id;
          return;
        }

        if (looksLikeResponse(part.detail)) {
          const matchingCall = findLatestByTool(entries, part.title);
          if (matchingCall) {
            matchingCall.outputDetail = part.detail;
            matchingCall.status = mergedStatus(matchingCall.status, entryStatus(part, message.status));
            matchingCall.messageId = message.id;
            byId.set(part.stepId, matchingCall);
            return;
          }
        }

        const entry: PendingEntry = {
          id: part.stepId,
          toolName: part.title,
          status: entryStatus(part, message.status),
          inputDetail: part.detail,
          outputDetail: "",
          rawOutput: "",
          messageId: message.id,
        };
        entries.push(entry);
        byId.set(part.stepId, entry);
        return;
      }

      if (part.type === "tool_result") {
        const existing = (part.toolCallId ? byId.get(part.toolCallId) : undefined)
          ?? findLatestByTool(entries, part.toolName);
        if (existing) {
          attachResult(existing, part, message.status);
          return;
        }
        const entry: PendingEntry = {
          id: part.toolCallId ?? `tool:${message.id}:${index}`,
          toolName: part.toolName,
          status: message.status === "failed" ? "failed" : part.status ?? "completed",
          inputDetail: "",
          outputDetail: part.detail ?? part.summary,
          rawOutput: part.rawText ?? part.detail ?? part.summary,
          messageId: message.id,
        };
        entries.push(entry);
        byId.set(entry.id, entry);
      }
    });
  }

  return entries;
}

function resolvedLabel(presentation: ToolPresentation, status: ActivityStatus): string {
  if (status === "running") return presentation.running;
  if (status === "failed") return presentation.failed;
  return presentation.completed;
}

function countLabel(presentation: ToolPresentation, count: number): string {
  return `${count} ${count === 1 ? presentation.singular : presentation.plural}`;
}

/** Project raw Tool lifecycle parts into grouped, user-facing activity. */
export function projectActivityGroups(messages: ChatMessage[]): ActivityGroup[] {
  const groups: ActivityGroup[] = [];

  for (const pending of collectEntries(messages)) {
    const presentation = toolPresentation(pending.toolName);
    const status = pending.status;
    const entry: ActivityEntry = {
      id: pending.id,
      toolName: pending.toolName,
      label: resolvedLabel(presentation, status),
      runningLabel: presentation.running,
      status,
      detail: activityDetail(pending, presentation),
      details: activityDetails(pending, presentation, status),
      rawDetail: redactActivityText(
        [pending.toolName, pending.inputDetail, pending.rawOutput || pending.outputDetail]
          .filter(Boolean)
          .join("\n→ "),
      ),
      messageId: pending.messageId,
    };

    const existing = groups.at(-1);
    if (existing?.key === presentation.key && existing.status === status) {
      existing.entries.push(entry);
      existing.count += 1;
      existing.status = mergedStatus(existing.status, status);
      existing.label = resolvedLabel(presentation, existing.status);
      existing.countLabel = countLabel(presentation, existing.count);
      existing.messageId = pending.messageId;
      continue;
    }

    const group: ActivityGroup = {
      key: presentation.key,
      label: resolvedLabel(presentation, status),
      runningLabel: presentation.running,
      status,
      count: 1,
      countLabel: countLabel(presentation, 1),
      messageId: pending.messageId,
      entries: [entry],
    };
    groups.push(group);
  }

  return groups;
}

/** Summarize a turn without exposing raw Tool identifiers. */
export function summarizeActivityGroups(groups: ActivityGroup[]): string {
  const totals = new Map<string, { toolName: string; count: number }>();
  for (const group of groups) {
    const current = totals.get(group.key);
    if (current) {
      current.count += group.count;
    } else {
      totals.set(group.key, {
        toolName: group.entries[0]?.toolName ?? group.key,
        count: group.count,
      });
    }
  }
  return [...totals.values()]
    .map(({ toolName, count }) => countLabel(toolPresentation(toolName), count))
    .join(" · ");
}
