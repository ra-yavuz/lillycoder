"""Output sink: decouples the agent loop from rich.Console.

The agent loop historically wrote everything to a rich Console. That is fine
for the interactive REPL, but it makes lillycoder hard to embed: a headless
one-shot run wants quiet/recording output, and an orchestrator driving many
workers wants STRUCTURED events (tool started, tool finished, token streamed)
rather than pre-rendered terminal text it would have to parse back.

Design (kept deliberately small):

  - A sink exposes `print(*args, **kwargs)` with the SAME signature the loop
    already used, so existing call sites are unchanged and the REPL behaves
    byte-for-byte as before (its sink IS a rich Console via ConsoleSink).
  - A sink also exposes structured hooks at the few semantic points an
    embedder cares about: on_token, on_tool_call, on_tool_result, on_turn_end.
    On ConsoleSink these are no-ops (the human already sees the rich output).
    An orchestrator subclasses/implements them to forward events to a queue.

This is intentionally NOT a full event system. It is the minimum surface that
lets the same agent loop serve the terminal, a headless run, and an embedder
without any of them parsing each other's presentation. Standalone lillycoder
benefits too: the headless `--prompt` mode uses a RecordingSink.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

_MARKUP_RE = re.compile(r"\[/?[a-zA-Z][a-zA-Z0-9 _#]*\]")


def _plain(obj: Any) -> str:
    """Best-effort plain-text rendering for the RecordingSink: strip rich
    console markup tags, and render rich renderables (Panel, Syntax, ...) to
    their visible text via a throwaway capture console rather than str()."""
    cls = type(obj).__module__ or ""
    if cls.startswith("rich.") and not isinstance(obj, str):
        try:
            from rich.console import Console as _C
            import io as _io
            cap = _C(file=_io.StringIO(), force_terminal=False, no_color=True, width=100)
            cap.print(obj)
            return cap.file.getvalue().rstrip("\n")
        except Exception:
            return str(obj)
    return _MARKUP_RE.sub("", str(obj))


@runtime_checkable
class Sink(Protocol):
    """Structural type the agent loop writes to. A rich.Console already
    satisfies `print`; the on_* hooks are additive."""

    def print(self, *args: Any, **kwargs: Any) -> None: ...

    def on_token(self, text: str, is_thought: bool = False) -> None: ...

    def on_tool_call(self, name: str, args: Mapping[str, Any]) -> None: ...

    def on_tool_result(self, name: str, ok: bool, result: Any) -> None: ...

    def on_turn_end(self, *, stopped_reason: Optional[str] = None) -> None: ...


class _NoOpHooks:
    """Mixin: default structured hooks do nothing. Presentation-only sinks
    (the terminal) inherit these because the human already sees print output."""

    def on_token(self, text: str, is_thought: bool = False) -> None:
        pass

    def on_tool_call(self, name: str, args: Mapping[str, Any]) -> None:
        pass

    def on_tool_result(self, name: str, ok: bool, result: Any) -> None:
        pass

    def on_turn_end(self, *, stopped_reason: Optional[str] = None) -> None:
        pass


class ConsoleSink(_NoOpHooks):
    """The default sink: delegates print() straight to a rich Console, so the
    interactive REPL is unchanged. Structured hooks are no-ops (the rich output
    is the human-facing channel)."""

    def __init__(self, console: Any) -> None:
        self._console = console

    def print(self, *args: Any, **kwargs: Any) -> None:
        self._console.print(*args, **kwargs)

    @property
    def console(self) -> Any:
        return self._console


class RecordingSink(_NoOpHooks):
    """Quiet sink for headless runs and tests: captures plain text instead of
    rendering it, and records the structured events. No rich dependency, no
    terminal control codes. `text` is the concatenation of streamed tokens;
    `lines` is the human-readable print log; `events` is the structured log."""

    def __init__(self, echo: bool = False) -> None:
        self.echo = echo
        self.lines: list[str] = []
        self.text_parts: list[str] = []
        self.events: list[dict] = []

    def print(self, *args: Any, **kwargs: Any) -> None:
        # Render args to PLAIN text: strip rich markup tags like [cyan]...[/cyan]
        # and render Panel/other rich objects to their visible text, so a
        # headless/recording sink never leaks terminal markup or object reprs.
        end = kwargs.get("end", "\n")
        msg = " ".join(_plain(a) for a in args)
        self.lines.append(msg)
        if self.echo:
            import sys
            sys.stdout.write(msg + ("" if end == "" else end))
            sys.stdout.flush()

    # Structured hooks: record them.
    def on_token(self, text: str, is_thought: bool = False) -> None:
        if not is_thought:
            self.text_parts.append(text)
        self.events.append({"kind": "token", "is_thought": is_thought, "text": text})

    def on_tool_call(self, name: str, args: Mapping[str, Any]) -> None:
        self.events.append({"kind": "tool_call", "name": name, "args": dict(args)})

    def on_tool_result(self, name: str, ok: bool, result: Any) -> None:
        self.events.append({"kind": "tool_result", "name": name, "ok": ok, "result": result})

    def on_turn_end(self, *, stopped_reason: Optional[str] = None) -> None:
        self.events.append({"kind": "turn_end", "stopped_reason": stopped_reason})

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


def as_sink(obj: Any) -> Sink:
    """Coerce a rich Console (or anything print-compatible) into a Sink.
    If it already implements the on_* hooks, return as-is; otherwise wrap in
    ConsoleSink so the hooks exist as no-ops. This keeps every existing caller
    that passes a bare Console working."""
    if isinstance(obj, _NoOpHooks):
        return obj  # already a sink
    if hasattr(obj, "on_token") and hasattr(obj, "on_tool_call"):
        return obj
    return ConsoleSink(obj)
