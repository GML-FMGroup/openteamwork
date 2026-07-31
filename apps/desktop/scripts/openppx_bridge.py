"""Minimal local bridge from ppx-client to openppx runner/session service."""

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
    parser = argparse.ArgumentParser(description="Local bridge between ppx-client and openppx.")
    parser.add_argument(
        "action",
        choices=["run", "list_sessions", "get_session", "create_session"],
    )
    parser.add_argument("--openppx-root", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--user-id", default="ppx-client-user")
    return parser.parse_args()


def _agent_config_path(agent_name: str) -> Path:
    return Path.home() / ".openpipixia" / agent_name / "config.json"


def _event_preview_text(event: object) -> str:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) or []
    texts: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return " ".join(texts).strip()


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
    from openpipixia.runtime.session_service import create_session_service

    config_path = _agent_config_path(args.agent)
    if not config_path.exists():
        _emit({"type": "error", "message": f"Agent config not found: {config_path}"})
        return 1

    bootstrap_env_from_config(config_path)

    from google.genai import types
    from openpipixia.app.agent import root_agent
    from openpipixia.runtime.message_time import inject_request_time

    session_service = create_session_service()
    app_name = root_agent.name

    if args.action == "create_session":
        session = await session_service.create_session(
            app_name=app_name,
            user_id=args.user_id,
            session_id=args.session_id or None,
        )
        _emit(
            {
                "type": "session_created",
                "session": {
                    "id": session.id,
                    "app_name": session.app_name,
                    "user_id": session.user_id,
                    "last_update_time": session.last_update_time,
                },
            }
        )
        return 0

    if args.action == "list_sessions":
        response = await session_service.list_sessions(app_name=app_name, user_id=args.user_id)
        _emit(
            {
                "type": "session_list",
                "sessions": [
                    {
                        "id": session.id,
                        "app_name": session.app_name,
                        "user_id": session.user_id,
                        "last_update_time": session.last_update_time,
                        "event_count": len(session.events),
                        "last_preview": _event_preview_text(session.events[-1]) if session.events else "",
                    }
                    for session in response.sessions
                ],
            }
        )
        return 0

    if args.action == "get_session":
        if not args.session_id:
            _emit({"type": "error", "message": "--session-id is required for get_session"})
            return 1
        session = await session_service.get_session(
            app_name=app_name,
            user_id=args.user_id,
            session_id=args.session_id,
        )
        if session is None:
            _emit({"type": "session_detail", "session": None})
            return 0
        _emit(
            {
                "type": "session_detail",
                "session": {
                    "id": session.id,
                    "app_name": session.app_name,
                    "user_id": session.user_id,
                    "last_update_time": session.last_update_time,
                    "events": [event.model_dump(mode="json") for event in session.events],
                },
            }
        )
        return 0

    if not args.session_id:
        _emit({"type": "error", "message": "--session-id is required for run"})
        return 1
    if not args.message:
        _emit({"type": "error", "message": "--message is required for run"})
        return 1

    prompt = inject_request_time(args.message)
    request = types.UserContent(parts=[types.Part.from_text(text=prompt)])
    runner, _service = create_runner(agent=root_agent, app_name=app_name, session_service=session_service)

    final_text = ""
    async for event in runner.run_async(user_id=args.user_id, session_id=args.session_id, new_message=request):
        payload = event.model_dump(mode="json")
        _emit({"type": "event", "event": payload})
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
