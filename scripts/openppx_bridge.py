"""Minimal local bridge from ppx-client to openppx runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one local openppx session turn and stream NDJSON events.")
    parser.add_argument("--openppx-root", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--user-id", default="ppx-client-user")
    return parser.parse_args()


def _agent_config_path(agent_name: str) -> Path:
    return Path.home() / ".openpipixia" / agent_name / "config.json"


async def _run() -> int:
    args = _parse_args()
    openppx_root = Path(args.openppx_root).expanduser().resolve()
    if not openppx_root.exists():
      _emit({"type": "error", "message": f"openppx root not found: {openppx_root}"})
      return 1

    sys.path.insert(0, str(openppx_root))

    from openpipixia.core.config import bootstrap_env_from_config
    from openpipixia.runtime.adk_utils import extract_text, merge_text_stream
    from openpipixia.runtime.runner_factory import create_runner

    config_path = _agent_config_path(args.agent)
    if not config_path.exists():
        _emit({"type": "error", "message": f"Agent config not found: {config_path}"})
        return 1

    bootstrap_env_from_config(config_path)

    from google.genai import types
    from openpipixia.app.agent import root_agent
    from openpipixia.runtime.message_time import inject_request_time

    prompt = inject_request_time(args.message)
    request = types.UserContent(parts=[types.Part.from_text(text=prompt)])
    runner, _service = create_runner(agent=root_agent, app_name=root_agent.name)

    final_text = ""
    async for event in runner.run_async(user_id=args.user_id, session_id=args.session_id, new_message=request):
        text = extract_text(getattr(event, "content", None))
        merged = merge_text_stream(final_text, text)
        if merged and merged != final_text:
            final_text = merged
            _emit({"type": "delta", "text": final_text})

    _emit({"type": "final", "text": final_text})
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as exc:  # pragma: no cover - bridge safety path
        _emit({"type": "error", "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
