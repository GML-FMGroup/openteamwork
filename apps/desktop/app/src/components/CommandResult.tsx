import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { CommandResultPresentation } from "../types";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function itemList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function sourceLabel(value: unknown): string {
  const projected = record(value);
  const source = String(projected.type ?? value ?? "").trim();
  if (!source) return "Skill";
  if (source === "builtin") return "Built-in";
  if (source.startsWith("plugin:")) {
    return `${source.slice("plugin:".length).trim() || "Plugin"} plugin`;
  }
  return source.replaceAll("_", " ");
}

function historyRoleLabel(value: unknown, agentName?: string): string {
  const role = String(value ?? "").trim().toLowerCase();
  if (role === "user") return "You";
  if (role === "assistant") return agentName?.trim() || "Agent";
  return role ? `${role[0]?.toUpperCase()}${role.slice(1)}` : "Message";
}

function validTimestamp(value: unknown): { dateTime: string; label: string } | null {
  const dateTime = String(value ?? "").trim();
  if (!dateTime) return null;
  const parsed = new Date(dateTime);
  if (Number.isNaN(parsed.getTime())) return null;
  return {
    dateTime,
    label: parsed.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }),
  };
}

function HistoryResult(props: { items: Record<string, unknown>[]; agentName?: string }) {
  const { items, agentName } = props;
  return (
    <>
      <header className="command-result-header">
        <strong>Recent history</strong>
        <span>{items.length} {items.length === 1 ? "message" : "messages"}</span>
      </header>
      {items.length ? (
        <div className="command-result-list history-result-list" role="list">
          {items.map((item, index) => {
            const timestamp = validTimestamp(item.timestamp);
            return (
              <article
                className="command-result-row history-result-row"
                key={String(item.invocationId ?? `history-${index}`)}
                role="listitem"
              >
                <div className="command-result-row-meta">
                  <strong>{historyRoleLabel(item.role, agentName)}</strong>
                  {timestamp ? <time dateTime={timestamp.dateTime}>{timestamp.label}</time> : null}
                </div>
                <p>{String(item.text ?? "")}</p>
              </article>
            );
          })}
        </div>
      ) : <p className="command-result-empty">This Session has no visible history yet.</p>}
    </>
  );
}

function SkillResult(props: { items: Record<string, unknown>[] }) {
  const { items } = props;
  return (
    <>
      <header className="command-result-header">
        <strong>Available Skills</strong>
        <span>{items.length} available</span>
      </header>
      {items.length ? (
        <div className="command-result-list skill-result-list" role="list">
          {items.map((item, index) => {
            const name = String(item.name ?? item.displayName ?? item.id ?? "Skill");
            return (
              <article
                className="command-result-row skill-result-row"
                key={`${name}-${index}`}
                role="listitem"
              >
                <div className="command-result-row-meta">
                  <strong>{name}</strong>
                  <span>{sourceLabel(item.source)}</span>
                </div>
                <p>{String(item.description ?? "No description provided.")}</p>
              </article>
            );
          })}
        </div>
      ) : <p className="command-result-empty">No Skills are available to this Agent.</p>}
    </>
  );
}

/** Render authorized slash-command data as native transcript content. */
export function CommandResult(props: {
  presentation: CommandResultPresentation;
  fallbackText: string;
  agentName?: string;
}) {
  const { presentation, fallbackText, agentName } = props;
  const items = itemList(record(presentation.result).items);
  return (
    <section aria-label={`${presentation.command} result`} className="command-result">
      {presentation.targetActionId === "session.history" ? (
        <HistoryResult agentName={agentName} items={items} />
      ) : presentation.targetActionId === "extension.list" && presentation.command === "/skills" ? (
        <SkillResult items={items} />
      ) : (
        <div className="command-result-fallback rich-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{fallbackText}</ReactMarkdown>
        </div>
      )}
    </section>
  );
}
