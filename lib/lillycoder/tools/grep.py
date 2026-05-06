"""grep tool — recursive ripgrep over the workspace."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .registry import Tool, register


def _handler(pattern: str, path: str = ".", max_results: int = 100) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return {"ok": False, "error": f"no such path: {path}"}

    # Prefer ripgrep if installed (much faster, respects .gitignore).
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--line-number", "--no-heading", "--color", "never",
               "--max-count", "10", pattern, str(p)]
    else:
        cmd = ["grep", "-rn", pattern, str(p)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "grep timed out after 15s"}
    lines = r.stdout.splitlines()[:max_results]
    return {
        "ok": True,
        "pattern": pattern,
        "matches": lines,
        "count": len(lines),
        "truncated": len(lines) >= max_results,
    }


register(Tool(
    name="grep",
    description="Search for a pattern in files (uses ripgrep if available, else grep). Returns up to 100 matches.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "regex pattern to search for"},
            "path": {"type": "string", "default": "."},
            "max_results": {"type": "integer", "default": 100},
        },
        "required": ["pattern"],
    },
    handler=_handler,
    mutating=False,
))
