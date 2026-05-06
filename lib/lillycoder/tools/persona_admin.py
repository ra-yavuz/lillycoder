"""Persona-admin tools.

Lets Lilly manage her own roster of personalities through real tool
calls instead of improvising with write_file. Each tool here is a thin
shell around config.add_persona / clone_persona / list_personas, plus
two REPL hooks (load + evolve) that the REPL registers at startup so
the model can switch the active persona and toggle evolve from inside
a turn.

Without the hooks installed, the load/evolve tools fail closed; the
read-only operations (list, show) work standalone."""
from __future__ import annotations

from typing import Callable, Optional

from .. import config as _config
from .registry import Tool, register


# REPL registers these at startup. Signatures match the REPL's existing
# in-memory behaviour for /personalities load and /persona-evolve.
_LOAD_HOOK: Optional[Callable[[str], dict]] = None
_EVOLVE_HOOK: Optional[Callable[[bool], dict]] = None


def set_load_hook(hook: Callable[[str], dict]) -> None:
    global _LOAD_HOOK
    _LOAD_HOOK = hook


def set_evolve_hook(hook: Callable[[bool], dict]) -> None:
    global _EVOLVE_HOOK
    _EVOLVE_HOOK = hook


# --- list_personas --------------------------------------------------------

def _list_handler() -> dict:
    out = []
    for name in _config.list_personas():
        out.append({
            "name": name,
            "origin": _config.persona_origin(name),
        })
    return {"ok": True, "personas": out}


register(Tool(
    name="list_personas",
    description=(
        "List every available persona Lilly can switch to (bundled "
        "and user-created), with their origin. Use this before "
        "creating a new persona to avoid name collisions, or to "
        "answer the user when they ask which personalities exist."
    ),
    parameters={"type": "object", "properties": {}},
    handler=_list_handler,
    mutating=False,
    danger="low",
))


# --- add_persona ----------------------------------------------------------

def _add_handler(name: str, text: str, overwrite: bool = False) -> dict:
    try:
        path = _config.add_persona(name, text, overwrite=overwrite)
    except FileExistsError as e:
        return {"ok": False, "error": f"persona already exists: {e}. "
                                       "pass overwrite=true to replace."}
    except (ValueError, OSError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "saved_to": str(path), "name": name}


register(Tool(
    name="add_persona",
    description=(
        "Create a new user-owned persona by name. The text is the full "
        "system-prompt body that Lilly would use under that persona. "
        "Pass overwrite=true to replace an existing user persona of "
        "the same name. Bundled personas of the same name are not "
        "modified; the user file shadows them. After creating, "
        "consider calling set_active_persona to switch to it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Persona name. Becomes <name>.md on disk. No "
                    "slashes or '..'. Lowercase recommended."
                ),
            },
            "text": {
                "type": "string",
                "description": (
                    "Full replacement system-prompt text for the new "
                    "persona."
                ),
            },
            "overwrite": {
                "type": "boolean",
                "description": "Replace an existing user persona of "
                               "the same name. Default false.",
            },
        },
        "required": ["name", "text"],
    },
    handler=_add_handler,
    mutating=True,
    danger="medium",
))


# --- clone_persona --------------------------------------------------------

def _clone_handler(src: str, dst: str, overwrite: bool = False) -> dict:
    try:
        path = _config.clone_persona(src, dst, overwrite=overwrite)
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    except FileExistsError as e:
        return {"ok": False, "error": f"persona already exists: {e}. "
                                       "pass overwrite=true to replace."}
    except (ValueError, OSError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "saved_to": str(path), "src": src, "dst": dst}


register(Tool(
    name="clone_persona",
    description=(
        "Copy an existing persona (bundled or user) into a new "
        "user-owned persona under a different name. Useful as the "
        "first step of forking a bundled persona for editing."
    ),
    parameters={
        "type": "object",
        "properties": {
            "src": {"type": "string",
                    "description": "Source persona name."},
            "dst": {"type": "string",
                    "description": "New persona name."},
            "overwrite": {"type": "boolean",
                          "description": "Replace dst if it already "
                                         "exists. Default false."},
        },
        "required": ["src", "dst"],
    },
    handler=_clone_handler,
    mutating=True,
    danger="medium",
))


# --- set_active_persona ---------------------------------------------------

def _set_active_handler(name: str) -> dict:
    if _LOAD_HOOK is None:
        return {"ok": False,
                "error": "set_active_persona is only available inside "
                         "the REPL"}
    if name not in _config.list_personas():
        return {"ok": False, "error": f"no such persona: {name!r}"}
    return _LOAD_HOOK(name)


register(Tool(
    name="set_active_persona",
    description=(
        "Switch the currently active persona by name. Use this after "
        "add_persona or clone_persona to actually load the new "
        "persona for the rest of the session. The active persona is "
        "remembered across runs."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string",
                     "description": "Persona name to load."},
        },
        "required": ["name"],
    },
    handler=_set_active_handler,
    mutating=True,
    danger="high",
))


# --- set_evolve -----------------------------------------------------------

def _set_evolve_handler(enabled: bool) -> dict:
    if _EVOLVE_HOOK is None:
        return {"ok": False,
                "error": "set_evolve is only available inside the REPL"}
    return _EVOLVE_HOOK(bool(enabled))


register(Tool(
    name="set_evolve",
    description=(
        "Enable or disable persona-evolve. When enabling, the "
        "currently active in-memory persona is snapshotted to disk "
        "and the active label is updated to that file, so future "
        "set_persona calls keep refining the same file. Disabling "
        "stops auto-saves but does not change the active persona."
    ),
    parameters={
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean",
                        "description": "true to enable, false to "
                                       "disable."},
        },
        "required": ["enabled"],
    },
    handler=_set_evolve_handler,
    mutating=True,
    danger="medium",
))
