import { describe, expect, it } from "vitest";
import { connectionFailureMessage } from "../app/src/lib/connection-feedback";
import type { ConnectionSettings } from "../app/src/types";

const localSettings: ConnectionSettings = {
  targetType: "local",
  targetId: "local-default",
  targetName: "This Mac",
  clientApiBaseUrl: "http://127.0.0.1:18764",
};

describe("connectionFailureMessage", () => {
  it("explains local reachability failures without Electron transport details", () => {
    const error = new Error(
      "Error invoking remote method 'ppx-client:test-connection-settings': Error: Testing the local connection requires the OpenPPX Client API. fetch failed",
    );

    expect(connectionFailureMessage(error, localSettings)).toBe(
      "Couldn’t reach an OpenPPX Node at http://127.0.0.1:18764. Check the URL and make sure the Node is running, then try again.",
    );
  });

  it("gives LAN, token, and protocol failures distinct recovery guidance", () => {
    const lanSettings = {
      ...localSettings,
      targetType: "lan" as const,
      clientApiBaseUrl: "http://192.168.1.20:18765",
    };

    expect(connectionFailureMessage(new Error("fetch failed"), lanSettings)).toContain(
      "Check the address and network connection",
    );
    expect(connectionFailureMessage(new Error("ClientApiRequestError: unauthorized access token"), lanSettings)).toBe(
      "The Node rejected the access token. Check the token and try again.",
    );
    expect(connectionFailureMessage(new Error("protocol version incompatible"), lanSettings)).toContain(
      "not compatible with this version of OpenPPX Desktop",
    );
  });

  it("keeps unknown internal failures out of the interface", () => {
    expect(connectionFailureMessage(new Error("sensitive internal adapter detail"), localSettings)).toBe(
      "Connection test failed. Check the Node URL and credentials, then try again.",
    );
  });
});
