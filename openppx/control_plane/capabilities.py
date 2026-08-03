"""Stable capabilities advertised by the in-process Control Plane kernel."""

CONTROL_PLANE_CAPABILITIES: tuple[str, ...] = (
    "actions.catalog",
    "actions.invoke",
    "config.agent.read",
    "config.agent.write",
    "config.node.read",
    "config.node.write",
    "extension.auth",
    "extension.read",
    "extension.write",
    "model.profiles.read",
    "model.select",
    "run.control",
    "session.read",
    "session.write",
    "task.read",
    "system.status",
)
