"""find tool — locate files by glob pattern."""
from __future__ import annotations

import fnmatch
from pathlib import Path

from .registry import Tool, register


def _handler(pattern: str, path: str = ".", max_results: int = 200) -> dict:
    base = Path(path).expanduser().resolve()
    if not base.exists():
        return {"ok": False, "error": f"no such path: {path}"}
    hits = []
    for p in base.rglob("*"):
        if len(hits) >= max_results:
            break
        if fnmatch.fnmatch(p.name, pattern):
            hits.append(str(p.relative_to(base)))
    return {
        "ok": True,
        "pattern": pattern,
        "results": hits,
        "count": len(hits),
        "truncated": len(hits) >= max_results,
    }


register(Tool(
    name="find",
    description="Find files matching a glob pattern (e.g. '*.py', 'test_*.js'). Recursive.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob pattern"},
            "path": {"type": "string", "default": "."},
            "max_results": {"type": "integer", "default": 200},
        },
        "required": ["pattern"],
    },
    handler=_handler,
    mutating=False,
))
