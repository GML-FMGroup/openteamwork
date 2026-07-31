import { bootstrap, createSession, loadSession } from "../app/src/lib/mock-client";

describe("mock client adapter", () => {
  it("returns initial local bootstrap payload", async () => {
    const payload = await bootstrap();
    expect(payload.runtime.target.type).toBe("local");
    expect(payload.agents.length).toBeGreaterThan(0);
    expect(payload.messages.length).toBeGreaterThan(0);
  });

  it("creates an empty session for the selected agent", async () => {
    const created = await createSession("builder");
    expect(created.session.agentId).toBe("builder");

    const loaded = await loadSession(created.session.id);
    expect(loaded.messages).toHaveLength(0);
  });
});
