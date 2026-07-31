import {
  CLIENT_API_PROTOCOL_VERSION,
  parseClientApiHandshake,
} from "../app/src/lib/client-api-contract";
import healthIncompatible from "../../../contracts/client-api/fixtures/health-incompatible.json";
import healthV1 from "../../../contracts/client-api/fixtures/health-v1.json";

describe("Client API protocol contract", () => {
  it("accepts the shared protocol v1 health fixture", () => {
    const handshake = parseClientApiHandshake(healthV1);

    expect(handshake).toMatchObject({
      protocolVersion: CLIENT_API_PROTOCOL_VERSION,
      productVersion: "0.4",
      ready: true,
      compatibility: "compatible",
    });
  });

  it("classifies a well-formed future protocol as incompatible", () => {
    const handshake = parseClientApiHandshake(healthIncompatible);

    expect(handshake.ready).toBe(true);
    expect(handshake.compatibility).toBe("incompatible");
  });

  it("rejects an unversioned successful response", () => {
    expect(() =>
      parseClientApiHandshake({
        ok: true,
        data: { service: "openppx-client-api", product_version: "0.4", ready: true, state: "healthy" },
      }),
    ).toThrow(/protocol_version/);
  });
});
