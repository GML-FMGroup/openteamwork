import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { ModelProfileDialog } from "../app/src/components/models/ModelProfileDialog";
import type { ModelProfileCreateInput, ModelProfileResourceResult } from "../app/src/types";

function savedResult(): ModelProfileResourceResult {
  return {
    resourceId: "model-profiles/profile-1",
    revision: "sha256:saved",
    credentialState: "available",
    effect: "next_run",
    document: {
      apiVersion: "openppx.io/v1alpha1",
      kind: "ModelProfile",
      metadata: { name: "profile-1" },
      spec: {
        displayName: "Gemini Fast",
        provider: "google",
        model: "gemini-3-flash-preview",
        credential: { store: "system", name: "write-only-ref" },
        executionLocation: "remote",
        apiBase: null,
        capabilities: ["text", "tool_calling"],
        contextWindowTokens: null,
        inputCostPerMillionUsd: null,
        outputCostPerMillionUsd: null,
        fallbackProfiles: [],
        enabled: true,
      },
    },
  };
}

describe("ModelProfileDialog", () => {
  it("creates an API-key Profile by name without accepting a user-selected ID", async () => {
    const onCreate = vi.fn(async (_input: ModelProfileCreateInput) => savedResult());
    const onSaved = vi.fn();
    render(
      <ModelProfileDialog
        mode="new"
        profileId={null}
        profiles={[]}
        providers={[{
          id: "google",
          displayName: "Google Gemini",
          runtime: "google",
          credentialMode: "api_key",
          credentialRequired: true,
          defaultModel: "gemini-3-flash-preview",
        }]}
        onRead={vi.fn()}
        onGetModels={vi.fn(async () => ({
          providerId: "google",
          source: "provider_registry",
          authoritative: true,
          defaultModel: "gemini-3-flash-preview",
          items: [{
            id: "gemini-3-flash-preview",
            displayName: "Gemini 3 Flash",
            description: "Fast model",
            defaultReasoningEffort: null,
            reasoningEfforts: [],
            contextWindowTokens: 1048576,
          }],
        }))}
        onGetAuth={vi.fn()}
        onBeginAuth={vi.fn()}
        onRefreshAuth={vi.fn()}
        onOpenExternal={vi.fn()}
        onCreate={onCreate}
        onUpdate={vi.fn()}
        onCancel={vi.fn()}
        onSaved={onSaved}
      />,
    );

    await waitFor(() => expect(screen.getByRole("option", { name: "Gemini 3 Flash" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/Profile name/), { target: { value: "Gemini Fast" } });
    fireEvent.change(screen.getByLabelText(/API key/), { target: { value: "super-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Profile" }));

    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      displayName: "Gemini Fast",
      providerId: "google",
      model: "gemini-3-flash-preview",
      contextWindowTokens: 1048576,
      apiKey: "super-secret",
    }));
    expect(onCreate.mock.calls[0][0]).not.toHaveProperty("profileId");
    expect(onSaved).toHaveBeenCalledWith(savedResult());
  });

  it("edits the mutable name while keeping the generated ID read-only", async () => {
    const onUpdate = vi.fn(async () => savedResult());
    render(
      <ModelProfileDialog
        mode="edit"
        profileId="profile-1"
        profiles={[{
          id: "profile-1",
          displayName: "Gemini Fast",
          revision: "sha256:saved",
          provider: "google",
          model: "gemini-3-flash-preview",
          enabled: true,
          credentialState: "available",
        }]}
        providers={[{
          id: "google",
          displayName: "Google Gemini",
          runtime: "google",
          credentialMode: "api_key",
          credentialRequired: true,
          defaultModel: "gemini-3-flash-preview",
        }]}
        onRead={vi.fn(async () => savedResult())}
        onGetModels={vi.fn(async () => ({
          providerId: "google",
          source: "provider_registry",
          authoritative: true,
          defaultModel: "gemini-3-flash-preview",
          items: [{
            id: "gemini-3-flash-preview",
            displayName: "Gemini 3 Flash",
            description: "Fast model",
            defaultReasoningEffort: null,
            reasoningEfforts: [],
            contextWindowTokens: 1048576,
          }],
        }))}
        onGetAuth={vi.fn()}
        onBeginAuth={vi.fn()}
        onRefreshAuth={vi.fn()}
        onOpenExternal={vi.fn()}
        onCreate={vi.fn()}
        onUpdate={onUpdate}
        onCancel={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    const nameInput = await screen.findByLabelText(/Profile name/);
    expect(nameInput).toHaveValue("Gemini Fast");
    expect(screen.queryByLabelText(/Profile ID/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Advanced configuration"));
    expect(screen.getByText("profile-1")).toBeInTheDocument();
    fireEvent.change(nameInput, { target: { value: "Gemini Daily" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({
      profileId: "profile-1",
      displayName: "Gemini Daily",
      expectedRevision: "sha256:saved",
    })));
  });
});
