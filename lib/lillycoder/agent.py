"""Agent loop. Replaces the plain streaming reply in repl.py once tools
exist.

Flow per user turn:
  1. Append user message to history.
  2. Call model with messages + tool schemas. Stream visible content to
     the console as tokens arrive.
  3. After stream ends, look at the response:
     - If it contained tool calls (in the OpenAI tool_calls shape):
       a. Append the assistant message verbatim.
       b. For each call: safety classify → permission prompt → dispatch
          → append the tool result as a `tool` role message.
       c. Loop back to step 2 (model gets to react to tool results).
     - Otherwise: turn is done.
  4. Hard cap on tool-call iterations to prevent runaway loops.

This implementation assumes the model speaks the OpenAI tool-call format
natively (qwen3+, gemma3+, dolphin-r1). For models that don't, agent.py
would need to swap to a JSON-protocol prompt — kept as future work.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from . import permissions
from .endpoint import ModelInfo
from .safety import classify_command, classify_path_write
from .tools import bash as bash_module
from .tools.registry import all_tools, by_name, schemas_for_model


MAX_TOOL_ITERATIONS = 12


def _parse_tool_calls_from_chunk(d: dict, accum: dict) -> None:
    """Accumulate streaming OpenAI-style tool_calls into `accum`.
    accum maps tool_call index → {id, name, arguments_text}."""
    delta = d["choices"][0].get("delta", {})
    for tc in delta.get("tool_calls", []) or []:
        idx = tc.get("index", 0)
        slot = accum.setdefault(idx, {"id": "", "name": "", "args": ""})
        if tc.get("id"):
            slot["id"] = tc["id"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            slot["name"] = fn["name"]
        if fn.get("arguments"):
            slot["args"] += fn["arguments"]


def _format_tool_args_summary(name: str, args: dict) -> tuple[str, Optional[str]]:
    """Compact summary for the permission prompt. Returns (summary, target_path)."""
    if name == "write_file":
        return f'"{args.get("path", "?")}"  ({len(args.get("content", ""))} chars)', args.get("path")
    if name == "edit_file":
        return f'"{args.get("path", "?")}"', args.get("path")
    if name == "rm":
        return f'"{args.get("path", "?")}"  (recursive={args.get("recursive", False)})', args.get("path")
    if name == "mv":
        return f'{args.get("src", "?")} → {args.get("dst", "?")}', args.get("dst")
    if name == "mkdir":
        return f'"{args.get("path", "?")}"', args.get("path")
    if name == "bash":
        return f'{args.get("cmd", "?")}', None
    if name == "pkg_install":
        return f'{args.get("manager", "?")} install {" ".join(args.get("packages", []))}', None
    return json.dumps(args)[:200], None


def _show_diff_preview(console: Console, name: str, args: dict) -> None:
    """Before asking permission, give the user a visual preview of mutations."""
    if name == "write_file":
        path = args.get("path", "?")
        content = args.get("content", "")
        # Show first 30 lines if it's long.
        lines = content.splitlines()
        body = "\n".join(lines[:30])
        if len(lines) > 30:
            body += f"\n[…{len(lines) - 30} more lines…]"
        try:
            sx = Syntax(body, _guess_lang(path), theme="ansi_dark", line_numbers=False)
            console.print(Panel(sx, title=f"about to write → {path}", border_style="cyan"))
        except Exception:
            console.print(Panel(body, title=f"about to write → {path}", border_style="cyan"))
    elif name == "edit_file":
        console.print(Panel(
            f"path: {args.get('path')}\n\n"
            f"REPLACE:\n{args.get('old_str', '')[:400]}\n\n"
            f"WITH:\n{args.get('new_str', '')[:400]}",
            title="about to edit", border_style="cyan",
        ))


def _guess_lang(path: str) -> str:
    ext = Path(path).suffix.lstrip(".")
    return {
        "py": "python", "js": "javascript", "ts": "typescript",
        "tsx": "tsx", "jsx": "jsx", "html": "html", "css": "css",
        "json": "json", "yml": "yaml", "yaml": "yaml", "md": "markdown",
        "sh": "bash", "rs": "rust", "go": "go", "c": "c", "cpp": "cpp",
        "java": "java", "rb": "ruby", "php": "php",
    }.get(ext, "text")


def _gate_tool_call(console: Console, name: str, args: dict,
                    bypass_perms: bool, workdir: Path) -> tuple[bool, Optional[str]]:
    """Run safety + permission gates. Returns (ok_to_run, blocked_reason)."""
    tool = by_name(name)
    if tool is None:
        return False, f"unknown tool: {name}"

    # Safety: command-level
    if name == "bash":
        verdict = classify_command(args.get("cmd", ""))
        if not verdict.allowed:
            return False, f"safety: {verdict.reason}"
    if name == "pkg_install" and args.get("manager") == "apt":
        return False, "safety: apt requires sudo, refused"

    # Safety: path-scope (writes outside workspace)
    paths_to_check = []
    if name in ("write_file", "edit_file", "mkdir", "rm"):
        paths_to_check.append(args.get("path", ""))
    if name == "mv":
        paths_to_check.extend([args.get("src", ""), args.get("dst", "")])
    for p in paths_to_check:
        if not p:
            continue
        v = classify_path_write(p, workdir)
        if not v.allowed:
            return False, f"safety: {v.reason}"

    # Show preview before asking
    _show_diff_preview(console, name, args)

    # Bash safe-subset shortcut: skip permission prompt
    if name == "bash" and bash_module.is_safe_cmd(args.get("cmd", "")):
        return True, None

    # Permission prompt (unless bypass)
    if not tool.mutating:
        return True, None
    summary, target_path = _format_tool_args_summary(name, args)
    if permissions.ask(console, name, summary, target_path,
                       bypass=bypass_perms, workdir=workdir):
        return True, None
    return False, "user declined"


async def _stream_one_completion(client: httpx.Client, model: ModelInfo,
                                  messages: list[dict], console: Console
                                  ) -> tuple[str, list[dict]]:
    """Send one chat-completion request, stream content tokens to the
    console, accumulate any tool calls. Returns (content_text, [tool_calls])."""
    payload = {
        "model": model.alias,
        "messages": messages,
        "tools": schemas_for_model(),
        "tool_choice": "auto",
        "stream": True,
        "temperature": 0.5,   # lower than chat — we want decisive tool use
    }
    full_content = ""
    accum_tools: dict[int, dict] = {}
    finish_reason = None

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
                fr = d["choices"][0].get("finish_reason")
                if fr:
                    finish_reason = fr
                content = delta.get("content")
                if content:
                    full_content += content
                    console.print(content, end="", style="bright_white",
                                  highlight=False, markup=False)
                _parse_tool_calls_from_chunk(d, accum_tools)
    except httpx.HTTPError as e:
        console.print(f"\n[red]✗ network error: {e}[/red]")
    if full_content:
        console.print()  # newline after streamed content

    # Build the final tool_calls list in OpenAI shape.
    tool_calls_out = []
    for idx in sorted(accum_tools.keys()):
        slot = accum_tools[idx]
        if not slot["name"]:
            continue
        try:
            parsed_args = json.loads(slot["args"]) if slot["args"] else {}
        except json.JSONDecodeError:
            parsed_args = {}
        tool_calls_out.append({
            "id": slot["id"] or f"call_{idx}",
            "type": "function",
            "function": {"name": slot["name"], "arguments": json.dumps(parsed_args)},
            "_parsed_args": parsed_args,
        })
    return full_content, tool_calls_out


def run_turn(client: httpx.Client, model: ModelInfo,
             messages: list[dict], console: Console,
             bypass_perms: bool, workdir: Path) -> None:
    """Single user turn: may involve multiple model + tool iterations.
    Mutates `messages` in place."""
    import asyncio

    async def _go():
        for _ in range(MAX_TOOL_ITERATIONS):
            content, tool_calls = await _stream_one_completion(
                client, model, messages, console,
            )
            # Build assistant message; OpenAI shape requires content key
            # even when tool_calls present.
            asst_msg: dict = {"role": "assistant", "content": content or None}
            if tool_calls:
                asst_msg["tool_calls"] = [{
                    "id": tc["id"], "type": "function",
                    "function": tc["function"],
                } for tc in tool_calls]
            messages.append(asst_msg)

            if not tool_calls:
                return  # done

            # Execute each tool call in order.
            for tc in tool_calls:
                name = tc["function"]["name"]
                args = tc["_parsed_args"]
                ok, reason = _gate_tool_call(console, name, args,
                                              bypass_perms, workdir)
                if not ok:
                    result = {"ok": False, "error": reason}
                    console.print(f"[yellow]   ⚠ {reason}[/yellow]")
                else:
                    tool = by_name(name)
                    try:
                        result = tool.handler(**args)
                    except TypeError as e:
                        result = {"ok": False, "error": f"bad args: {e}"}
                    except Exception as e:
                        result = {"ok": False, "error": str(e)}
                    if result.get("ok"):
                        console.print(f"[green]   ✓ {name}[/green]")
                    else:
                        console.print(f"[red]   ✗ {name}: {result.get('error')}[/red]")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })
            # Loop back: model gets to read tool results and decide next.
        console.print("[yellow]   ⚠ hit max tool iterations, stopping[/yellow]")

    asyncio.run(_go())
