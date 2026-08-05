import type { ArtifactSummary, ArtifactUploadInput } from "./models";

interface ArtifactTransport {
  request(pathname: string, init?: RequestInit): Promise<Response>;
  requestJson(pathname: string, init?: RequestInit): Promise<Record<string, unknown>>;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function projectArtifact(value: unknown): ArtifactSummary {
  const item = record(value);
  return {
    id: String(item.id ?? ""),
    key: String(item.key ?? ""),
    fileName: String(item.file_name ?? item.fileName ?? "Attachment"),
    mimeType: String(item.mime_type ?? item.mimeType ?? "application/octet-stream"),
    sizeBytes: Number(item.size_bytes ?? item.sizeBytes ?? 0),
    version: Number(item.version ?? 0),
    source: String(item.source ?? "agent_output"),
    createdAt: String(item.created_at ?? item.createdAt ?? ""),
  };
}

/** Typed Client API boundary for Session-scoped Artifact upload and download. */
export class ArtifactClient {
  public constructor(private readonly transport: ArtifactTransport) {}

  public async upload(input: ArtifactUploadInput): Promise<ArtifactSummary> {
    const payload = await this.transport.requestJson(
      `/api/v1/agents/${encodeURIComponent(input.agentId)}/sessions/${encodeURIComponent(input.sessionId)}/artifacts`,
      {
        method: "POST",
        body: JSON.stringify({
          file_name: input.fileName,
          mime_type: input.mimeType,
          data_base64: input.dataBase64,
        }),
      },
    );
    return projectArtifact(record(payload.data).artifact);
  }

  public async list(agentId: string, sessionId: string): Promise<ArtifactSummary[]> {
    const payload = await this.transport.requestJson(
      `/api/v1/agents/${encodeURIComponent(agentId)}/sessions/${encodeURIComponent(sessionId)}/artifacts`,
    );
    const items = record(payload.data).items;
    return Array.isArray(items) ? items.map(projectArtifact) : [];
  }

  public async download(
    agentId: string,
    sessionId: string,
    artifact: Pick<ArtifactSummary, "key" | "version">,
  ): Promise<{ bytes: ArrayBuffer; mimeType: string }> {
    const query = new URLSearchParams({ key: artifact.key, version: String(artifact.version) });
    const response = await this.transport.request(
      `/api/v1/agents/${encodeURIComponent(agentId)}/sessions/${encodeURIComponent(sessionId)}/artifact-content?${query}`,
    );
    if (!response.ok) {
      throw new Error(`Artifact download failed: ${response.status}`);
    }
    return {
      bytes: await response.arrayBuffer(),
      mimeType: response.headers.get("Content-Type") || "application/octet-stream",
    };
  }
}
