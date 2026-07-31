import { buildClientApiAuthorizationHeaders } from "../app/src/lib/client-api-auth";

describe("Client API authorization headers", () => {
  it("omits Authorization when no credential is configured", () => {
    expect(buildClientApiAuthorizationHeaders("")).toEqual({});
  });

  it("uses the bearer scheme without query-string encoding", () => {
    const headers = buildClientApiAuthorizationHeaders("  secret-token  ");

    expect(headers).toEqual({ Authorization: "Bearer secret-token" });
    expect(JSON.stringify(headers)).not.toContain("access_token=");
  });
});
