import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  projectActivityItems,
  mergeArtifactResources,
  type ArtifactItem,
} from "../../lib/workspace-inspector";
import type { ArtifactSummary, ChatMessage } from "../../types";
import { ShellIcon } from "./ContextSidebar";

function formatBytes(value: number | undefined): string {
  if (!value) {
    return "Unknown size";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${Math.max(1, Math.round(value / 1024))} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function locateMessage(messageId: string): void {
  document
    .getElementById(`message-${messageId}`)
    ?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function canPreviewAsText(mimeType: string | undefined): boolean {
  if (!mimeType) return false;
  return mimeType.startsWith("text/") || [
    "application/json",
    "application/javascript",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
  ].includes(mimeType);
}

function decodeArtifactText(dataUrl: string): string | null {
  const match = /^data:[^;,]+(?:;charset=[^;,]+)?;base64,(.*)$/s.exec(dataUrl);
  if (!match) return null;
  try {
    const decoded = atob(match[1]);
    const bytes = Uint8Array.from(decoded, (character) => character.charCodeAt(0));
    const text = new TextDecoder().decode(bytes);
    return text.length > 100_000 ? `${text.slice(0, 100_000)}\n\n[Preview truncated]` : text;
  } catch {
    return null;
  }
}

interface InspectorSectionProps {
  title: string;
  count?: number;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}

/** Collapsible section used by the session-scoped task panel. */
function InspectorSection({
  title,
  count,
  open,
  onToggle,
  children,
}: InspectorSectionProps) {
  return (
    <section className="inspector-section">
      <button
        className="inspector-section-toggle"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span className={open ? "inspector-section-chevron open" : "inspector-section-chevron"}>
          <ShellIcon name="expand" />
        </span>
        <span>{title}</span>
        {count ? <span className="inspector-section-count">({count})</span> : null}
      </button>
      {open ? <div className="inspector-section-body">{children}</div> : null}
    </section>
  );
}

interface WorkspaceInspectorProps {
  sessionId: string;
  messages: ChatMessage[];
  running: boolean;
  collapsed: boolean;
  artifacts?: ArtifactSummary[];
  onLoadArtifact?: (artifact: ArtifactSummary) => Promise<string>;
}

/** Right-side task panel derived from the currently selected session. */
export function WorkspaceInspector({
  sessionId,
  messages,
  running,
  collapsed,
  artifacts: artifactResources = [],
  onLoadArtifact,
}: WorkspaceInspectorProps) {
  const [open, setOpen] = useState({ progress: true, artifacts: true });
  const [selectedArtifact, setSelectedArtifact] = useState<ArtifactItem | null>(null);
  const activity = useMemo(() => projectActivityItems(messages), [messages]);
  const artifacts = useMemo(() => mergeArtifactResources(messages, artifactResources), [messages, artifactResources]);

  async function openArtifact(artifact: ArtifactItem): Promise<void> {
    if (!artifact.resource) {
      setSelectedArtifact(artifact);
      return;
    }
    try {
      const url = onLoadArtifact ? await onLoadArtifact(artifact.resource) : "";
      setSelectedArtifact({ ...artifact, url });
    } catch {
      setSelectedArtifact(artifact);
    }
  }

  useEffect(() => {
    setSelectedArtifact(null);
  }, [sessionId]);

  if (collapsed) {
    return null;
  }

  if (selectedArtifact) {
    const textPreview = canPreviewAsText(selectedArtifact.mimeType) && selectedArtifact.url
      ? decodeArtifactText(selectedArtifact.url)
      : null;
    return (
      <aside className="workspace-inspector" aria-label="Task panel">
        <div className="inspector-body artifact-panel">
          <div className="artifact-detail">
            <button className="artifact-back" onClick={() => setSelectedArtifact(null)}>
              ← Back to artifacts
            </button>
            {selectedArtifact.kind === "image" && selectedArtifact.url ? (
              <img src={selectedArtifact.url} alt={selectedArtifact.title} />
            ) : selectedArtifact.mimeType?.startsWith("audio/") && selectedArtifact.url ? (
              <audio controls src={selectedArtifact.url} aria-label={`Preview ${selectedArtifact.title}`} />
            ) : textPreview !== null ? (
              <pre className="artifact-text-preview">{textPreview}</pre>
            ) : (
              <div className="file-preview-glyph">
                {selectedArtifact.title.split(".").pop()?.toUpperCase() || "FILE"}
              </div>
            )}
            <div>
              <small>{selectedArtifact.mimeType || selectedArtifact.kind}</small>
              <h3>{selectedArtifact.title}</h3>
              <p>{selectedArtifact.description}</p>
            </div>
            {selectedArtifact.url ? (
              <a className="locate-source" href={selectedArtifact.url} download={selectedArtifact.title}>Download</a>
            ) : null}
            {selectedArtifact.messageId ? (
              <button className="locate-source" onClick={() => locateMessage(selectedArtifact.messageId)}>
                Locate in conversation
              </button>
            ) : null}
          </div>
        </div>
      </aside>
    );
  }

  return (
    <aside className="workspace-inspector" aria-label="Task panel">
      <div className="inspector-body rail-sections">
        <InspectorSection
          title="Progress"
          open={open.progress}
          onToggle={() => setOpen((current) => ({ ...current, progress: !current.progress }))}
        >
          <div className={`run-summary ${running ? "running" : "idle"}`}>
            <span className="run-orbit" />
            <div>
              <small>CURRENT RUN</small>
              <strong>
                {running ? "Agent is running" : activity.length ? "Latest run finished" : "Waiting for a task"}
              </strong>
            </div>
          </div>
          {activity.length ? (
            <ol className="activity-list">
              {activity.map((item, index) => (
                <li key={item.id} className={item.status}>
                  <button onClick={() => locateMessage(item.messageId)}>
                    <span className="activity-index">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <span className="activity-copy">
                      <strong>{item.title}</strong>
                      <small>{item.detail || item.kind}</small>
                    </span>
                    <span className={`activity-state ${item.status}`} />
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <p className="rail-section-empty">
              Plans and tool activity will appear here.
            </p>
          )}
        </InspectorSection>

        <InspectorSection
          title="Artifacts"
          count={artifacts.length}
          open={open.artifacts}
          onToggle={() => setOpen((current) => ({ ...current, artifacts: !current.artifacts }))}
        >
          {artifacts.length ? (
            <div className="artifact-list">
              {artifacts.map((artifact) => (
                <button
                  key={artifact.id}
                  className="artifact-row"
                  onClick={() => void openArtifact(artifact)}
                >
                  {artifact.kind === "image" && artifact.url ? (
                    <img src={artifact.url} alt="" />
                  ) : (
                    <span className="artifact-file-icon">
                      {artifact.title.split(".").pop()?.slice(0, 3).toUpperCase() || "DOC"}
                    </span>
                  )}
                  <span>
                    <strong>{artifact.title}</strong>
                    <small>
                      {artifact.kind === "file"
                        ? formatBytes(artifact.sizeBytes)
                        : artifact.mimeType || "image"}
                    </small>
                  </span>
                  <b>Open</b>
                </button>
              ))}
            </div>
          ) : (
            <p className="rail-section-empty">No artifacts yet.</p>
          )}
        </InspectorSection>
      </div>
    </aside>
  );
}
