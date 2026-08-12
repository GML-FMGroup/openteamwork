import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OperationsSettings } from "../app/src/components/settings/OperationsSettings";
import type { OperationsDashboard, PpxClientApi } from "../app/src/types";

function dashboard(): OperationsDashboard {
  return {
    overview: {
      state: "healthy",
      components: [{ component: "runtime", state: "healthy", code: "ready", reason: "Runtime is ready.", remediation: null }],
      tasks: { total: 1, byStatus: { completed: 1 } },
      automation: { cronJobs: 0, heartbeatEnabled: true },
    },
    tasks: {
      ok: true,
      items: [{
        taskId: "task-1",
        kind: "manual",
        status: "completed",
        title: "Research report",
        progressSummary: "",
        terminalSummary: "Ready",
        lastError: "",
        checkpointRef: "",
        resumePolicy: "",
        updatedAtMs: 1_780_000_000_000,
        actions: [
          { action: "inspect_output", label: "View output", enabled: true, readOnly: true, reason: "" },
          { action: "send_input", label: "Send input", enabled: true, readOnly: false, reason: "" },
        ],
      }],
    },
    cron: { status: {}, items: [], history: [] },
    heartbeat: { running: true, enabled: true, intervalMs: 1_800_000, wakePending: false, lastRunAtMs: null, lastStatus: null, lastReason: null, lastDurationMs: null, configuration: { enabled: true, everySeconds: 1800, prompt: "Review tasks", activeHours: { start: null, end: null, timezone: "user" } } },
    compaction: { configuration: { enabled: true, thresholdPercent: 70 }, models: [{ agentId: "main", agentName: "Main", profileId: "primary", provider: "openai_codex", model: "openai-codex/gpt-current", strategy: "token_threshold", contextWindowTokens: 272000, contextWindowSource: "catalog", tokenThreshold: 190400, eventRetentionSize: 12, compactionInterval: null }] },
    usage: { requests: 2, requestTokens: 100, responseTokens: 50, totalTokens: 150, recent: [] },
    audit: [],
  };
}

describe("Operations settings", () => {
  beforeEach(() => {
    window.ppxClient = {
      getOperationsDashboard: vi.fn(async () => dashboard()),
      getOperationsTaskOutput: vi.fn(async () => ({ output: "Retained result" })),
      controlOperationsTask: vi.fn(async () => ({})),
      createOperationsCron: vi.fn(async () => ({})),
      updateOperationsCron: vi.fn(async () => ({})),
      setOperationsCronEnabled: vi.fn(async () => ({})),
      runOperationsCron: vi.fn(async () => ({})),
      removeOperationsCron: vi.fn(async () => ({})),
      runOperationsHeartbeat: vi.fn(async () => ({})),
      configureOperationsHeartbeat: vi.fn(async () => ({})),
      configureOperationsContextCompaction: vi.fn(async () => ({})),
    } as unknown as PpxClientApi;
  });

  it("configures context compaction as a model-window percentage", async () => {
    render(<OperationsSettings
      runtime={{ target: { id: "local", type: "local", name: "This Mac" }, state: "healthy", summary: "Ready" }}
      agents={[{ id: "main", name: "Main", description: "Primary", enabled: true, status: "healthy", tags: [] }]}
      selectedAgentId="main"
      userId="ppx-client-user"
      onRuntimeAction={vi.fn()}
      onStopRuntime={vi.fn()}
    />);

    expect(await screen.findByText("Automatically compact at 70% of the model context window")).toBeInTheDocument();
    expect(screen.getByText("190,400 of 272,000 tokens")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    fireEvent.change(screen.getByLabelText("Compact at (%)"), { target: { value: "75" } });
    fireEvent.click(screen.getByRole("button", { name: "Save policy" }));

    await waitFor(() => expect(window.ppxClient.configureOperationsContextCompaction).toHaveBeenCalledWith({ enabled: true, thresholdPercent: 75 }));
  });

  it("inspects retained Task output through the shared Operations action", async () => {
    render(<OperationsSettings
      runtime={{ target: { id: "local", type: "local", name: "This Mac" }, state: "healthy", summary: "Ready" }}
      agents={[{ id: "main", name: "Main", description: "Primary", enabled: true, status: "healthy", tags: [] }]}
      selectedAgentId="main"
      userId="ppx-client-user"
      onRuntimeAction={vi.fn()}
      onStopRuntime={vi.fn()}
    />);

    fireEvent.click(await screen.findByRole("button", { name: /Tasks/ }));
    fireEvent.click(await screen.findByText("Research report"));
    fireEvent.click(screen.getByRole("button", { name: "View output" }));

    expect(await screen.findByText("Retained result")).toBeInTheDocument();
    expect(window.ppxClient.getOperationsTaskOutput).toHaveBeenCalledWith("task-1");
  });

  it("creates an Agent-scoped interval schedule", async () => {
    render(<OperationsSettings
      runtime={{ target: { id: "local", type: "local", name: "This Mac" }, state: "healthy", summary: "Ready" }}
      agents={[{ id: "main", name: "Main", description: "Primary", enabled: true, status: "healthy", tags: [] }]}
      selectedAgentId="main"
      userId="ppx-client-user"
      onRuntimeAction={vi.fn()}
      onStopRuntime={vi.fn()}
    />);

    fireEvent.click(await screen.findByRole("button", { name: "Automations" }));
    fireEvent.click(screen.getByRole("button", { name: "New schedule" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Daily review" } });
    fireEvent.change(screen.getByLabelText("Every (minutes)"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("Agent instruction"), { target: { value: "Review the workspace" } });
    fireEvent.click(screen.getByRole("button", { name: "Create schedule" }));

    await waitFor(() => expect(window.ppxClient.createOperationsCron).toHaveBeenCalledWith(expect.objectContaining({
      name: "Daily review",
      agentId: "main",
      schedule: { kind: "every", everySeconds: 1800 },
    })));
  });

  it("provides required content when a durable Task is waiting for input", async () => {
    render(<OperationsSettings
      runtime={{ target: { id: "local", type: "local", name: "This Mac" }, state: "healthy", summary: "Ready" }}
      agents={[{ id: "main", name: "Main", description: "Primary", enabled: true, status: "healthy", tags: [] }]}
      selectedAgentId="main"
      userId="ppx-client-user"
      onRuntimeAction={vi.fn()}
      onStopRuntime={vi.fn()}
    />);

    fireEvent.click(await screen.findByRole("button", { name: /Tasks/ }));
    fireEvent.click(await screen.findByText("Research report"));
    const sendButton = screen.getByRole("button", { name: "Send input" });
    expect(sendButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Task input"), { target: { value: "Use the approved dataset" } });
    fireEvent.click(sendButton);

    await waitFor(() => expect(window.ppxClient.controlOperationsTask).toHaveBeenCalledWith({
      taskId: "task-1",
      action: "send_input",
      content: "Use the approved dataset",
    }));
  });
});
