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
        "--persona", "-p", default=None,
        help="persona name (file under ~/.config/lillycoder/personas/), "
             "or 'default' for the bundled lilly-coder persona. if "
             "omitted, lillycoder remembers the last active persona "
             "across runs.",
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
    parser.add_argument(
        "--no-autocompact",
        action="store_true",
        help="disable automatic context compaction at 90 percent fill "
             "(overrides config; toggle live with /autocompact)",
    )
    parser.add_argument(
        "--persona-evolve",
        action="store_true",
        help="allow the set_persona tool to write the new persona to "
             "disk so it survives restarts (default: session-only).",
    )
    parser.add_argument(
        "--max-tokens",
        default=None,
        help="cap the per-reply token budget. 'auto' lets the server "
             "decide (the default; same as today, but server defaults "
             "are often very small, e.g. 128). examples: auto, 256, "
             "1024, 4096, 8192. persists to config; toggle live with "
             "/max-tokens.",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="run a SINGLE task non-interactively (no REPL) and exit. Reads "
             "this prompt, runs one turn (with tool calls), prints the result, "
             "and returns 0 on success. Useful for scripting, CI, and being "
             "driven by another program. Use --tools to restrict the tool set.",
    )
    parser.add_argument(
        "--tools",
        default=None,
        help="comma-separated allowlist of tool names to expose for this run "
             "(e.g. read_file,write_file,edit_file). Fewer tools improves "
             "selection accuracy on small models. Only meaningful with --prompt.",
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

    if args.prompt is not None:
        from .headless import run_headless
        tool_subset = None
        if args.tools:
            tool_subset = [t.strip() for t in args.tools.split(",") if t.strip()]
        return run_headless(
            prompt=args.prompt,
            api_url=args.api,
            model=args.model,
            persona=args.persona,
            force=args.force,
            max_tokens_arg=args.max_tokens,
            tool_subset=tool_subset,
        )

    from .repl import run_repl
    return run_repl(
        api_url=args.api,
        model=args.model,
        persona=args.persona,
        force=args.force,
        bypass_perms=args.bypass_permissions,
        no_autocompact=args.no_autocompact,
        persona_evolve=args.persona_evolve,
        max_tokens_arg=args.max_tokens,
    )


if __name__ == "__main__":
    sys.exit(main())
