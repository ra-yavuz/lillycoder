"""set_persona tool. Lets the model rewrite lilly's system prompt at the
user's request. The actual application of the new prompt (and optional
persistence to disk) happens in the REPL, which registers a hook here at
startup. The tool itself just validates and forwards."""
from __future__ import annotations

from typing import Callable, Optional

from .registry import Tool, register


# REPL registers a callable here at startup. Signature:
#   hook(text: str) -> dict   # returns {ok, scope, persona, ...}
# Until set, calls fail closed - the tool isn't usable on its own.
_HOOK: Optional[Callable[[str], dict]] = None


def set_hook(hook: Callable[[str], dict]) -> None:
    global _HOOK
    _HOOK = hook


def _handler(text: str) -> dict:
    if _HOOK is None:
        return {"ok": False, "error": "set_persona is only available inside the REPL"}
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty persona text"}
    if len(text) > 16000:
        return {"ok": False, "error": "persona text too long (>16000 chars)"}
    return _HOOK(text)


register(Tool(
    name="set_persona",
    description=(
        "Rewrite lilly's own system prompt (her persona). The new text "
        "replaces the system message for the current session. If the user "
        "has enabled persona-evolve, it is also saved to disk so it "
        "survives restarts; otherwise it is session-only. Use this only "
        "when the user explicitly asks you to change how you behave."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The full replacement persona text.",
            },
        },
        "required": ["text"],
    },
    handler=_handler,
    mutating=True,
    danger="high",
))
