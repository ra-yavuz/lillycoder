"""edit_file tool — string-replace edit with diff preview."""
from __future__ import annotations

import difflib
from pathlib import Path

from .registry import Tool, register


def _handler(path: str, old_str: str, new_str: str, count: int = 1) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return {"ok": False, "error": f"no such file: {path}"}
    text = p.read_text()
    occurrences = text.count(old_str)
    if occurrences == 0:
        return {"ok": False, "error": "old_str not found in file"}
    if count != -1 and occurrences > count:
        return {
            "ok": False,
            "error": f"old_str matches {occurrences} times; specify count=-1 to replace all, "
                     f"or include more context to make it unique",
        }
    new = text.replace(old_str, new_str) if count == -1 else text.replace(old_str, new_str, count)
    diff = "\n".join(difflib.unified_diff(
        text.splitlines(), new.splitlines(),
        fromfile=str(p), tofile=str(p), lineterm="",
    ))
    p.write_text(new)
    return {
        "ok": True,
        "path": str(p),
        "replaced": occurrences if count == -1 else min(occurrences, count),
        "diff": diff,
    }


register(Tool(
    name="edit_file",
    description="Replace exact substring(s) in a file. Default replaces 1 occurrence; pass count=-1 to replace all.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_str": {"type": "string"},
            "new_str": {"type": "string"},
            "count": {"type": "integer", "default": 1, "description": "max replacements; -1 = all"},
        },
        "required": ["path", "old_str", "new_str"],
    },
    handler=_handler,
    mutating=True,
    danger="medium",
))
