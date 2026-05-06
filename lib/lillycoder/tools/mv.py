"""mv tool — rename / move a file or directory."""
from __future__ import annotations

import shutil
from pathlib import Path

from .registry import Tool, register


def _handler(src: str, dst: str) -> dict:
    s = Path(src).expanduser().resolve()
    d = Path(dst).expanduser().resolve()
    if not s.exists():
        return {"ok": False, "error": f"source missing: {src}"}
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "src": str(s), "dst": str(d)}


register(Tool(
    name="mv",
    description="Move or rename a file or directory.",
    parameters={
        "type": "object",
        "properties": {"src": {"type": "string"}, "dst": {"type": "string"}},
        "required": ["src", "dst"],
    },
    handler=_handler,
    mutating=True,
    danger="medium",
))
