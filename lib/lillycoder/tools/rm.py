"""rm tool — delete a file or directory.

Recursion through directories must be explicitly requested. Even then,
the safety classifier still rejects roots like /, ~, $HOME.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .registry import Tool, register


def _handler(path: str, recursive: bool = False) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return {"ok": False, "error": f"no such path: {path}"}
    try:
        if p.is_dir():
            if not recursive:
                return {"ok": False, "error": f"is a directory; pass recursive=true"}
            shutil.rmtree(p)
        else:
            p.unlink()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "path": str(p), "removed": True}


register(Tool(
    name="rm",
    description="Delete a file. For directories, set recursive=true.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "recursive": {"type": "boolean", "default": False},
        },
        "required": ["path"],
    },
    handler=_handler,
    mutating=True,
    danger="high",
))
