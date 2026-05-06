"""write_file tool — create/overwrite a file. Diff preview gated."""
from __future__ import annotations

import difflib
from pathlib import Path

from .registry import Tool, register


def _handler(path: str, content: str) -> dict:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    old = p.read_text() if existed else ""
    diff = "\n".join(difflib.unified_diff(
        old.splitlines(), content.splitlines(),
        fromfile=str(p) + " (was)", tofile=str(p) + " (now)",
        lineterm="",
    ))
    p.write_text(content)
    return {
        "ok": True,
        "path": str(p),
        "bytes": len(content.encode("utf-8")),
        "existed": existed,
        "diff": diff or "(new file)",
    }


register(Tool(
    name="write_file",
    description="Create or overwrite a file with the given content.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
    handler=_handler,
    mutating=True,
    danger="medium",
))
