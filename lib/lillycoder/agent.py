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
would need to swap to a JSON-protocol prompt (kept as future work).
"""
from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Callable, Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from . import permissions
from .context import (
    bound_json_value,
    estimate_messages,
    estimate_tools,
    safe_working_window,
)
from .endpoint import ModelInfo
from .safety import classify_command, classify_path_write
from .sink import as_sink as _as_sink
from .tools import bash as bash_module
from .tools.registry import all_tools, by_name, schemas_for_model
from .spinner import Spinner


def _looks_complete(content: Optional[str]) -> bool:
    """Heuristic: does the assistant's tool-less reply read as a finished turn
    rather than a narration of an action it forgot to perform? Used to decide
    whether to nudge. Conservative: only treat clear completion signals as
    'done' so we err toward nudging when ambiguous."""
    if not content:
        return False
    c = content.strip().lower()
    if not c:
        return False
    if "done" in c[:8] or c.startswith("done"):
        return True
    # Narration markers that mean "about to act" => NOT complete.
    narration = ("i'll ", "i will ", "let me ", "first i", "next i",
                 "i'm going to", "i am going to", "i need to")
    if any(c.startswith(n) or (" " + n) in c[:40] for n in narration):
        return False
    # A reply that ends with a question or is a plain answer (no pending file
    # op implied) is treated as complete.
    return c.endswith((".", "!", "?", "✨", "`")) or len(c) < 200


MAX_TOOL_ITERATIONS = 12


class ModelRequestError(RuntimeError):
    """A model request failed without terminating the LillyCoder process."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code

    @property
    def context_related(self) -> bool:
        text = str(self).lower()
        signals = (
            "context", "too many tokens", "token limit", "prompt is too long",
            "exceeds the available", "kv cache", "n_ctx", "input too long",
        )
        return any(signal in text for signal in signals)

    @property
    def oom_related(self) -> bool:
        text = str(self).lower()
        signals = ("out of memory", "oom", "memory allocation", "killed")
        return any(signal in text for signal in signals)


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


class RepeatGuard:
    """Suppress IDENTICAL read-only tool calls within one turn.

    Weak local models loop on re-reading what they already saw (observed
    live: a repair worker grepped, read the right 400-line region, then
    re-read the same region twice more and died of context bloat without
    editing). Repeating a non-mutating call cannot produce new information
    unless state changed, so the repeat gets a short stub telling the model
    to act on what it has. Any mutating call resets the guard, keeping the
    legitimate edit-then-re-read verification pattern intact.
    """

    NOTE = ("You already made this exact call this turn and the result has "
            "not changed; it is not repeated here to save context. Act on "
            "what you already read: if you have located the defect, apply "
            "the fix with edit_file now.")

    def __init__(self):
        self._seen: set[tuple[str, str]] = set()

    def is_repeat(self, name: str, args: dict, mutating: bool) -> bool:
        """True if this exact non-mutating call already ran this turn.
        Mutating calls always run, and reset the guard."""
        if mutating:
            self._seen.clear()
            return False
        key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        if key in self._seen:
            return True
        self._seen.add(key)
        return False

    def stub_result(self) -> dict:
        return {"ok": True, "repeat_suppressed": True, "note": self.NOTE}


def _gate_tool_call(console: Console, name: str, args: dict,
                    bypass_perms: bool, workdir: Path,
                    bash_deny: Optional[list] = None) -> tuple[bool, Optional[str]]:
    """Run safety + permission gates. Returns (ok_to_run, blocked_reason).

    `bash_deny` is an optional list of pre-compiled regex patterns; a bash
    command matching any of them is refused. This is a CALLER policy layer
    (an embedder like an orchestrator forbidding package installs in an
    autonomous run), distinct from the always-on hard-deny safety classifier.
    Default None means no extra denial, so the standalone REPL is unaffected."""
    tool = by_name(name)
    if tool is None:
        return False, f"unknown tool: {name}"

    # Safety: command-level
    if name == "bash":
        cmd = args.get("cmd", "")
        verdict = classify_command(cmd)
        if not verdict.allowed:
            return False, f"safety: {verdict.reason}"
        for pat in (bash_deny or []):
            if pat.search(cmd):
                return False, (f"policy: command matches a denied pattern "
                               f"({pat.pattern!r}); this run forbids it")
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


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"

