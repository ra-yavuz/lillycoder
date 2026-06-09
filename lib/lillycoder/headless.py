"""Headless one-shot runner: `lillycoder --prompt "..."`.

Runs a SINGLE task with no REPL and no interactive prompt, then exits. This
makes lillycoder scriptable, usable in CI, and drivable by another program
(an orchestrator). It exercises the same agent loop as the REPL via a
RecordingSink instead of an interactive Console.

Exit codes:
  0  the turn completed (the model finished, with or without tool calls)
  1  no endpoint / setup failure
  2  bad arguments (e.g. unparseable --max-tokens)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.console import Console

from . import config as _config
from .config import load_persona, list_personas
from .endpoint import acquire
from .sink import RecordingSink
from .tools import registry  # noqa: F401  (force tool registration)
from . import agent


def run_headless(prompt: str,
                 api_url: Optional[str] = None,
                 model: Optional[str] = None,
                 persona: Optional[str] = None,
                 force: bool = False,
                 max_tokens_arg: Optional[str] = None,
                 tool_subset: Optional[list[str]] = None,
                 workdir: Optional[Path] = None,
                 bypass_perms: bool = True) -> int:
    """Run one turn for `prompt` and return an exit code. bypass_perms defaults
    to True because a headless run has no human to answer permission prompts;
    the safety deny-list (sudo, rm -rf /, writes outside the workspace) still
    applies."""
    console = Console()
    wd = (workdir or Path.cwd()).resolve()

    # Resolve persona system prompt (mirrors the REPL's resolution).
    if persona is None:
        cfg = _config.load()
        last = cfg.get("ui", {}).get("last_persona")
        persona = last if (isinstance(last, str) and last in list_personas()) else "default"
    system_prompt = load_persona(persona)

    # max_tokens parse (same semantics as the REPL flag).
    max_tokens: Optional[int] = None
    if max_tokens_arg is not None:
        try:
            max_tokens = _config.parse_max_tokens(max_tokens_arg)
        except ValueError as e:
            console.print(f"[red]bad --max-tokens: {e}[/red]")
            return 2

    try:
        with acquire(api_url=api_url, preferred_model=model, force=force,
                     console=console) as (model_info, client):
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            sink = RecordingSink(echo=True)
            agent.run_turn(
                client, model_info, messages, sink,
                bypass_perms=bypass_perms, workdir=wd,
                show_thoughts=False, max_tokens=max_tokens,
                tool_subset=tool_subset,
            )
            # Final newline so piped output is clean.
            console.print()
            return 0
    except RuntimeError as e:
        # acquire raises RuntimeError("no endpoint") with its own guidance.
        if str(e) != "no endpoint":
            console.print(f"[red]error: {e}[/red]")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        return 130
