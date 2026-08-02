import type { spawn } from "node:child_process";
import { vi } from "vitest";
import { LegacyBridgeClient } from "../electron/main/legacy-bridge-client";

function childProcessFixture() {
  const childListeners = new Map<string, Array<(...args: unknown[]) => void>>();
  const stdoutListeners: Array<(chunk: string) => void> = [];
  const stderrListeners: Array<(chunk: string) => void> = [];
  const child = {
    stdout: {
      on: vi.fn((_event: string, listener: (chunk: string) => void) => {
        stdoutListeners.push(listener);
      }),
    },
    stderr: {
      on: vi.fn((_event: string, listener: (chunk: string) => void) => {
        stderrListeners.push(listener);
      }),
    },
    exitCode: null as number | null,
    kill: vi.fn(() => true),
    once: vi.fn((event: string, listener: (...args: unknown[]) => void) => {
      childListeners.set(event, [...(childListeners.get(event) ?? []), listener]);
      return child;
    }),
    emit(event: string, ...args: unknown[]) {
      (childListeners.get(event) ?? []).forEach((listener) => listener(...args));
    },
  };
  return {
    child: child as unknown as ReturnType<typeof spawn>,
    stdout: (chunk: string) => stdoutListeners.forEach((listener) => listener(chunk)),
    stderr: (chunk: string) => stderrListeners.forEach((listener) => listener(chunk)),
    close: (code: number | null) => child.emit("close", code),
  };
}

describe("LegacyBridgeClient", () => {
  it("reads the last JSON response from a bridge command", async () => {
    const fixture = childProcessFixture();
    const spawnProcess = vi.fn(() => fixture.child) as unknown as typeof spawn;
    const bridge = new LegacyBridgeClient({
      openppxRoot: "/repo/openppx",
      pythonBin: "python3",
      scriptPath: "/desktop/scripts/openppx_bridge.py",
      spawnProcess,
    });

    const pending = bridge.request<{ sessions: unknown[] }>("list_sessions", "writer");
    fixture.stdout("diagnostic line\n");
    fixture.stdout('{"sessions":[{"id":"session-1"}]}\n');
    fixture.close(0);

    await expect(pending).resolves.toEqual({ sessions: [{ id: "session-1" }] });
    expect(spawnProcess).toHaveBeenCalledWith(
      "python3",
      ["/desktop/scripts/openppx_bridge.py", "--openppx-root", "/repo/openppx", "list_sessions", "--agent", "writer"],
      expect.objectContaining({ cwd: "/repo/openppx" }),
    );
  });

  it("parses structured and raw streaming lines and returns stderr", async () => {
    const fixture = childProcessFixture();
    const bridge = new LegacyBridgeClient({
      openppxRoot: "/repo/openppx",
      pythonBin: "python3",
      scriptPath: "/desktop/scripts/openppx_bridge.py",
      spawnProcess: vi.fn(() => fixture.child) as unknown as typeof spawn,
    });
    const events: unknown[] = [];

    const pending = bridge.run(
      { agentId: "writer", sessionId: "session-1", text: "hello" },
      (event) => events.push(event),
    );
    fixture.stdout('{"type":"delta","text":"hel');
    fixture.stdout('lo"}\nplain text\n');
    fixture.stderr("warning");
    fixture.close(0);

    await expect(pending).resolves.toEqual({ code: 0, stderr: "warning" });
    expect(events).toEqual([
      { type: "delta", text: "hello" },
      { type: "raw", text: "plain text" },
    ]);
  });

  it("rejects failed request commands with bridge output", async () => {
    const fixture = childProcessFixture();
    const bridge = new LegacyBridgeClient({
      openppxRoot: "/repo/openppx",
      pythonBin: "python3",
      scriptPath: "/desktop/scripts/openppx_bridge.py",
      spawnProcess: vi.fn(() => fixture.child) as unknown as typeof spawn,
    });

    const pending = bridge.request("get_session", "writer", ["--session-id", "missing"]);
    fixture.stderr("session not found");
    fixture.close(2);

    await expect(pending).rejects.toThrow("session not found");
  });
});