# Harmony / OpenAI-channel control-token markers. Some recent local
# models (gemma uncensored MoE, qwen3-MoE, deepseek-r1 derivatives) emit
# their reasoning wrapped in <|channel|>NAME<|message|>...<|end|>
# blocks instead of (or alongside) <think>...</think>. We treat any
# channel that isn't "final"/"response" as thought, hide its content
# unless /thoughts on, and strip the control tokens themselves so they
# never leak to the user.
_HARMONY_VISIBLE_CHANNELS = {"final", "response"}


def _emit_segment(console: Console, text: str, *, in_thought: bool,
                   show_thoughts: bool) -> None:
    """Render a chunk to the console. Thought segments are styled
    distinctly (italic dim) and only shown when show_thoughts is True;
    visible content is always printed. Also forwards the chunk to the sink's
    structured on_token hook so embedders get the token stream without parsing
    rendered terminal text."""
    if not text:
        return
    # Structured event for embedders (no-op on a plain Console).
    hook = getattr(console, "on_token", None)
    if callable(hook):
        hook(text, in_thought)
    if in_thought:
        if show_thoughts:
            console.print(text, end="", style="italic grey50",
                          highlight=False, markup=False)
        return
    console.print(text, end="", style="bright_white",
                  highlight=False, markup=False)


def _strip_harmony_tokens(buf: str, state: dict) -> str:
    """Remove harmony channel/message/end control tokens from `buf`,
    and silently consume the channel-name plaintext that follows
    <|channel|>. Mutates state['in_thought'] based on which channel we
    are in.

    Harmony shape:
        <|channel|>analysis<|message|>...thought body...<|end|>
        <|channel|>final<|message|>...visible body...<|end|>

    State machine:
        normal       -> see `<|channel|>` -> AWAIT_CHANNEL_NAME
        AWAIT_CHANNEL_NAME -> next non-control text is the channel name
                              (we strip it). See `<|message|>` ->
                              IN_BODY (visible if channel is
                              final/response, thought otherwise)
        IN_BODY      -> see `<|end|>`/`<|return|>` -> normal

    Partial tokens at the end of `buf` are kept in
    state['harmony_carry'] and re-prepended on the next call.
    Returns the cleaned text, ready to be fed into
    _route_content_chunk for <think>-tag handling on top.
    """
    text = state.get("harmony_carry", "") + buf
    state["harmony_carry"] = ""
    out: list[str] = []
    i = 0
    n = len(text)

    # Three modes:
    #   "normal"        -- between channel blocks (no body active)
    #   "channel_name"  -- inside <|channel|>...<|message|>, swallow text
    #                       as the channel name
    #   "in_body"       -- inside a body, plaintext is real content;
    #                       state['harmony_in_thought_body'] tells us
    #                       whether to wrap it in synthetic <think> tags
    mode = state.get("harmony_mode", "normal")

    def open_thought():
        if not state.get("harmony_in_thought_body"):
            out.append(_THINK_OPEN)
            state["harmony_in_thought_body"] = True

    def close_thought():
        if state.get("harmony_in_thought_body"):
            out.append(_THINK_CLOSE)
            state["harmony_in_thought_body"] = False

    while i < n:
        start = text.find("<|", i)
        if start < 0:
            # Tail with no further control tokens.
            tail = text[i:]
            if mode == "channel_name":
                state["harmony_pending_name"] = (
                    state.get("harmony_pending_name", "") + tail
                )
            else:
                out.append(tail)
            break

        chunk_pre = text[i:start]
        if chunk_pre:
            if mode == "channel_name":
                state["harmony_pending_name"] = (
                    state.get("harmony_pending_name", "") + chunk_pre
                )
            else:
                out.append(chunk_pre)

        end = text.find("|>", start + 2)
        if end < 0:
            state["harmony_carry"] = text[start:]
            break
        token = text[start + 2:end].strip().lower()

        if token == "channel":
            close_thought()
            mode = "channel_name"
            state["harmony_pending_name"] = ""
        elif token == "message":
            name = state.get("harmony_pending_name", "").strip().lower()
            state["harmony_pending_name"] = ""
            if name in _HARMONY_VISIBLE_CHANNELS:
                close_thought()
            else:
                open_thought()
            mode = "in_body"
        elif token in ("end", "return"):
            close_thought()
            mode = "normal"
        # Other tokens (start/system/user/assistant/...) swallowed silently.
        i = end + 2

    state["harmony_mode"] = mode
    return "".join(out)


