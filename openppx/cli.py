"""Stable thin CLI entry point for OpenPPX Node Actions."""

from __future__ import annotations

from openppx.command.dispatch import dispatch
from openppx.command.parser import build_parser
from openppx.runtime.adk_version import assert_supported_adk_major


def main(argv: list[str] | None = None) -> None:
    """Parse one command, dispatch it, and terminate with its exit code."""
    assert_supported_adk_major()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code = dispatch(args)
    except ValueError as exc:
        parser.error(str(exc))
        return
    raise SystemExit(code)


__all__ = ["main"]


if __name__ == "__main__":
    main()
