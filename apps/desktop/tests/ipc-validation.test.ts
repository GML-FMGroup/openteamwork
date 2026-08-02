import { describe, expect, it } from "vitest";
import {
  validateConnectionSettings,
  validateIdentifier,
  validateRuntimeCommand,
  validateSendMessageInput,
} from "../electron/main/ipc-validation";

describe("Electron IPC validation", () => {
  it("accepts well-formed renderer requests", () => {
    expect(validateRuntimeCommand("restart")).toBe("restart");
    expect(validateIdentifier("run-1", "Run id")).toBe("run-1");
    expect(validateSendMessageInput({ agentId: "writer", sessionId: "session-1", text: "hello" })).toEqual({
      agentId: "writer",
      sessionId: "session-1",
      text: "hello",
    });
    expect(
      validateConnectionSettings({
        targetType: "lan",
        targetId: "studio-node",
        targetName: "Studio Node",
        clientApiBaseUrl: "http://192.168.1.8:8765",
        accessToken: "secret",
      }),
    ).toEqual({
      targetType: "lan",
      targetId: "studio-node",
      targetName: "Studio Node",
      clientApiBaseUrl: "http://192.168.1.8:8765",
      accessToken: "secret",
    });
  });

  it("rejects malformed renderer requests before they reach services", () => {
    expect(() => validateRuntimeCommand("delete")).toThrow("Runtime command");
    expect(() => validateIdentifier("", "Run id")).toThrow("Run id is required");
    expect(() => validateSendMessageInput({ agentId: "writer", sessionId: [], text: "hello" })).toThrow(
      "Session id must be a string",
    );
    expect(() => validateConnectionSettings({ targetType: "internet" })).toThrow("targetType");
  });
});