def _route_content_chunk(console: Console, chunk: str, state: dict,
                          show_thoughts: bool) -> None:
    """Strip harmony control tokens, then split the remaining text on
    <think>/</think> markers and dispatch each piece to the right
    renderer. State is mutated to track whether we're currently inside
    a thought block across chunk boundaries."""
    buf = _strip_harmony_tokens(chunk, state)
    buf = state.get("carry", "") + buf
    state["carry"] = ""
    while buf:
        if state["in_thought"]:
            idx = buf.find(_THINK_CLOSE)
            if idx < 0:
                # Whole buffer is thought; keep a trailing partial-tag tail
                # in carry so a split "</thi" + "nk>" still matches.
                tail = min(len(buf), len(_THINK_CLOSE) - 1)
                _emit_segment(console, buf[:-tail] if tail else buf,
                              in_thought=True, show_thoughts=show_thoughts)
                state["carry"] = buf[-tail:] if tail else ""
                return
            _emit_segment(console, buf[:idx], in_thought=True,
                          show_thoughts=show_thoughts)
            buf = buf[idx + len(_THINK_CLOSE):]
            state["in_thought"] = False
        else:
            idx = buf.find(_THINK_OPEN)
            if idx < 0:
                tail = min(len(buf), len(_THINK_OPEN) - 1)
                _emit_segment(console, buf[:-tail] if tail else buf,
                              in_thought=False, show_thoughts=show_thoughts)
                state["carry"] = buf[-tail:] if tail else ""
                return
            _emit_segment(console, buf[:idx], in_thought=False,
                          show_thoughts=show_thoughts)
            buf = buf[idx + len(_THINK_OPEN):]
            state["in_thought"] = True


def _flush_content_state(console: Console, state: dict,
                         show_thoughts: bool) -> bool:
    """Emit text retained only to recognize a control tag across chunks.

    The streaming parser keeps up to six trailing characters in ``carry`` so
    that a later chunk can complete ``<think>`` or ``</think>``. Once the
    server has ended the stream, no later chunk can arrive and that ambiguity
    is resolved: the retained characters are ordinary response text and must
    be emitted. The same applies to an incomplete Harmony marker outside a
    channel-name header.

    Returns True when something was rendered visibly, allowing the caller to
    preserve its trailing-newline behavior.
    """
    pending = state.get("carry", "")
    state["carry"] = ""

    harmony_carry = state.get("harmony_carry", "")
    state["harmony_carry"] = ""
    if state.get("harmony_mode", "normal") != "channel_name":
        pending += harmony_carry

    if not pending:
        return False
    in_thought = bool(state.get("in_thought"))
    _emit_segment(console, pending, in_thought=in_thought,
                  show_thoughts=show_thoughts)
    return not in_thought or show_thoughts


def _resolve_max_tokens(model: ModelInfo, messages: list[dict],
                        setting: Optional[int],
                        tools: Optional[list[dict]] = None) -> int:
    """Compute the max_tokens value to send.

    setting=None means 'auto': use most of the remaining safe working window
    (after subtracting an estimate of the prompt and tool schemas), capped at
    4096 so huge-context models do not ask for impractically long replies.
    setting>0 is a user cap, still bounded by the available headroom.

    The estimate is char/4 (same heuristic as ContextTracker); imprecise
    but consistent. We leave a 15% margin for tokeniser slop."""
    AUTO_FLOOR = 512
    AUTO_CEILING = 4096
    window = safe_working_window(model.context_window)
    prompt_estimate = int(estimate_messages(messages) + estimate_tools(tools))
    headroom = window - prompt_estimate
    budget = int(headroom * 0.85)
    # Never advertise an output budget larger than the estimated remaining
    # window. Explicit settings are caps, not instructions to overflow.
    if setting is not None and setting > 0:
        return max(32, min(setting, max(32, budget)))
    if budget < AUTO_FLOOR:
        budget = max(32, budget)
    if budget > AUTO_CEILING:
        budget = AUTO_CEILING
    return budget


