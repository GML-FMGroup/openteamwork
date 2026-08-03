"""ADK eval entrypoint for openppx.

The official ``adk eval`` CLI infers ``app_name`` from the agent directory
basename. Keep this directory named ``openppx`` so eval sessions use the same
application scope as production.
"""

from __future__ import annotations

from types import SimpleNamespace

from google.adk.agents import LlmAgent

from openppx.tooling.skills_adapter import list_skills

agent = SimpleNamespace(
    root_agent=LlmAgent(
        name="openppx",
        model="gemini-2.5-flash",
        instruction="Offline OpenPPX evaluation fixture.",
        tools=[list_skills],
    )
)

__all__ = ["agent"]
