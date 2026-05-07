"""Interactive REPL.

For step 3 (no tools yet), this is plain streaming chat with the model.
Tool integration lands in steps 4-5; the agent loop in agent.py will
take over message construction once tools exist.
"""
from __future__ import annotations

import difflib
import json
import os
import sys
from pathlib import Path
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown

import time

from .agent import run_turn
from . import config as _config
from .config import (
    load_persona,
    list_personas,
    persona_origin,
    persona_path,
    add_persona,
    clone_persona,
    remove_persona,
    bundled_base_path,
    BUNDLED_PERSONAS_DIR,
    PERSONAS_DIR,
)
from .context import ContextTracker
from .endpoint import acquire
from .tools.registry import all_tools
from .tools import persona as persona_tool
from .tools import persona_admin


SLASH_HELP = """
slash commands:
  /help                       this message
  /exit                       leave (also: ctrl+d, or ctrl+c twice)
  /clear                      reset conversation history this session
  /compact                    summarise older history into a system note
  /tools                      list tools the model can call

persona system (unified namespace, all subcommands of /persona):
  /persona                    show the current persona text
  /persona list               list every available persona, with origin and
                              first-line preview
  /persona show <name>        print a persona's full text
  /persona load <name>        switch the active persona
  /persona add <name> <text>  save a new user persona from inline text
  /persona add <name> -f <path>     save a new user persona from a file
  /persona copy <src> <dst>   clone a persona under a new user-owned name
  /persona remove <name>      delete a user persona
  /persona diff <name>        compare a user shadow against bundled
  /persona active             show which persona is loaded and where
  /persona evolve [on|off]    snapshot in-memory persona to disk, evolve it

session settings (persisted in ~/.config/lillycoder/config.toml):
  /thoughts [on|off]          toggle showing the model's <think> tokens
  /autocompact [on|off]       toggle automatic compaction at 90% context
  /max-tokens [auto|<n>]      per-reply token cap. examples: auto, 256,
                              1024, 4096, 8192. 'auto' is computed from
                              the model's context window so reasoning
                              models get enough headroom for both
                              thinking and visible content

deprecated (still work, but use the /persona namespace instead):
  /personas, /setpersona, /personalities, /persona-active,
  /persona-copy, /persona-evolve
"""

# Window in which a second Ctrl+C is interpreted as "yes, really exit".
_DOUBLE_INTERRUPT_S = 2.0


def _history_path(workdir: Path) -> Path:
    """Per-project history file under .lillycoder/history.jsonl"""
    d = workdir / ".lillycoder"
    d.mkdir(exist_ok=True)
    return d / "history.jsonl"


def _line_history_path() -> Path:
    """Cross-project prompt-toolkit input line history."""
    p = Path.home() / ".config" / "lillycoder"
    p.mkdir(parents=True, exist_ok=True)
    return p / "input_history"


def _load_messages(history_file: Path, system_prompt: str) -> list[dict]:
    """Load any prior session for this folder, prepending the system prompt."""
    msgs = [{"role": "system", "content": system_prompt}]
    if not history_file.exists():
        return msgs
    for line in history_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            if d.get("role") in ("user", "assistant"):
                msgs.append({"role": d["role"], "content": d["content"]})
        except json.JSONDecodeError:
            continue
    return msgs


def _append_history(history_file: Path, role: str, content: str) -> None:
    with history_file.open("a") as f:
        f.write(json.dumps({"role": role, "content": content},
                           ensure_ascii=False) + "\n")


