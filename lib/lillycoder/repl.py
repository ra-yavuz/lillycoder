"""Interactive REPL.

For step 3 (no tools yet), this is plain streaming chat with the model.
Tool integration lands in steps 4-5; the agent loop in agent.py will
take over message construction once tools exist.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown

import time

from .agent import run_turn
from . import config as _config
from .config import load_persona, list_personas
from .context import ContextTracker
from .endpoint import acquire, ModelInfo
from .tools.registry import all_tools


SLASH_HELP = """
slash commands:
  /help                       this message
  /exit                       leave (also: ctrl+d, or ctrl+c twice)
  /clear                      reset conversation history this session
  /compact                    (placeholder - autocompact lands in step 7)
  /tools                      list available tools (lands in step 4+)
  /persona                    show the current persona text
  /personas                   list saved personas
  /setpersona <name>          switch to a saved persona by name
  /setpersona -f <path>       load persona text from a file
  /setpersona <text...>       set persona inline to the given text
  /thoughts [on|off]          toggle showing the model's <think> tokens
  /autocompact [on|off]       toggle automatic compaction at 90% context
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


def _stream_reply(client: httpx.Client, model: ModelInfo,
                  messages: list[dict], console: Console) -> str:
    """Send a chat-completion request, stream tokens to the console as they
    arrive, return the full text."""
    payload = {
        "model": model.alias,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
    }
    full = ""
    try:
        with client.stream("POST", "/chat/completions",
                           json=payload, timeout=None) as resp:
            for raw in resp.iter_lines():
                if not raw or not raw.startswith("data: "):
                    continue
                body = raw[6:]
                if body == "[DONE]":
                    break
                try:
                    d = json.loads(body)
                except json.JSONDecodeError:
                    continue
                delta = d["choices"][0].get("delta", {})
                chunk = delta.get("content")
                if chunk:
                    full += chunk
                    console.print(chunk, end="", style="bright_white",
                                  highlight=False, markup=False)
    except httpx.HTTPError as e:
        console.print(f"\n[red]✗ network error: {e}[/red]")
    console.print()
    return full


def run_repl(api_url: Optional[str] = None,
             model: Optional[str] = None,
             persona: str = "default",
             force: bool = False,
             bypass_perms: bool = False,
             no_autocompact: bool = False) -> int:
    """Main entry. Resolves an endpoint (auto-discover, --api, or saved),
    then loops on user input until /exit."""
    console = Console()
    workdir = Path.cwd()
    # Force registry imports so all tools are registered before first turn.
    from .tools import registry  # noqa: F401

    try:
        with acquire(api_url=api_url, preferred_model=model, force=force,
                     console=console) as (model, client):
            system_prompt = load_persona(persona)
            history_file = _history_path(workdir)
            messages = _load_messages(history_file, system_prompt)
            session = PromptSession(history=FileHistory(str(_line_history_path())))
            ctx = ContextTracker(model_window=model.context_window or 8192)
            ctx.refresh(messages)

            console.rule(style="grey39")
            cfg = _config.load()
            show_thoughts = bool(cfg.get("ui", {}).get("show_thoughts", False))
            # Autocompact: --no-autocompact CLI flag wins; otherwise the
            # persisted setting; otherwise on by default.
            if no_autocompact:
                autocompact = False
            else:
                autocompact = bool(cfg.get("ui", {}).get("autocompact", True))
            ctx_label = (
                f"{ctx.window // 1024}k ctx" if ctx.window >= 1024
                else f"{ctx.window} ctx"
            )
            ctx_source = "server" if model.context_window else "fallback"
            console.print(
                f"🦊 [bold magenta]lilly[/bold magenta] is awake · "
                f"[cyan]{model.alias}[/cyan] · "
                f"[dim]{model.endpoint.label}@{model.endpoint.base_url}[/dim] · "
                f"[dim]{ctx_label} ({ctx_source})[/dim] · "
                f"[dim]{workdir}[/dim]  ·  {len(all_tools())} tools"
                + ("  ·  [yellow]bypass-perms[/yellow]" if bypass_perms else "")
            )
            console.print(
                "[dim]   type a message · /help for commands · /exit to leave[/dim]"
            )
            console.rule(style="grey39")

            last_interrupt = 0.0
            while True:
                # REPL prompt shows live context usage.
                ctx.refresh(messages)
                pct = ctx.percent()
                ctx_color = "ansigreen" if pct < 70 else (
                    "ansiyellow" if pct < 90 else "ansired")
                prompt_html = HTML(
                    f"<{ctx_color}>[ctx {ctx.estimated:.0f}/{ctx.window}·{pct:.0f}%]</{ctx_color}> "
                    f"<ansicyan>› </ansicyan>"
                )
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
                        console.print(Markdown(f"```\n{system_prompt}\n```"))
                        continue
                    if cmd == "/personas":
                        for n in list_personas():
                            marker = " [dim](current)[/dim]" if n == persona else ""
                            console.print(f"  [cyan]{n}[/cyan]{marker}")
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
                        if messages and messages[0].get("role") == "system":
                            messages[0]["content"] = system_prompt
                        else:
                            messages.insert(0, {"role": "system", "content": system_prompt})
                        ctx.refresh(messages)
                        console.print(f"[dim]✓ persona set ({new_label}, {len(system_prompt)} chars)[/dim]")
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
                             show_thoughts=show_thoughts)
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
