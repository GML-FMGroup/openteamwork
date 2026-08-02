import type { spawn } from "node:child_process";

export interface LegacyBridgeStreamEvent extends Record<string, unknown> {
  type: string;
  text?: unknown;
  message?: unknown;
  event?: unknown;
}

export interface LegacyBridgeClientOptions {
  openppxRoot: string;
  pythonBin: string;
  scriptPath: string;
  spawnProcess: typeof spawn;
  log?: (tag: string, payload: unknown) => void;
}

export interface LegacyBridgeRunInput {
  agentId: string;
  sessionId: string;
  text: string;
}

function parseStreamLine(line: string): LegacyBridgeStreamEvent | null {
  const text = line.trim();
  if (!text) {
    return null;
  }
  try {
    const payload = JSON.parse(text) as unknown;
    if (payload && typeof payload === "object" && !Array.isArray(payload) && "type" in payload) {
      return payload as LegacyBridgeStreamEvent;
    }
  } catch {
    // Development bridges can emit plain text from older versions.
  }
  return { type: "raw", text };
}

/** Isolate the explicitly enabled development bridge subprocess protocol. */
export class LegacyBridgeClient {
  private readonly openppxRoot: string;

  private readonly pythonBin: string;

  private readonly scriptPath: string;

  private readonly spawnProcess: typeof spawn;

  private readonly log: (tag: string, payload: unknown) => void;

  public constructor(options: LegacyBridgeClientOptions) {
    this.openppxRoot = options.openppxRoot;
    this.pythonBin = options.pythonBin;
    this.scriptPath = options.scriptPath;
    this.spawnProcess = options.spawnProcess;
    this.log = options.log ?? (() => undefined);
  }

  public request<T>(action: string, agentId: string, extra: string[] = []): Promise<T | null> {
    return new Promise((resolve, reject) => {
      const child = this.spawnProcess(this.pythonBin, this.arguments(action, agentId, extra), {
        cwd: this.openppxRoot,
        stdio: ["ignore", "pipe", "pipe"],
      });
      let stdout = "";
      let stderr = "";
      child.stdout?.on("data", (chunk: Buffer | string) => {
        stdout += chunk.toString();
      });
      child.stderr?.on("data", (chunk: Buffer | string) => {
        stderr += chunk.toString();
      });
      child.once("error", reject);
      child.once("close", (code) => {
        if (code !== 0) {
          reject(new Error(stderr.trim() || stdout.trim() || `Bridge exited with code ${code}`));
          return;
        }
        const lines = stdout
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);
        if (!lines.length) {
          resolve(null);
          return;
        }
        try {
          resolve(JSON.parse(lines.at(-1)!) as T);
        } catch (error) {
          reject(error);
        }
      });
    });
  }

  public run(
    input: LegacyBridgeRunInput,
    onEvent: (event: LegacyBridgeStreamEvent) => void,
  ): Promise<{ code: number | null; stderr: string }> {
    return new Promise((resolve, reject) => {
      const child = this.spawnProcess(
        this.pythonBin,
        this.arguments("run", input.agentId, ["--session-id", input.sessionId, "--message", input.text]),
        {
          cwd: this.openppxRoot,
          stdio: ["ignore", "pipe", "pipe"],
        },
      );
      let stdoutBuffer = "";
      let stderr = "";
      const deliver = (line: string): void => {
        const event = parseStreamLine(line);
        if (event) {
          onEvent(event);
        }
      };
      child.stdout?.on("data", (chunk: Buffer | string) => {
        stdoutBuffer += chunk.toString();
        const lines = stdoutBuffer.split("\n");
        stdoutBuffer = lines.pop() ?? "";
        lines.forEach(deliver);
      });
      child.stderr?.on("data", (chunk: Buffer | string) => {
        stderr += chunk.toString();
        this.log("send.bridge.stderr", { text: chunk.toString().trim() });
      });
      child.once("error", reject);
      child.once("close", (code) => {
        if (stdoutBuffer.trim()) {
          deliver(stdoutBuffer);
        }
        resolve({ code, stderr: stderr.trim() });
      });
    });
  }

  private arguments(action: string, agentId: string, extra: string[]): string[] {
    return [this.scriptPath, "--openppx-root", this.openppxRoot, action, "--agent", agentId, ...extra];
  }
}
