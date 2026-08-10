import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GoalStatusBar } from "../app/src/components/workspace/GoalStatusBar";
import type { GoalDetail } from "../app/src/types";

function goal(status: GoalDetail["status"] = "active"): GoalDetail {
  return {
    goalId: "goal-1",
    sessionId: "session-1",
    agentId: "agent-1",
    userId: "desktop-user",
    objective: "Research durable Goal controls",
    status,
    revision: 3,
    activeFlowId: "",
    completionCriteria: [],
    budgetState: {},
    createdAtMs: 1,
    updatedAtMs: 2,
    completedAtMs: null,
    cancelledAtMs: null,
    workspaceRef: "",
    constraints: [],
    budgetPolicy: {},
    permissionRevision: "",
    modelProfileRevision: "",
    extensionSnapshotDigest: "",
    completionEvidence: [],
    correlationId: "correlation-1",
    createdBy: "desktop-user",
    flow: null,
  };
}

describe("GoalStatusBar", () => {
  it("shows the active Goal and updates its objective inline", async () => {
    const onUpdate = vi.fn(async () => true);
    render(
      <GoalStatusBar
        goal={goal()}
        mutation={null}
        error={null}
        onUpdate={onUpdate}
        onTransition={vi.fn(async () => true)}
        onRetry={vi.fn(async () => true)}
      />,
    );

    expect(screen.getByText("Pursuing goal")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Goal objective" }), {
      target: { value: "Ship the Goal status rail" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith("Ship the Goal status rail"));
  });

  it("pauses active Goals and resumes paused Goals", () => {
    const onTransition = vi.fn(async () => true);
    const view = render(
      <GoalStatusBar goal={goal()} mutation={null} error={null} onUpdate={vi.fn(async () => true)} onTransition={onTransition} onRetry={vi.fn(async () => true)} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(onTransition).toHaveBeenCalledWith("pause");

    view.rerender(
      <GoalStatusBar goal={goal("paused")} mutation={null} error={null} onUpdate={vi.fn(async () => true)} onTransition={onTransition} onRetry={vi.fn(async () => true)} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(onTransition).toHaveBeenCalledWith("resume");
  });

  it("requires confirmation before cancelling a Goal", () => {
    const onTransition = vi.fn(async () => true);
    render(
      <GoalStatusBar goal={goal()} mutation={null} error={null} onUpdate={vi.fn(async () => true)} onTransition={onTransition} onRetry={vi.fn(async () => true)} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onTransition).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog", { name: "Cancel this Goal?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel Goal" }));
    expect(onTransition).toHaveBeenCalledWith("cancel");
  });

  it("keeps Goal controls read-only for an archived Session", () => {
    const onTransition = vi.fn(async () => true);
    render(
      <GoalStatusBar
        goal={goal()}
        mutation={null}
        error={null}
        disabled
        onUpdate={vi.fn(async () => true)}
        onTransition={onTransition}
        onRetry={vi.fn(async () => true)}
      />,
    );

    expect(screen.getByRole("region", { name: "Current Goal" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pause" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(onTransition).not.toHaveBeenCalled();
  });

  it("retries a blocked Goal instead of presenting a generic resume action", () => {
    const onRetry = vi.fn(async () => true);
    const blocked = goal("blocked");
    blocked.flow = {
      flowId: "flow-1",
      goalId: blocked.goalId,
      status: "blocked",
      revision: 4,
      steps: [],
      taskRunRefs: [],
      artifactRefs: [],
      waitReason: {
        kind: "repeated_action",
        message: "The same search repeated without progress.",
        stepId: "goal-execution",
        canRetry: true,
      },
      recoveryState: {},
      lastEvent: "goal.progress.blocked",
      createdAtMs: 1,
      updatedAtMs: 2,
    };

    render(
      <GoalStatusBar
        goal={blocked}
        mutation={null}
        error={null}
        onUpdate={vi.fn(async () => true)}
        onTransition={vi.fn(async () => true)}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByText("The same search repeated without progress.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
