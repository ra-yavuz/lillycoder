"""list_dir tool — list entries in a directory."""
from __future__ import annotations

from pathlib import Path

from .registry import Tool, register


def _handler(path: str = ".", show_hidden: bool = False) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return {"ok": False, "error": f"no such path: {path}"}
    if not p.is_dir():
        return {"ok": False, "error": f"not a directory: {path}"}
    entries = []
    for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if not show_hidden and child.name.startswith("."):
            continue
        try:
            stat = child.stat()
            entries.append({
                "name": child.name,
                "is_dir": child.is_dir(),
                "size": stat.st_size if child.is_file() else None,
            })
        except OSError:
            continue
    return {"ok": True, "path": str(p), "entries": entries, "count": len(entries)}


register(Tool(
    name="list_dir",
    description="List files and subdirectories in a directory.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "default": "."},
            "show_hidden": {"type": "boolean", "default": False},
        },
        "required": [],
    },
    handler=_handler,
    mutating=False,
))