def run_repl(api_url: Optional[str] = None,
             model: Optional[str] = None,
             persona: Optional[str] = None,
             force: bool = False,
             bypass_perms: bool = False,
             no_autocompact: bool = False,
             persona_evolve: bool = False,
             max_tokens_arg: Optional[str] = None) -> int:
    """Main entry. Resolves an endpoint (auto-discover, --api, or saved),
    then loops on user input until /exit."""
    console = Console()
    workdir = Path.cwd()
    # Force registry imports so all tools are registered before first turn.
    from .tools import registry  # noqa: F401

    try:
        with acquire(api_url=api_url, preferred_model=model, force=force,
                     console=console) as (model, client):
            cfg = _config.load()
            # If the user didn't pass --persona explicitly, prefer the
            # last active persona we wrote to config. Falls back to the
            # bundled default.
            if persona is None:
                last = cfg.get("ui", {}).get("last_persona")
                if isinstance(last, str) and last in list_personas():
                    persona = last
                else:
                    persona = "default"
            system_prompt = load_persona(persona)
            history_file = _history_path(workdir)
            messages = _load_messages(history_file, system_prompt)
            ctx = ContextTracker(model_window=model.context_window or 8192)
            ctx.refresh(messages)

            def _remember_persona(name: str) -> None:
                """Persist `name` as the last active persona so future
                runs without --persona pick it up. Best-effort: if disk
                write fails, swallow (the in-memory state is what matters
                for the current session)."""
                try:
                    c = _config.load()
                    c.setdefault("ui", {})["last_persona"] = name
                    _config.save(c)
                except OSError:
                    pass

            # Remember the resolved bootstrap persona too, so a fresh
            # user has the row populated even if they never switch.
            _remember_persona(persona)

            # Tracks which legacy slash commands the user has already
            # been nagged about this session, so the deprecation tip
            # appears once per command, not on every invocation.
            _legacy_warned: set[str] = set()

            show_thoughts = bool(cfg.get("ui", {}).get("show_thoughts", False))
            # Autocompact: --no-autocompact CLI flag wins; otherwise the
            # persisted setting; otherwise on by default.
            if no_autocompact:
                autocompact = False
            else:
                autocompact = bool(cfg.get("ui", {}).get("autocompact", True))
            # Persona-evolve: --persona-evolve flag wins; otherwise persisted.
            if persona_evolve:
                evolve = True
            else:
                evolve = bool(cfg.get("ui", {}).get("persona_evolve", False))
            # max_tokens: CLI flag wins, otherwise persisted, otherwise None
            # (auto). Stored in config as a string ("auto" or digits) so
            # the toml writer doesn't have to know about Optional[int].
            if max_tokens_arg is not None:
                try:
                    max_tokens = _config.parse_max_tokens(max_tokens_arg)
                except ValueError as e:
                    console.print(f"[red]✗ {e}[/red]")
                    return 2
            else:
                try:
                    max_tokens = _config.parse_max_tokens(
                        cfg.get("ui", {}).get("max_tokens", "auto")
                    )
                except ValueError:
                    max_tokens = None

            # Hook the set_persona tool so the model can rewrite the
            # current system prompt. The hook is closed over the local
            # state below; we update it via the nonlocal-capturing
            # apply_persona() helper.

            def _persona_hook(text: str) -> dict:
                nonlocal system_prompt, persona
                system_prompt = text
                if messages and messages[0].get("role") == "system":
                    messages[0]["content"] = system_prompt
                else:
                    messages.insert(0, {"role": "system", "content": system_prompt})
                ctx.refresh(messages)
                # Persist to disk if persona-evolve is on. Save under the
                # current persona name (or "evolved" if it's "default" so
                # we never clobber the bundled file).
                saved_path = None
                scope = "session"
                if evolve:
                    target = persona if persona != "default" else "evolved"
                    saved_path = add_persona(target, text, overwrite=True)
                    persona = target
                    _remember_persona(persona)
                    scope = "persisted"
                if saved_path is not None:
                    console.print(
                        f"[magenta]   🪄 persona rewritten "
                        f"({persona}, {len(text)} chars) → "
                        f"{saved_path}[/magenta]"
                    )
                else:
                    console.print(
                        f"[magenta]   🪄 persona rewritten "
                        f"({persona}, {len(text)} chars, session-only; "
                        f"/persona-evolve on to persist)[/magenta]"
                    )
                return {
                    "ok": True,
                    "scope": scope,
                    "persona": persona,
                    "chars": len(text),
                    "saved_to": str(saved_path) if saved_path else None,
                }

            persona_tool.set_hook(_persona_hook)

            # Hooks for the persona_admin tools (set_active_persona,
            # set_evolve). These mirror the side effects of the
            # /personalities load and /persona-evolve slash commands so
            # the model-driven path and the user-typed path stay in
            # sync.
            def _load_active_persona(name: str) -> dict:
                nonlocal system_prompt, persona
                new_text = load_persona(name)
                if not new_text.strip():
                    return {"ok": False,
                            "error": "persona text is empty"}
                system_prompt = new_text
                persona = name
                if messages and messages[0].get("role") == "system":
                    messages[0]["content"] = system_prompt
                else:
                    messages.insert(
                        0,
                        {"role": "system", "content": system_prompt},
                    )
                ctx.refresh(messages)
                _remember_persona(persona)
                return {"ok": True, "active": persona,
                        "chars": len(system_prompt)}

            def _set_evolve(enabled: bool) -> dict:
                nonlocal evolve, persona, system_prompt
                prev = evolve
                evolve = bool(enabled)
                snapshot = None
                if evolve and not prev:
                    target = persona
                    if persona == "default":
                        target = "evolved"
                        i = 2
                        while (PERSONAS_DIR / f"{target}.md").exists():
                            target = f"evolved-{i}"
                            i += 1
                    try:
                        snapshot = add_persona(
                            target, system_prompt, overwrite=True,
                        )
                    except (ValueError, OSError) as e:
                        evolve = prev
                        return {"ok": False,
                                "error": f"snapshot failed: {e}"}
                    persona = target
                    _remember_persona(persona)
                c = _config.load()
                c.setdefault("ui", {})["persona_evolve"] = evolve
                _config.save(c)
                return {
                    "ok": True,
                    "evolve": evolve,
                    "active": persona,
                    "snapshotted_to": str(snapshot) if snapshot else None,
                }

            persona_admin.set_load_hook(_load_active_persona)
            persona_admin.set_evolve_hook(_set_evolve)

            # Build the bottom toolbar: live status that redraws while
            # the prompt is shown. Closes over the locals above so
            # toggles update on the next refresh.
            def _bottom_toolbar():
                ctx.refresh(messages)
                pct = ctx.percent()
                pct_color = (
                    "ansigreen" if pct < 70
                    else "ansiyellow" if pct < 90
                    else "ansired"
                )
                ctx_lbl = (
                    f"{ctx.window // 1024}k" if ctx.window >= 1024
                    else f"{ctx.window}"
                )
                flags = []
                if show_thoughts:
                    flags.append("thoughts")
                if not autocompact:
                    flags.append("no-autocompact")
                if evolve:
                    flags.append("evolve")
                if bypass_perms:
                    flags.append("bypass-perms")
                flag_str = (" · " + ", ".join(flags)) if flags else ""
                # Keep this short: prompt_toolkit drops the toolbar if
                # there isn't enough vertical room for prompt + toolbar.
                # A long toolbar that wraps to two lines is the usual
                # cause of the toolbar "disappearing" while typing.
                mt_lbl = "auto" if max_tokens is None else str(max_tokens)
                return HTML(
                    f"<ansimagenta>🦊</ansimagenta> "
                    f"<ansicyan>{model.alias}</ansicyan> · "
                    f"<{pct_color}>{pct:.0f}% of {ctx_lbl}</{pct_color}> · "
                    f"{persona} · "
                    f"max:{mt_lbl}"
                    f"{flag_str}"
                )

            session = PromptSession(
                history=FileHistory(str(_line_history_path())),
                bottom_toolbar=_bottom_toolbar,
                refresh_interval=0.5,
            )

            console.rule(style="grey39")
            console.print(
                f"🦊 [bold magenta]lilly[/bold magenta] is awake in "
                f"[dim]{workdir}[/dim] · "
                f"[dim]/help · /exit · ctrl+d to leave · ctrl+c twice[/dim]"
            )
            console.rule(style="grey39")

            last_interrupt = 0.0
            prompt_html = HTML("<ansicyan>› </ansicyan>")
            while True:
                try:
                    user_input = session.prompt(prompt_html)
                except EOFError:
                    # Ctrl+D - definite exit.
                    console.print()
                    break
                except KeyboardInterrupt:
                    # Ctrl+C at the prompt: clear line and stay. A second
                    # Ctrl+C within the window confirms exit.
                    now = time.monotonic()
                    if now - last_interrupt < _DOUBLE_INTERRUPT_S:
                        console.print()
                        break
                    last_interrupt = now
                    console.print(
                        "[dim]   (ctrl+c again within 2s to exit, or /exit)[/dim]"
                    )
                    continue
                # Successful read: forget any prior single-tap.
                last_interrupt = 0.0

                user_input = user_input.strip()
                if not user_input:
                    continue

                # Slash command?
                if user_input.startswith("/"):
                    cmd = user_input.split(None, 1)[0].lower()
                    if cmd in ("/exit", "/quit", "/q"):
                        break
                    if cmd == "/help":
                        console.print(SLASH_HELP, style="grey78")
                        continue

                    # Legacy-command rewriter. Translates the old
                    # commands (/personas, /setpersona, /persona-active,
                    # /persona-copy, /persona-evolve, /personalities) to
                    # the unified /persona <subcommand> shape so the
                    # rest of the dispatch only needs to know about one
                    # namespace. Each legacy command shows a one-shot
                    # deprecation tip in this session, then keeps
                    # working unchanged.
                    legacy_rewrite: Optional[tuple[str, str]] = None
                    if cmd == "/personas":
                        legacy_rewrite = (cmd, "/persona list "
                                          + user_input[len("/personas"):].strip())
                    elif cmd == "/personalities":
                        # Already accepts a subcommand; just route to
                        # /persona with the same tail.
                        tail = user_input[len("/personalities"):].strip()
                        legacy_rewrite = (cmd, ("/persona " + tail).strip())
                    elif cmd == "/setpersona":
                        rest = user_input[len("/setpersona"):].strip()
                        if rest.startswith("-f"):
                            # /setpersona -f <path>: there's no direct
                            # /persona equivalent (we don't have a
                            # "load from path" subcommand), so leave
                            # this one to the original handler.
                            legacy_rewrite = None
                        elif " " not in rest and rest in list_personas():
                            legacy_rewrite = (cmd, f"/persona load {rest}")
                        else:
                            # Inline text; leave to original handler
                            # which sets a "custom" inline persona.
                            legacy_rewrite = None
                    elif cmd == "/persona-active":
                        legacy_rewrite = (cmd, "/persona active")
                    elif cmd == "/persona-copy":
                        tail = user_input[len("/persona-copy"):].strip()
                        legacy_rewrite = (cmd, ("/persona copy " + tail).strip())
                    elif cmd == "/persona-evolve":
                        tail = user_input[len("/persona-evolve"):].strip()
                        legacy_rewrite = (cmd, ("/persona evolve " + tail).strip())

                    if legacy_rewrite is not None:
                        old_cmd, new_input = legacy_rewrite
                        if old_cmd not in _legacy_warned:
                            _legacy_warned.add(old_cmd)
                            new_form = new_input.split(None, 1)[0]
                            console.print(
                                f"[dim]   (note: {old_cmd} is deprecated; "
                                f"use {new_form} instead. both still work.)[/dim]"
                            )
                        user_input = new_input
                        cmd = user_input.split(None, 1)[0].lower()

                    if cmd == "/clear":
                        messages = [{"role": "system", "content": system_prompt}]
                        history_file.write_text("")
                        ctx.refresh(messages)
                        console.print("[dim]✓ session cleared[/dim]")
                        continue
                    if cmd == "/compact":
                        try:
                            ctx.compact(messages, system_prompt, client, model)
                            console.print("[dim]✓ compacted[/dim]")
                        except Exception as e:
                            console.print(f"[red]✗ compact failed: {e}[/red]")
                        continue
                    if cmd == "/persona":
                        rest_after_cmd = user_input[len("/persona"):].strip()
                        if not rest_after_cmd:
                            # /persona with no args: print the current
                            # system-prompt text, same as before.
                            console.print(Markdown(f"```\n{system_prompt}\n```"))
                            continue
                        # /persona <subcommand>: fall through to the
                        # unified handler below (formerly /personalities).
                    if cmd == "/personas":
                        for n in list_personas():
                            origin = persona_origin(n)
                            tag = "[magenta]user[/magenta]" if origin == "user" else "[blue]bundled[/blue]"
                            marker = " [dim](current)[/dim]" if n == persona else ""
                            console.print(f"  [cyan]{n}[/cyan] {tag}{marker}")
                        continue
                    if cmd == "/setpersona":
                        rest = user_input[len("/setpersona"):].strip()
                        if not rest:
                            console.print(
                                "[yellow]usage: /setpersona <name> | -f <path> | <text...>[/yellow]"
                            )
                            continue
                        new_prompt: Optional[str] = None
                        new_label: str = "custom"
                        if rest.startswith("-f"):
                            path_str = rest[2:].strip()
                            if not path_str:
                                console.print("[yellow]usage: /setpersona -f <path>[/yellow]")
                                continue
                            p = Path(path_str).expanduser()
                            if not p.is_file():
                                console.print(f"[red]✗ no such file: {p}[/red]")
                                continue
                            try:
                                new_prompt = p.read_text()
                            except OSError as e:
                                console.print(f"[red]✗ read failed: {e}[/red]")
                                continue
                            new_label = f"file:{p}"
                        elif " " not in rest and rest in list_personas():
                            new_prompt = load_persona(rest)
                            new_label = rest
                        else:
                            new_prompt = rest
                        if not new_prompt or not new_prompt.strip():
                            console.print("[red]✗ empty persona, ignored[/red]")
                            continue
                        system_prompt = new_prompt
                        persona = new_label
                        _remember_persona(persona)
                        if messages and messages[0].get("role") == "system":
                            messages[0]["content"] = system_prompt
                        else:
                            messages.insert(0, {"role": "system", "content": system_prompt})
                        ctx.refresh(messages)
                        console.print(f"[dim]✓ persona set ({new_label}, {len(system_prompt)} chars)[/dim]")
                        continue
                    if cmd in ("/personalities", "/persona"):
                        prefix_len = len(cmd)
                        rest = user_input[prefix_len:].strip()
                        parts = rest.split(None, 1)
                        sub = parts[0].lower() if parts else "list"
                        tail = parts[1] if len(parts) > 1 else ""
                        if sub in ("list", "ls", ""):
                            # First-line preview helper: read the file
                            # and return the first non-empty line,
                            # truncated. Cheap; runs once per persona.
                            def _preview(name: str) -> str:
                                p = persona_path(name)
                                if p is None:
                                    return ""
                                try:
                                    text = p.read_text()
                                except OSError:
                                    return ""
                                for line in text.splitlines():
                                    s = line.strip()
                                    if s:
                                        return s if len(s) <= 70 else s[:67] + "..."
                                return ""
                            for n in list_personas():
                                origin = persona_origin(n)
                                tag = "[magenta]user[/magenta]" if origin == "user" else "[blue]bundled[/blue]"
                                marker = " [dim](current)[/dim]" if n == persona else ""
                                preview = _preview(n)
                                preview_str = (
                                    f" [dim]- {preview}[/dim]" if preview else ""
                                )
                                console.print(
                                    f"  [cyan]{n}[/cyan] {tag}{marker}{preview_str}"
                                )
                            continue
                        if sub == "show":
                            target = tail.strip()
                            if not target:
                                console.print("[yellow]usage: /persona show <name>[/yellow]")
                                continue
                            p = persona_path(target)
                            if p is None:
                                console.print(f"[red]✗ no such personality: {target}[/red]")
                                continue
                            try:
                                console.print(Markdown(f"```\n{p.read_text()}\n```"))
                            except OSError as e:
                                console.print(f"[red]✗ read failed: {e}[/red]")
                            continue
                        if sub == "load":
                            target = tail.strip()
                            if not target:
                                console.print("[yellow]usage: /persona load <name>[/yellow]")
                                continue
                            if target not in list_personas():
                                console.print(f"[red]✗ no such personality: {target}[/red]")
                                continue
                            new_prompt = load_persona(target)
                            if not new_prompt.strip():
                                console.print("[red]✗ empty persona, ignored[/red]")
                                continue
                            system_prompt = new_prompt
                            persona = target
                            _remember_persona(persona)
                            if messages and messages[0].get("role") == "system":
                                messages[0]["content"] = system_prompt
                            else:
                                messages.insert(0, {"role": "system", "content": system_prompt})
                            ctx.refresh(messages)
                            console.print(f"[dim]✓ persona set ({target}, {len(system_prompt)} chars)[/dim]")
                            continue
                        if sub == "add":
                            add_parts = tail.split(None, 1)
                            if len(add_parts) < 2:
                                console.print(
                                    "[yellow]usage: /persona add <name> -f <path> | <text...>[/yellow]"
                                )
                                continue
                            new_name, body = add_parts[0], add_parts[1].strip()
                            overwrite = False
                            if body.startswith("--force "):
                                overwrite = True
                                body = body[len("--force "):].strip()
                            elif body == "--force":
                                console.print(
                                    "[yellow]usage: /persona add <name> [--force] -f <path> | <text...>[/yellow]"
                                )
                                continue
                            if body.startswith("-f"):
                                path_str = body[2:].strip()
                                if not path_str:
                                    console.print("[yellow]usage: /persona add <name> -f <path>[/yellow]")
                                    continue
                                src = Path(path_str).expanduser()
                                if not src.is_file():
                                    console.print(f"[red]✗ no such file: {src}[/red]")
                                    continue
                                try:
                                    body = src.read_text()
                                except OSError as e:
                                    console.print(f"[red]✗ read failed: {e}[/red]")
                                    continue
                            try:
                                saved = add_persona(new_name, body, overwrite=overwrite)
                            except FileExistsError as e:
                                console.print(
                                    f"[yellow]✗ already exists: {e}. add --force to overwrite.[/yellow]"
                                )
                                continue
                            except (ValueError, OSError) as e:
                                console.print(f"[red]✗ {e}[/red]")
                                continue
                            console.print(f"[dim]✓ saved → {saved}[/dim]")
                            continue
                        if sub in ("remove", "rm", "delete", "del"):
                            target = tail.strip()
                            if not target:
                                console.print("[yellow]usage: /persona remove <name>[/yellow]")
                                continue
                            result = remove_persona(target)
                            if result == "removed":
                                console.print(f"[dim]✓ removed user persona: {target}[/dim]")
                            elif result == "bundled":
                                console.print(
                                    f"[yellow]✗ '{target}' is bundled and cannot be removed. "
                                    f"create a user file with the same name to override it.[/yellow]"
                                )
                            else:
                                console.print(f"[red]✗ no such personality: {target}[/red]")
                            continue
                        if sub == "diff":
                            target = tail.strip()
                            if not target:
                                console.print("[yellow]usage: /persona diff <name>[/yellow]")
                                continue
                            user_file = PERSONAS_DIR / f"{target}.md"
                            bundled_file = BUNDLED_PERSONAS_DIR / f"{target}.md"
                            base_file = bundled_base_path(target)
                            if not user_file.exists():
                                if bundled_file.exists():
                                    console.print(
                                        f"[dim]'{target}' has no user shadow; "
                                        f"loading the bundled file directly. "
                                        f"nothing to diff.[/dim]"
                                    )
                                else:
                                    console.print(
                                        f"[red]✗ no such personality: {target}[/red]"
                                    )
                                continue
                            try:
                                user_text = user_file.read_text()
                            except OSError as e:
                                console.print(f"[red]✗ read failed: {e}[/red]")
                                continue
                            shown_any = False
                            if bundled_file.exists():
                                try:
                                    bundled_text = bundled_file.read_text()
                                except OSError as e:
                                    bundled_text = ""
                                    console.print(
                                        f"[yellow]bundled read failed: {e}[/yellow]"
                                    )
                                console.print(
                                    f"[bold]── your shadow vs current bundled "
                                    f"({target}) ──[/bold]"
                                )
                                diff = list(difflib.unified_diff(
                                    bundled_text.splitlines(keepends=True),
                                    user_text.splitlines(keepends=True),
                                    fromfile=f"bundled/{target}.md",
                                    tofile=f"user/{target}.md",
                                    n=2,
                                ))
                                if diff:
                                    for line in diff:
                                        if line.startswith("+++") or line.startswith("---"):
                                            console.print(
                                                f"[bold]{line.rstrip()}[/bold]"
                                            )
                                        elif line.startswith("@@"):
                                            console.print(
                                                f"[cyan]{line.rstrip()}[/cyan]"
                                            )
                                        elif line.startswith("+"):
                                            console.print(
                                                f"[green]{line.rstrip()}[/green]"
                                            )
                                        elif line.startswith("-"):
                                            console.print(
                                                f"[red]{line.rstrip()}[/red]"
                                            )
                                        else:
                                            console.print(line.rstrip())
                                else:
                                    console.print(
                                        "[dim](identical)[/dim]"
                                    )
                                shown_any = True
                                if base_file.exists():
                                    try:
                                        base_text = base_file.read_text()
                                    except OSError:
                                        base_text = ""
                                    console.print()
                                    console.print(
                                        f"[bold]── upstream drift since you "
                                        f"shadowed: bundled-base vs current "
                                        f"bundled ──[/bold]"
                                    )
                                    drift = list(difflib.unified_diff(
                                        base_text.splitlines(keepends=True),
                                        bundled_text.splitlines(keepends=True),
                                        fromfile=f"bundled-base/{target}.md",
                                        tofile=f"bundled/{target}.md",
                                        n=2,
                                    ))
                                    if drift:
                                        for line in drift:
                                            if line.startswith("+++") or line.startswith("---"):
                                                console.print(
                                                    f"[bold]{line.rstrip()}[/bold]"
                                                )
                                            elif line.startswith("@@"):
                                                console.print(
                                                    f"[cyan]{line.rstrip()}[/cyan]"
                                                )
                                            elif line.startswith("+"):
                                                console.print(
                                                    f"[green]{line.rstrip()}[/green]"
                                                )
                                            elif line.startswith("-"):
                                                console.print(
                                                    f"[red]{line.rstrip()}[/red]"
                                                )
                                            else:
                                                console.print(line.rstrip())
                                    else:
                                        console.print(
                                            "[dim](upstream unchanged since "
                                            "you shadowed)[/dim]"
                                        )
                                else:
                                    console.print()
                                    console.print(
                                        "[dim](no bundled-base sidecar; "
                                        "this shadow predates the sidecar "
                                        "feature, so we can't show drift "
                                        "history.)[/dim]"
                                    )
                            else:
                                console.print(
                                    f"[dim]'{target}' is a user-only persona "
                                    f"(no bundled counterpart). nothing to "
                                    f"diff against.[/dim]"
                                )
                            if not shown_any:
                                continue
                            continue
                        if sub == "copy":
                            copy_parts = tail.split()
                            force = False
                            if "--force" in copy_parts:
                                force = True
                                copy_parts = [p for p in copy_parts if p != "--force"]
                            if len(copy_parts) != 2:
                                console.print(
                                    "[yellow]usage: /persona copy <src> <dst> "
                                    "[--force][/yellow]"
                                )
                                continue
                            src, dst = copy_parts
                            try:
                                saved = clone_persona(src, dst, overwrite=force)
                            except FileNotFoundError:
                                console.print(
                                    f"[red]✗ no such persona: {src}[/red]"
                                )
                                continue
                            except FileExistsError:
                                console.print(
                                    f"[yellow]✗ {dst} already exists. "
                                    f"add --force to overwrite.[/yellow]"
                                )
                                continue
                            except (ValueError, OSError) as e:
                                console.print(f"[red]✗ {e}[/red]")
                                continue
                            console.print(
                                f"[dim]✓ copied {src} → {dst} ({saved})[/dim]"
                            )
                            continue
                        if sub == "active":
                            origin = persona_origin(persona)
                            path = persona_path(persona)
                            path_str = (
                                str(path) if path is not None else "(in-memory only)"
                            )
                            console.print(
                                f"[bold cyan]{persona}[/bold cyan] "
                                f"[dim]({origin})[/dim]"
                            )
                            console.print(f"  [dim]{path_str}[/dim]")
                            console.print(
                                f"  [dim]{len(system_prompt)} chars in memory[/dim]"
                            )
                            if path is not None:
                                try:
                                    disk_text = path.read_text()
                                    if disk_text != system_prompt:
                                        console.print(
                                            "  [yellow]note: in-memory text "
                                            "differs from the file on disk "
                                            "(unsaved evolution).[/yellow]"
                                        )
                                except OSError:
                                    pass
                            continue
                        if sub == "evolve":
                            arg = tail.strip().lower()
                            prev_evolve = evolve
                            if arg in ("on", "true", "1", "yes"):
                                evolve = True
                            elif arg in ("off", "false", "0", "no"):
                                evolve = False
                            elif arg == "":
                                evolve = not evolve
                            else:
                                console.print(
                                    "[yellow]usage: /persona evolve [on|off][/yellow]"
                                )
                                continue
                            if evolve and not prev_evolve:
                                target = persona
                                if persona == "default":
                                    target = "evolved"
                                    i = 2
                                    while (PERSONAS_DIR / f"{target}.md").exists():
                                        target = f"evolved-{i}"
                                        i += 1
                                try:
                                    saved = add_persona(
                                        target, system_prompt, overwrite=True,
                                    )
                                except (ValueError, OSError) as e:
                                    console.print(
                                        f"[red]✗ snapshot failed: {e}[/red]"
                                    )
                                    evolve = prev_evolve
                                    continue
                                persona = target
                                _remember_persona(persona)
                                console.print(
                                    f"[magenta]   🪄 snapshotted current persona "
                                    f"as [cyan]{persona}[/cyan] → "
                                    f"{saved}[/magenta]"
                                )
                            cfg2 = _config.load()
                            cfg2.setdefault("ui", {})["persona_evolve"] = evolve
                            _config.save(cfg2)
                            state = "on" if evolve else "off"
                            console.print(f"[dim]✓ persona-evolve {state}[/dim]")
                            continue
                        console.print(
                            "[yellow]usage: /persona "
                            "[list|show|load|add|copy|remove|diff|active|evolve] "
                            "...[/yellow]"
                        )
                        continue
                    if cmd == "/autocompact":
                        rest = user_input[len("/autocompact"):].strip().lower()
                        if rest in ("on", "true", "1", "yes"):
                            autocompact = True
                        elif rest in ("off", "false", "0", "no"):
                            autocompact = False
                        elif rest == "":
                            autocompact = not autocompact
                        else:
                            console.print("[yellow]usage: /autocompact [on|off][/yellow]")
                            continue
                        cfg = _config.load()
                        cfg.setdefault("ui", {})["autocompact"] = autocompact
                        _config.save(cfg)
                        state = "on" if autocompact else "off"
                        console.print(f"[dim]✓ autocompact {state}[/dim]")
                        continue
                    if cmd == "/max-tokens":
                        rest = user_input[len("/max-tokens"):].strip()
                        if rest == "":
                            current = (
                                "auto" if max_tokens is None else str(max_tokens)
                            )
                            console.print(
                                f"[dim]max_tokens = {current}. usage: "
                                f"/max-tokens [auto|<n>]. examples: auto, "
                                f"256, 1024, 4096, 8192.[/dim]"
                            )
                            continue
                        try:
                            new_val = _config.parse_max_tokens(rest)
                        except ValueError as e:
                            console.print(f"[red]✗ {e}[/red]")
                            continue
                        max_tokens = new_val
                        cfg = _config.load()
                        cfg.setdefault("ui", {})["max_tokens"] = (
                            "auto" if max_tokens is None else str(max_tokens)
                        )
                        _config.save(cfg)
                        shown = "auto" if max_tokens is None else str(max_tokens)
                        console.print(f"[dim]✓ max_tokens {shown}[/dim]")
                        continue
                    if cmd == "/thoughts":
                        rest = user_input[len("/thoughts"):].strip().lower()
                        if rest in ("on", "true", "1", "yes"):
                            show_thoughts = True
                        elif rest in ("off", "false", "0", "no"):
                            show_thoughts = False
                        elif rest == "":
                            show_thoughts = not show_thoughts
                        else:
                            console.print("[yellow]usage: /thoughts [on|off][/yellow]")
                            continue
                        cfg = _config.load()
                        cfg.setdefault("ui", {})["show_thoughts"] = show_thoughts
                        _config.save(cfg)
                        state = "on" if show_thoughts else "off"
                        console.print(f"[dim]✓ thoughts {state}[/dim]")
                        continue
                    if cmd == "/tools":
                        for t in all_tools():
                            tag = "[red]mut[/red]" if t.mutating else "[green]ro[/green]"
                            console.print(f"  {tag} [cyan]{t.name}[/cyan]  {t.description}")
                        continue
                    console.print(f"[yellow]unknown: {cmd} — try /help[/yellow]")
                    continue

                # Auto-compact at 90% (unless disabled).
                if ctx.percent() >= 90:
                    if autocompact:
                        console.print(
                            "[yellow]   context nearly full - auto-compacting before turn...[/yellow]"
                        )
                        try:
                            ctx.compact(messages, system_prompt, client, model)
                        except Exception as e:
                            console.print(f"[red]   ✗ auto-compact failed: {e}[/red]")
                    else:
                        console.print(
                            "[yellow]   ⚠ context >90% full and autocompact is off; "
                            "the model may truncate. Use /compact to compact now, "
                            "/autocompact on to re-enable.[/yellow]"
                        )

                # Chat turn - agent loop with tool dispatch.
                messages.append({"role": "user", "content": user_input})
                _append_history(history_file, "user", user_input)
                console.print()  # blank line before reply
                try:
                    run_turn(client, model, messages, console,
                             bypass_perms=bypass_perms, workdir=workdir,
                             show_thoughts=show_thoughts,
                             max_tokens=max_tokens)
                except KeyboardInterrupt:
                    # Turn was cut short. Stay in the REPL; reset the
                    # double-tap window so the same Ctrl+C that stopped
                    # the model doesn't also pre-arm an exit.
                    last_interrupt = 0.0
                # Persist any new assistant + tool messages from this turn.
                # (We re-write a slim version that stores only chat
                # messages, not tool-call internals - those are session-local.)
                _persist_assistant_msgs(history_file, messages)
                console.print()

            return 0
    except RuntimeError as e:
        console.print(f"[red]✗ {e}[/red]")
        return 1


def _persist_assistant_msgs(history_file: Path, messages: list[dict]) -> None:
    """Append only the most recent assistant content message (no tool internals)."""
    # Find the latest assistant message with non-empty string content.
    for m in reversed(messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"]:
            _append_history(history_file, "assistant", m["content"])
            return
