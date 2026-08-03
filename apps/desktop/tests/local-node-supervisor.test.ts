import type { spawn } from "node:child_process";
import { vi } from "vitest";
import { LocalNodeSupervisor } from "../electron/main/local-node-supervisor";

function childProcessFixture() {
  const listeners = new Map<string, Array<(...args: unknown[]) => void>>();
  const child = {
    stdout: { on: vi.fn() },
    stderr: { on: vi.fn() },
    exitCode: null as number | null,
    kill: vi.fn(() => true),
    on: vi.fn((event: string, listener: (...args: unknown[]) => void) => {
      listeners.set(event, [...(listeners.get(event) ?? []), listener]);
      return child;
    }),
    once: vi.fn((event: string, listener: (...args: unknown[]) => void) => {
      listeners.set(event, [...(listeners.get(event) ?? []), listener]);
      return child;
    }),
    emit(event: string, ...args: unknown[]) {
      (listeners.get(event) ?? []).forEach((listener) => listener(...args));
    },
  };
  return child as unknown as ReturnType<typeof spawn> & { emit(event: string, ...args: unknown[]): void };
}

describe("LocalNodeSupervisor", () => {
  it("starts one managed Node and performs bounded readiness probes", async () => {
    const child = childProcessFixture();
    const spawnProcess = vi.fn(() => child) as unknown as typeof spawn;
    const wait = vi.fn(async () => undefined);
    const probe = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const supervisor = new LocalNodeSupervisor({
      openppxRoot: "/repo/openppx",
      nodeRoot: "/data/openppx",
      pythonBin: "/repo/openppx/.venv/bin/python",
      spawnProcess,
      wait,
    });

    await expect(
      supervisor.ensureReady({
        host: "127.0.0.1",
        port: 8765,
        accessToken: "secret",
        baseUrl: "http://127.0.0.1:8765",
        probe,
        attempts: 3,
        intervalMs: 10,
      }),
    ).resolves.toBe(true);

    expect(spawnProcess).toHaveBeenCalledTimes(1);
    expect(spawnProcess).toHaveBeenCalledWith(
      "/repo/openppx/.venv/bin/python",
      [
        "-m",
        "openppx.cli",
        "node",
        "run",
        "--node-root",
        "/data/openppx",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
      ],
      expect.objectContaining({
        cwd: "/repo/openppx",
        env: expect.objectContaining({ OPENPPX_CLIENT_API_TOKEN: "secret" }),
      }),
    );
    expect(wait).toHaveBeenCalledTimes(2);
    expect(probe).toHaveBeenCalledTimes(2);
    expect(supervisor.processRunning).toBe(true);
  });

  it("invalidates health on exit and stops the owned process", async () => {
    const child = childProcessFixture();
    const onExit = vi.fn();
    const supervisor = new LocalNodeSupervisor({
      openppxRoot: "/repo/openppx",
      nodeRoot: "/data/openppx",
      pythonBin: "python3",
      spawnProcess: vi.fn(() => child) as unknown as typeof spawn,
      wait: async () => undefined,
      onExit,
    });
    await supervisor.ensureReady({
      host: "127.0.0.1",
      port: 8765,
      accessToken: "secret",
      baseUrl: "http://127.0.0.1:8765",
      probe: async () => true,
      attempts: 1,
      intervalMs: 0,
    });

    const stopping = supervisor.stop();
    expect(child.kill).toHaveBeenCalledTimes(1);
    child.emit("close", 0);
    await stopping;

    expect(onExit).toHaveBeenCalledWith({ baseUrl: "http://127.0.0.1:8765", code: 0 });
    expect(supervisor.processRunning).toBe(false);
  });

  it("does not spawn when management is disabled", async () => {
    const spawnProcess = vi.fn();
    const supervisor = new LocalNodeSupervisor({
      openppxRoot: "/repo/openppx",
      nodeRoot: "/data/openppx",
      pythonBin: "python3",
      spawnProcess: spawnProcess as unknown as typeof spawn,
      enabled: false,
    });

    await expect(
      supervisor.ensureReady({
        host: "127.0.0.1",
        port: 8765,
        accessToken: "secret",
        baseUrl: "http://127.0.0.1:8765",
        probe: async () => true,
      }),
    ).resolves.toBe(false);
    expect(spawnProcess).not.toHaveBeenCalled();
  });

  it("handles spawn errors once and releases the failed child", async () => {
    const child = childProcessFixture();
    const onExit = vi.fn();
    const supervisor = new LocalNodeSupervisor({
      openppxRoot: "/repo/openppx",
      nodeRoot: "/data/openppx",
      pythonBin: "missing-python",
      spawnProcess: vi.fn(() => child) as unknown as typeof spawn,
      wait: async () => undefined,
      onExit,
    });
    const pending = supervisor.ensureReady({
      host: "127.0.0.1",
      port: 8765,
      accessToken: "secret",
      baseUrl: "http://127.0.0.1:8765",
      probe: async () => false,
      attempts: 1,
      intervalMs: 0,
    });

    child.emit("error", new Error("spawn ENOENT"));
    child.emit("close", -2);
    await expect(pending).resolves.toBe(false);

    expect(onExit).toHaveBeenCalledTimes(1);
    expect(onExit).toHaveBeenCalledWith({ baseUrl: "http://127.0.0.1:8765", code: null });
    expect(supervisor.processRunning).toBe(false);
  });
});
