"""Stable capabilities advertised by the in-process Control Plane kernel."""

CONTROL_PLANE_CAPABILITIES: tuple[str, ...] = (
    "actions.catalog",
    "actions.invoke",
    "config.agent.read",
    "config.agent.write",
    "config.node.read",
    "config.node.write",
    "model.profiles.read",
    "model.select",
    "system.status",
)
