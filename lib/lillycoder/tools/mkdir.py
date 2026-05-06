"""mkdir tool — create directory (parents as needed)."""
from __future__ import annotations

from pathlib import Path

from .registry import Tool, register


def _handler(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if p.exists():
        return {"ok": True, "path": str(p), "already_existed": True}
    try:
        p.mkdir(parents=True)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "path": str(p), "already_existed": False}


register(Tool(
    name="mkdir",
    description="Create a directory (and any missing parents).",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    handler=_handler,
    mutating=True,
    danger="low",
))
