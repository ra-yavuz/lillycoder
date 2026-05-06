"""read_file tool — read a file from disk and return its content."""
from __future__ import annotations

from pathlib import Path

from .registry import Tool, register


def _handler(path: str, max_bytes: int = 200_000) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return {"ok": False, "error": f"no such file: {path}"}
    if p.is_dir():
        return {"ok": False, "error": f"is a directory, use list_dir: {path}"}
    try:
        data = p.read_bytes()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": f"binary file ({len(data)} bytes)"}
    return {
        "ok": True,
        "path": str(p),
        "content": text,
        "truncated": truncated,
        "bytes": len(data),
    }


register(Tool(
    name="read_file",
    description="Read a text file's contents. Returns UTF-8 string. Up to 200KB by default.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "absolute or relative file path"},
            "max_bytes": {"type": "integer", "description": "cap on bytes read", "default": 200000},
        },
        "required": ["path"],
    },
    handler=_handler,
    mutating=False,
))
