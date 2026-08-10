import type { spawn } from "node:child_process";
import { productProfile } from "../../product";

export interface LocalNodeSupervisorOptions {
  openppxRoot: string;
  nodeRoot: string;
  pythonBin: string;
  enabled?: boolean;
  spawnProcess: typeof spawn;
  wait?: (milliseconds: number) => Promise<void>;
  log?: (tag: string, payload: unknown) => void;
  onExit?: (event: { baseUrl: string; code: number | null }) => void;
}

export interface EnsureLocalNodeReadyOptions {
  host: string;
  port: number;
  accessToken: string;
  baseUrl: string;
  probe: () => Promise<boolean>;
  attempts?: number;
  intervalMs?: number;
}

/** Own the lifecycle of the Desktop-managed local product Node process. */
export class LocalNodeSupervisor {
  private readonly openppxRoot: string;

  private readonly nodeRoot: string;

  private readonly pythonBin: string;

  private readonly spawnProcess: typeof spawn;

  private readonly wait: (milliseconds: number) => Promise<void>;

  private readonly log: (tag: string, payload: unknown) => void;

  private readonly onExit: (event: { baseUrl: string; code: number | null }) => void;

  private enabled: boolean;

  private child: ReturnType<typeof spawn> | null = null;

  public constructor(options: LocalNodeSupervisorOptions) {
    this.openppxRoot = options.openppxRoot;
    this.nodeRoot = options.nodeRoot;
    this.pythonBin = options.pythonBin;
    this.enabled = options.enabled ?? true;
    this.spawnProcess = options.spawnProcess;
    this.wait = options.wait ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
    this.log = options.log ?? (() => undefined);
    this.onExit = options.onExit ?? (() => undefined);
  }

  public get processRunning(): boolean {
    return Boolean(this.child && this.child.exitCode === null);
  }

  public get managementEnabled(): boolean {
    return this.enabled;
  }

  public setEnabled(enabled: boolean): void {
    this.enabled = enabled;
  }

  public async ensureReady(options: EnsureLocalNodeReadyOptions): Promise<boolean> {
    if (!this.enabled) {
      return false;
    }
    this.startIfNeeded(options);
    const attempts = options.attempts ?? 12;
    const intervalMs = options.intervalMs ?? 250;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      await this.wait(intervalMs);
      if (await options.probe()) {
        this.log("client-api.health", {
          baseUrl: options.baseUrl,
          status: "healthy-after-spawn",
          attempt: attempt + 1,
        });
        return true;
      }
    }
    this.log("client-api.health", { baseUrl: options.baseUrl, status: "unreachable" });
    return false;
  }

  public stopImmediately(): void {
    const child = this.child;
    this.child = null;
    if (child && child.exitCode === null) {
      child.kill();
    }
  }

  public async stop(): Promise<void> {
    const child = this.child;
    this.child = null;
    if (!child || child.exitCode !== null) {
      return;
    }
    await new Promise<void>((resolve) => {
      let settled = false;
      const finish = (): void => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timeout);
        resolve();
      };
      const timeout = setTimeout(finish, 1_500);
      timeout.unref();
      child.once("close", finish);
      child.kill();
    });
  }

  private startIfNeeded(options: EnsureLocalNodeReadyOptions): void {
    if (this.child && this.child.exitCode === null) {
      return;
    }
    this.log("client-api.spawn", {
      baseUrl: options.baseUrl,
      pythonBin: this.pythonBin,
      openppxRoot: this.openppxRoot,
      nodeRoot: this.nodeRoot,
    });
    const child = this.spawnProcess(
      this.pythonBin,
      [
        "-m",
        "openppx.cli",
        "node",
        "run",
        "--node-root",
        this.nodeRoot,
        "--host",
        options.host,
        "--port",
        String(options.port),
      ],
      {
        cwd: this.openppxRoot,
        env: {
          ...process.env,
          [`${productProfile.environmentPrefix}_CLIENT_API_TOKEN`]: options.accessToken,
        },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    this.child = child;
    child.stdout?.on("data", (chunk: Buffer | string) => {
      this.log("client-api.stdout", chunk.toString().trim());
    });
    child.stderr?.on("data", (chunk: Buffer | string) => {
      this.log("client-api.stderr", chunk.toString().trim());
    });
    let finalized = false;
    const finalize = (code: number | null): void => {
      if (finalized) {
        return;
      }
      finalized = true;
      if (this.child === child) {
        this.child = null;
      }
      this.onExit({ baseUrl: options.baseUrl, code });
    };
    child.once("error", (error) => {
      this.log("client-api.error", {
        baseUrl: options.baseUrl,
        error: error instanceof Error ? error.message : String(error),
      });
      finalize(null);
    });
    child.on("close", (code) => {
      this.log("client-api.close", { baseUrl: options.baseUrl, code });
      finalize(code);
    });
  }
}