async def _stream_one_completion(client: httpx.Client, model: ModelInfo,
                                  messages: list[dict], console: Console,
                                  show_thoughts: bool = False,
                                  max_tokens: Optional[int] = None,
                                  reasoning_effort: Optional[str] = None,
                                  tool_subset: Optional[list[str]] = None,
                                  ) -> tuple[str, list[dict]]:
    """Send one chat-completion request, stream content tokens to the
    console, accumulate any tool calls. Returns (content_text, [tool_calls]).
    Raises KeyboardInterrupt back to the caller if the user hits Ctrl+C
    during the stream (caller wipes the spinner and resumes the REPL).

    `reasoning_effort` is one of "low"/"medium"/"high"/"none", or None to
    omit the field entirely (server picks its own default). When set, we
    send it under both `reasoning_effort` (OpenAI shape) and
    `reasoning.effort` (llama.cpp / some compat servers) so it lands on
    whichever the backend understands; servers ignore unknown fields."""
    tool_schemas = schemas_for_model(tool_subset)
    payload: dict = {
        "model": model.alias,
        "messages": messages,
        "tools": tool_schemas,
        "tool_choice": "auto",
        "stream": True,
        "temperature": 0.5,   # lower than chat - we want decisive tool use
        "max_tokens": _resolve_max_tokens(
            model, messages, max_tokens, tools=tool_schemas,
        ),
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
        payload["reasoning"] = {"effort": reasoning_effort}
    full_content = ""
    accum_tools: dict[int, dict] = {}
    finish_reason = None
    think_state = {
        "in_thought": False,
        "carry": "",
        "harmony_carry": "",
        "harmony_mode": "normal",
        "harmony_pending_name": "",
        "harmony_in_thought_body": False,
    }

    # Spinner shown while waiting for the model's first byte. Stopped on
    # visible content or tool deltas, but kept alive (with a different
    # label) for reasoning-only streams so an all-reasoning turn doesn't
    # look like a hang followed by silence. Plain-stdout spinner so it
    # plays nicely with prompt_toolkit's patch_stdout().
    status = Spinner("lilly is thinking...")
    status.start()
    spinner_active = True
    spinner_mode = "thinking"  # 'thinking' | 'reasoning' | 'stopped'
    interrupted = False
    printed_any = False
    saw_event = False

    try:
        with client.stream("POST", "/chat/completions",
                           json=payload, timeout=None) as resp:
            if resp.status_code >= 400:
                body = resp.read().decode("utf-8", "replace").strip()
                detail = body[:2000] or resp.reason_phrase
                raise ModelRequestError(
                    f"model server returned HTTP {resp.status_code}: {detail}",
                    status_code=resp.status_code,
                )
            for raw in resp.iter_lines():
                if not raw or not raw.startswith("data: "):
                    continue
                body = raw[6:]
                if body == "[DONE]":
                    saw_event = True
                    break
                try:
                    d = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if d.get("error"):
                    error = d["error"]
                    if isinstance(error, dict):
                        error = error.get("message") or json.dumps(error)
                    raise ModelRequestError(str(error))
                choices = d.get("choices") or []
                if not choices:
                    continue
                saw_event = True
                delta = choices[0].get("delta", {})
                fr = choices[0].get("finish_reason")
                if fr:
                    finish_reason = fr
                content = delta.get("content")
                # Some servers expose chain-of-thought as a separate field
                # rather than inline <think> tags. Treat it as a thought
                # segment - dim, hidden unless /thoughts is on.
                reasoning = (delta.get("reasoning_content")
                             or delta.get("reasoning"))
                has_tool_delta = bool(delta.get("tool_calls"))
                # Visible deltas (content, tool calls) stop the spinner so
                # streamed text doesn't fight it. Reasoning-only deltas
                # swap the spinner label to "reasoning" instead, so the
                # user sees that something is happening even if /thoughts
                # is off and the model spends the whole budget thinking.
                if (content or has_tool_delta) and spinner_active:
                    status.stop()
                    spinner_active = False
                    spinner_mode = "stopped"
                elif reasoning and spinner_active and spinner_mode == "thinking":
                    status.stop()
                    status = Spinner("lilly is reasoning...")
                    status.start()
                    spinner_mode = "reasoning"
                if reasoning:
                    if show_thoughts:
                        printed_any = True
                    _emit_segment(console, reasoning, in_thought=True,
                                  show_thoughts=show_thoughts)
                if content:
                    full_content += content
                    # Only count visible content for the trailing newline.
                    # If the chunk is purely thought, we skip newlining.
                    if not (think_state["in_thought"]
                            and "<think>" not in content
                            and "</think>" not in content
                            and not show_thoughts):
                        printed_any = True
                    _route_content_chunk(console, content, think_state,
                                          show_thoughts)
                _parse_tool_calls_from_chunk(d, accum_tools)
        if _flush_content_state(console, think_state, show_thoughts):
            printed_any = True
    except KeyboardInterrupt:
        interrupted = True
        if spinner_active:
            status.stop()
            spinner_active = False
        console.print("\n[yellow]   ⚠ interrupted[/yellow]")
        # Re-raise so run_turn can stop iterating and the REPL returns
        # to the prompt.
        raise
    except ModelRequestError:
        if spinner_active:
            status.stop()
            spinner_active = False
        raise
    except httpx.HTTPError as e:
        if spinner_active:
            status.stop()
            spinner_active = False
        raise ModelRequestError(f"model server connection failed: {e}") from e
    finally:
        if spinner_active:
            status.stop()
        # Always end on a fresh line after streamed output so the next
        # prompt or tool announcement starts cleanly. Avoid the double
        # newline when nothing was printed (eg. tool-only response).
        if printed_any and not interrupted:
            console.print()
    if not saw_event:
        raise ModelRequestError(
            "model server returned an empty or non-streaming response"
        )
    if full_content:
        console.print()  # newline after streamed content
    if finish_reason == "length" and not interrupted and not accum_tools:
        # Server stopped because we hit max_tokens. If the entire budget
        # was eaten by hidden reasoning, full_content is empty and the
        # turn looks silent; either way the user deserves to know.
        hint = ("budget consumed by hidden reasoning; "
                "try /reasoning low or /thoughts on to see it"
                if not full_content
                else "raise /max-tokens, or lower /reasoning")
        console.print(f"[yellow]   ⚠ cut off at max_tokens ({hint})[/yellow]")

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


def _short_args(args: dict) -> str:
    """One-line representation of a tool's args for the announce line."""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        if isinstance(v, str):
            shown = v if len(v) <= 40 else v[:37] + "..."
            parts.append(f"{k}={shown!r}")
        elif isinstance(v, (int, float, bool)) or v is None:
            parts.append(f"{k}={v}")
        else:
            parts.append(f"{k}=<{type(v).__name__}>")
    return ", ".join(parts)


@contextlib.contextmanager
def _in_workdir(workdir: Path):
    """Make the process CWD equal `workdir` for the duration of the turn.

    The file tools (write_file/read_file/...) and the path-scope safety
    check both resolve relative paths via Path(path).resolve(), which is
    relative to the process CWD. The REPL happens to launch with CWD ==
    workdir, so it works there by accident. Any embedding caller (e.g. an
    orchestrator) or a headless run where CWD != workdir would otherwise
    have every relative path resolve against the wrong base and get blocked
    as 'outside workspace'. Anchoring CWD to workdir makes the tools and the
    safety check agree, and makes the `workdir` argument actually mean
    something. Best-effort: if workdir doesn't exist we run as-is."""
    prev = os.getcwd()
    try:
        target = Path(workdir).expanduser().resolve()
        if target.is_dir():
            os.chdir(target)
        yield
    finally:
        try:
            os.chdir(prev)
        except OSError:
            pass


NO_ACTION_NUDGE = (
    "You described what you would do but did not call any tool. "
    "Do it now by CALLING the appropriate tool(s); do not just describe the "
    "action. If the task is already fully complete, say exactly: DONE."
)


def run_turn(client: httpx.Client, model: ModelInfo,
             messages: list[dict], console: Console,
             bypass_perms: bool, workdir: Path,
             show_thoughts: bool = False,
             max_tokens: Optional[int] = None,
             reasoning_effort: Optional[str] = None,
             nudge_on_no_action: bool = True,
             max_nudges: int = 1,
             tool_subset: Optional[list[str]] = None,
             bash_deny: Optional[list[str]] = None,
             before_completion: Optional[Callable[[], None]] = None) -> None:
    """Single user turn: may involve multiple model + tool iterations.
    Mutates `messages` in place. Raises KeyboardInterrupt back to the
    caller if the user hits Ctrl+C during the turn.

    `console` may be a rich Console (the REPL) or any Sink; it is coerced so
    the structured on_* hooks are always available for embedders.

    `nudge_on_no_action`: when a turn ends with assistant content but zero tool
    calls (the model narrated an action instead of performing it, a common
    weak-local-model failure), re-prompt imperatively up to `max_nudges` times.
    Disabled automatically once the model signals completion (says DONE or the
    content reads like a final answer with no pending action).

    `bash_deny` (optional) is a list of regex strings; a bash command matching
    any is refused with a policy error. Used by embedders to forbid e.g.
    package installs in an autonomous run. The standalone REPL passes None."""
    import asyncio

    sink = _as_sink(console)
    deny_patterns = [re.compile(p) for p in (bash_deny or [])]

    async def _go():
        nudges_used = 0
        repeat_guard = RepeatGuard()
        for _ in range(MAX_TOOL_ITERATIONS):
            if before_completion is not None:
                before_completion()
            content, tool_calls = await _stream_one_completion(
                client, model, messages, console,
                show_thoughts=show_thoughts,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                tool_subset=tool_subset,
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
                # The model produced no tool call. Either it is genuinely done,
                # or it narrated an action without performing it. If we still
                # have nudges left and the content does not look final, push it
                # to actually act. This recovers the ~30% narrate-instead-of-act
                # failure measured on harder local-model tasks.
                if (nudge_on_no_action and nudges_used < max_nudges
                        and not _looks_complete(content)):
                    nudges_used += 1
                    sink.print("[yellow]   ↻ no tool call; nudging to act[/yellow]")
                    messages.append({"role": "user", "content": NO_ACTION_NUDGE})
                    continue
                sink.on_turn_end(stopped_reason=None)
                return  # done

            # Execute each tool call in order.
            for tc in tool_calls:
                name = tc["function"]["name"]
                args = tc["_parsed_args"]
                # Announce *before* dispatch so the user has something on
                # screen while the tool runs (write_file/bash can take a
                # while; before this line, the REPL looked frozen). The
                # arg summary is user/model controlled and may contain
                # brackets, so emit it as a separate plain segment.
                console.print(f"[cyan]   ⏳ {name}[/cyan]", end="")
                if args:
                    console.print(f" ({_short_args(args)})",
                                  markup=False, highlight=False, style="grey50")
                else:
                    console.print()
                sink.on_tool_call(name, args)
                _tool = by_name(name)
                if _tool is not None and repeat_guard.is_repeat(
                        name, args, _tool.mutating):
                    result = repeat_guard.stub_result()
                    console.print(f"[yellow]   ↺ {name} (identical repeat suppressed)[/yellow]")
                    sink.on_tool_result(name, True, result)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    continue
                ok, reason = _gate_tool_call(console, name, args,
                                              bypass_perms, workdir,
                                              bash_deny=deny_patterns)
                if not ok:
                    result = {"ok": False, "error": reason}
                    console.print(f"[yellow]   ⚠ {reason}[/yellow]")
                else:
                    tool = by_name(name)
                    with Spinner(f"running {name}..."):
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
                sink.on_tool_result(name, bool(result.get("ok")), result)
                context_result = result
                try:
                    serialized = json.dumps(result, ensure_ascii=False)
                except (TypeError, ValueError):
                    serialized = str(result)
                if len(serialized) > 12000:
                    context_result = bound_json_value(result, 11600)
                    if isinstance(context_result, dict):
                        context_result["_context_truncated"] = True
                        context_result["_context_note"] = (
                            "full result was delivered to the event sink but "
                            "bounded in model context"
                        )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(context_result, ensure_ascii=False),
                })
            # Loop back: model gets to read tool results and decide next.
        console.print("[yellow]   ⚠ hit max tool iterations, stopping[/yellow]")
        sink.on_turn_end(stopped_reason="max_iterations")

    try:
        with _in_workdir(workdir):
            asyncio.run(_go())
    except KeyboardInterrupt:
        # Append a synthetic assistant note so the conversation stays
        # well-formed even though the model's own message was cut short.
        messages.append({
            "role": "assistant",
            "content": "(interrupted by user)",
        })
        raise
