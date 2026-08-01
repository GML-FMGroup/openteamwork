import {
  canReuseStoredCredential,
  hydrateConnectionSettings,
  normalizeConnectionSettings,
  parseBoundConnectionCredential,
  parseStoredConnectionSettings,
  serializeBoundConnectionCredential,
  toStoredConnectionSettings,
} from "../app/src/lib/connection-profile";

describe("connection profile persistence", () => {
  it("migrates the legacy remote mode to lan", () => {
    expect(
      parseStoredConnectionSettings({
        targetType: "remote",
        targetId: "remote-studio",
        targetName: "Studio",
        clientApiBaseUrl: "http://192.168.1.10:8765",
      }),
    ).toMatchObject({ targetType: "lan", targetName: "Studio" });
  });

  it("never includes the access token in persisted JSON", () => {
    const stored = toStoredConnectionSettings(
      {
        targetType: "lan",
        targetId: "lan-studio",
        targetName: "Studio",
        clientApiBaseUrl: "http://192.168.1.10:8765",
        accessToken: "super-secret",
      },
      "client-api-token-v1",
    );

    expect(stored.secretRef).toBe("client-api-token-v1");
    expect(JSON.stringify(stored)).not.toContain("super-secret");
    expect("accessToken" in stored).toBe(false);
  });

  it("hydrates a decrypted token only for Main-side adapter settings", () => {
    const stored = parseStoredConnectionSettings({
      targetType: "lan",
      targetId: "lan-studio",
      targetName: "Studio",
      clientApiBaseUrl: "http://192.168.1.10:8765",
      secretRef: "client-api-token-v1",
    });

    expect(hydrateConnectionSettings(stored!, "secret").accessToken).toBe("secret");
  });

  it("normalizes and validates a LAN endpoint at the shared trust boundary", () => {
    expect(
      normalizeConnectionSettings({
        targetType: "lan",
        targetId: "lan-default",
        targetName: "Studio Node",
        clientApiBaseUrl: "http://studio.local:8765/",
        accessToken: " secret ",
      }),
    ).toEqual({
      targetType: "lan",
      targetId: "lan-studio-node",
      targetName: "Studio Node",
      clientApiBaseUrl: "http://studio.local:8765",
      accessToken: "secret",
    });

    expect(() =>
      normalizeConnectionSettings({
        targetType: "lan",
        targetId: "lan-studio",
        targetName: "Studio",
        clientApiBaseUrl: "http://user:password@studio.local:8765/path?token=secret",
      }),
    ).toThrow("cannot contain credentials");
    expect(() =>
      normalizeConnectionSettings({
        targetType: "lan",
        targetId: "lan-studio",
        targetName: "Studio",
        clientApiBaseUrl: "http://studio.local",
      }),
    ).toThrow("must include an explicit port");
    expect(() =>
      normalizeConnectionSettings({
        targetType: "local",
        targetId: "local-default",
        targetName: "This Mac",
        clientApiBaseUrl: "http://192.168.1.20:8765",
      }),
    ).toThrow("Local mode can only connect");
    expect(() =>
      normalizeConnectionSettings({
        targetType: "remote",
        targetId: "remote-studio",
        targetName: "Studio",
        clientApiBaseUrl: "http://192.168.1.20:8765",
      } as never),
    ).toThrow("Run location must be this computer or a LAN node");
  });

  it("reuses a stored credential only for the same LAN endpoint", () => {
    const stored = parseStoredConnectionSettings({
      targetType: "lan",
      targetId: "lan-studio",
      targetName: "Studio",
      clientApiBaseUrl: "http://studio.local:8765",
      secretRef: "client-api-token-v1",
    });
    const candidate = {
      targetType: "lan" as const,
      targetId: "lan-renamed",
      targetName: "Renamed",
      clientApiBaseUrl: "http://studio.local:8765/",
      accessToken: "",
    };

    expect(canReuseStoredCredential(stored, candidate)).toBe(true);
    expect(
      canReuseStoredCredential(stored, {
        ...candidate,
        clientApiBaseUrl: "http://other.local:8765",
      }),
    ).toBe(false);
    expect(canReuseStoredCredential(stored, { ...candidate, targetType: "local" })).toBe(false);
  });

  it("binds the encrypted credential payload to its LAN endpoint", () => {
    const payload = serializeBoundConnectionCredential("http://studio.local:8765/", "secret");

    expect(parseBoundConnectionCredential(payload, "http://studio.local:8765")).toBe("secret");
    expect(parseBoundConnectionCredential(payload, "http://other.local:8765")).toBe("");
    expect(parseBoundConnectionCredential("not-json", "http://studio.local:8765")).toBe("");
    expect(payload).toContain("http://studio.local:8765");
  });
});
