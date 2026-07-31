import { useEffect, useMemo, useState } from "react";
import {
  projectActivityItems,
  projectArtifactItems,
  type ArtifactItem,
} from "../../lib/workspace-inspector";
import type { ChatMessage } from "../../types";

type InspectorTab = "activity" | "artifacts";

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

interface WorkspaceInspectorProps {
  sessionId: string;
  messages: ChatMessage[];
  running: boolean;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

/** Right-side inspection surface derived from the selected transcript. */
export function WorkspaceInspector({
  sessionId,
  messages,
  running,
  collapsed,
  onToggleCollapse,
}: WorkspaceInspectorProps) {
  const [tab, setTab] = useState<InspectorTab>("activity");
  const [selectedArtifact, setSelectedArtifact] = useState<ArtifactItem | null>(null);
  const activity = useMemo(() => projectActivityItems(messages), [messages]);
  const artifacts = useMemo(() => projectArtifactItems(messages), [messages]);

  useEffect(() => {
    setSelectedArtifact(null);
  }, [sessionId]);

  if (collapsed) {
    return (
      <aside className="workspace-inspector collapsed">
        <button onClick={onToggleCollapse} title="展开检查器 (⌘⇧B)">
          <span>INSPECT</span>
          <strong>‹</strong>
        </button>
      </aside>
    );
  }

  return (
    <aside className="workspace-inspector">
      <header className="inspector-header">
        <div className="inspector-tabs" role="tablist" aria-label="Session inspector">
          <button
            role="tab"
            aria-selected={tab === "activity"}
            className={tab === "activity" ? "active" : ""}
            onClick={() => {
              setTab("activity");
              setSelectedArtifact(null);
            }}
          >
            Activity <span>{activity.length}</span>
          </button>
          <button
            role="tab"
            aria-selected={tab === "artifacts"}
            className={tab === "artifacts" ? "active" : ""}
            onClick={() => setTab("artifacts")}
          >
            Artifacts <span>{artifacts.length}</span>
          </button>
        </div>
        <button
          className="inspector-collapse"
          onClick={onToggleCollapse}
          title="折叠检查器 (⌘⇧B)"
        >
          ›
        </button>
      </header>

      {tab === "activity" ? (
        <div className="inspector-body activity-panel">
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
            <div className="inspector-empty">
              <span>01</span>
              <strong>过程会出现在这里</strong>
              <p>当 Agent 规划、调用工具或遇到错误时，这里会形成可检查的时间线。</p>
            </div>
          )}
        </div>
      ) : (
        <div className="inspector-body artifact-panel">
          {selectedArtifact ? (
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
          ) : artifacts.length ? (
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
                  <b>↗</b>
                </button>
              ))}
            </div>
          ) : (
            <div className="inspector-empty">
              <span>02</span>
              <strong>交付物会集中在这里</strong>
              <p>消息中的文件和图片会自动汇总；当前 Session 还没有产物。</p>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
