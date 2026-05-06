"""Entry point for the `lillycoder` command."""
from __future__ import annotations

import argparse
import sys


DISCLAIMER = (
    "lillycoder is provided AS IS, WITHOUT WARRANTY OF ANY KIND. "
    "It can read, write, and delete files in the current folder, run shell "
    "commands, and install packages. You alone are responsible for any "
    "damage to your data, hardware, or system. By running this you accept "
    "all risk. See the README for the full disclaimer."
)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="lillycoder",
        description="local coder-assistant REPL with file + shell tools.",
        epilog=DISCLAIMER,
    )
    parser.add_argument(
        "--api",
        help="OpenAI-compatible /v1 base URL "
             "(e.g. http://localhost:11434/v1). "
             "Skip to auto-discover localhost.",
    )
    parser.add_argument(
        "--model", "-m",
        help="model id to request from the endpoint (must be in its catalog)",
    )
    parser.add_argument(
        "--persona", "-p", default="default",
        help="persona name (file under ~/.config/lillycoder/personas/), "
             "or 'default' for the bundled lilly-coder persona",
    )
    parser.add_argument(
        "--bypass-permissions",
        action="store_true",
        help="skip per-tool permission prompts (still respects safety "
             "deny-list: sudo, rm -rf /, etc are always blocked)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="silence the warning when the chosen model is not on the "
             "tool-capable allowlist",
    )
    parser.add_argument(
        "--list-personas",
        action="store_true",
        help="list available personas and exit",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="probe localhost for OpenAI-compatible endpoints and exit",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print version and exit",
    )
    args = parser.parse_args()

    if args.version:
        from . import __version__
        print(f"lillycoder {__version__}")
        return 0

    if args.list_personas:
        from .config import list_personas
        for name in list_personas():
            print(name)
        return 0

    if args.scan:
        from .discovery import discover
        eps = discover()
        if not eps:
            print("no OpenAI-compatible endpoints found on localhost.")
            return 1
        for ep in eps:
            print(f"{ep.base_url}\t{ep.label}\t{len(ep.models)} models")
        return 0

    from .repl import run_repl
    return run_repl(
        api_url=args.api,
        model=args.model,
        persona=args.persona,
        force=args.force,
        bypass_perms=args.bypass_permissions,
    )


if __name__ == "__main__":
    sys.exit(main())
