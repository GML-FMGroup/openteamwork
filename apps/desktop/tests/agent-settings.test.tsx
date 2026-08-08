import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { AgentSettings } from "../app/src/components/settings/AgentSettings";
import type { AgentResourceSummary, ModelProfileSummary, PpxClientApi } from "../app/src/types";

const profile: ModelProfileSummary = {
  id: "primary",
  displayName: "Primary",
  provider: "openai_codex",
  model: "openai-codex/gpt-5",
  enabled: true,
  credentialState: "available",
  revision: "profile-revision",
};

function agent(name: string, revision: string): AgentResourceSummary {
  return {
    id: "main",
    name,
    description: "Primary agent",
    workspace: "/workspace/openppx",
    instruction: "",
    privilegeLevel: "medium",
    modelProfileId: "primary",
    enabled: true,
    status: "healthy",
    avatar: null,
    tags: [],
    revision,
    nodeRevision: "node-revision",
    effect: "none",
  };
}

describe("AgentSettings", () => {
  it("makes pending name changes and their save result explicit", async () => {
    let current = agent("Main", "agent-revision-1");
    const updateAgent = vi.fn(async (input) => {
      current = agent(input.displayName, "agent-revision-2");
      return current;
    });
    window.ppxClient = {
      listManagedAgents: vi.fn(async () => ({ agents: [current] })),
      updateAgent,
    } as unknown as PpxClientApi;
    const onWorkspaceChanged = vi.fn(async () => undefined);

    render(
      <AgentSettings
        selectedAgentId="main"
        modelProfiles={[profile]}
        onWorkspaceChanged={onWorkspaceChanged}
      />,
    );

    const nameInput = await screen.findByLabelText("Name");
    const saveButton = screen.getByRole("button", { name: "Save changes" });
    expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
    expect(saveButton).toBeDisabled();

    fireEvent.change(nameInput, { target: { value: "Medium" } });
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);

    await waitFor(() => expect(updateAgent).toHaveBeenCalledWith(expect.objectContaining({
      agentId: "main",
      displayName: "Medium",
      expectedRevision: "agent-revision-1",
    })));
    expect(await screen.findByText("Changes saved")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(onWorkspaceChanged).toHaveBeenCalledTimes(1);
  });
});
