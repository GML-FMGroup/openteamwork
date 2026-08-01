import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  projectActivityItems,
  projectArtifactItems,
  type ArtifactItem,
} from "../../lib/workspace-inspector";
import type { ChatMessage } from "../../types";
import { ShellIcon } from "./ContextSidebar";

function formatBytes(value: number | undefined): string {
  if (!value) {
    return "大小未知";
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
}

/** Right-side task panel derived from the currently selected session. */
export function WorkspaceInspector({
  sessionId,
  messages,
  running,
  collapsed,
}: WorkspaceInspectorProps) {
  const [open, setOpen] = useState({ progress: true, artifacts: true });
  const [selectedArtifact, setSelectedArtifact] = useState<ArtifactItem | null>(null);
  const activity = useMemo(() => projectActivityItems(messages), [messages]);
  const artifacts = useMemo(() => projectArtifactItems(messages), [messages]);

  useEffect(() => {
    setSelectedArtifact(null);
  }, [sessionId]);

  if (collapsed) {
    return null;
  }

  if (selectedArtifact) {
    return (
      <aside className="workspace-inspector" aria-label="任务面板">
        <div className="inspector-body artifact-panel">
          <div className="artifact-detail">
            <button className="artifact-back" onClick={() => setSelectedArtifact(null)}>
              ← 返回产物
            </button>
            {selectedArtifact.kind === "image" && selectedArtifact.url ? (
              <img src={selectedArtifact.url} alt={selectedArtifact.title} />
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
            <button
              className="locate-source"
              onClick={() => locateMessage(selectedArtifact.messageId)}
            >
              在对话中定位
            </button>
          </div>
        </div>
      </aside>
    );
  }

  return (
    <aside className="workspace-inspector" aria-label="任务面板">
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
                {running ? "Agent 正在执行" : activity.length ? "最近运行已结束" : "等待任务"}
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
              任务执行过程会在这里显示，包括规划、工具调用和错误。
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
                  onClick={() => setSelectedArtifact(artifact)}
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
            <p className="rail-section-empty">当前对话还没有可预览的文件或图片。</p>
          )}
        </InspectorSection>
      </div>
    </aside>
  );
}
