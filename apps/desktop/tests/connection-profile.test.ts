import {
  canReuseStoredCredential,
  hydrateConnectionSettings,
  normalizeLoginConnectionSettings,
  normalizeConnectionSettings,
  parseBoundConnectionCredential,
  parseStoredConnectionSettings,
  parseStoredConnectionProfiles,
  serializeBoundConnectionCredential,
  toStoredConnectionSettings,
  upsertStoredConnectionProfile,
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
        userId: "user_jiang",
        userEmail: "jiang@example.com",
        userPrivilegeLevel: "high",
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

  it("infers the hidden login connection mode from the Node URL", () => {
    expect(
      normalizeLoginConnectionSettings({
        targetType: "local",
        targetId: "local-this-mac",
        targetName: "This Mac",
        clientApiBaseUrl: "https://team.example.com",
      }),
    ).toMatchObject({
      targetType: "lan",
      targetId: "lan-team-node",
      targetName: "Team Node",
      clientApiBaseUrl: "https://team.example.com",
      accessToken: "",
    });

    expect(
      normalizeLoginConnectionSettings({
        targetType: "lan",
        targetId: "lan-team-node",
        targetName: "Team Node",
        clientApiBaseUrl: "http://127.0.0.2:18765",
        accessToken: "stale-token",
      }),
    ).toMatchObject({
      targetType: "local",
      targetId: "local-this-mac",
      targetName: "This Mac",
      clientApiBaseUrl: "http://127.0.0.2:18765",
      accessToken: "",
    });
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
    const payload = serializeBoundConnectionCredential(
      "http://studio.local:8765/",
      "secret",
      "user_jiang",
    );

    expect(parseBoundConnectionCredential(payload, "http://studio.local:8765", "user_jiang")).toBe("secret");
    expect(parseBoundConnectionCredential(payload, "http://studio.local:8765", "user_other")).toBe("");
    expect(parseBoundConnectionCredential(payload, "http://other.local:8765", "user_jiang")).toBe("");
    expect(parseBoundConnectionCredential("not-json", "http://studio.local:8765", "user_jiang")).toBe("");
    expect(payload).toContain("http://studio.local:8765");
  });

  it("migrates a single saved target and upserts multiple target profiles", () => {
    const migrated = parseStoredConnectionProfiles({
      targetType: "remote",
      targetId: "remote-studio",
      targetName: "Studio",
      clientApiBaseUrl: "http://studio.local:8765",
      secretRef: "client-api-token-v1",
    });
    expect(migrated).toMatchObject({ schemaVersion: 2, activeTargetId: "remote-studio" });

    const local = toStoredConnectionSettings({
      targetType: "local",
      targetId: "local-this-mac",
      targetName: "This Mac",
      clientApiBaseUrl: "http://127.0.0.1:18765",
    });
    const updated = upsertStoredConnectionProfile(migrated, local);

    expect(updated.activeTargetId).toBe("local-this-mac");
    expect(updated.items.map((item) => item.targetName)).toEqual(["This Mac", "Studio"]);
    expect(JSON.stringify(updated)).not.toContain("accessToken");
  });

  it("deduplicates corrupt profile collections and preserves the requested active target", () => {
    const parsed = parseStoredConnectionProfiles({
      schemaVersion: 2,
      activeTargetId: "lan-studio",
      items: [
        {
          targetType: "lan",
          targetId: "lan-studio",
          targetName: "Studio",
          clientApiBaseUrl: "http://studio.local:8765",
        },
        {
          targetType: "lan",
          targetId: "lan-studio",
          targetName: "Stale duplicate",
          clientApiBaseUrl: "http://stale.local:8765",
        },
      ],
    });

    expect(parsed?.activeTargetId).toBe("lan-studio");
    expect(parsed?.items).toHaveLength(1);
    expect(parsed?.items[0].targetName).toBe("Studio");
  });
});
