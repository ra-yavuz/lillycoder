"""read_file tool, read a file from disk and return its content.

Reads are line-windowed and capped small by default. The cap exists for the
16K-context local models this tool serves: an earlier 200KB default let an
agent pull a 4,500-line file into its window in one call, which destroyed the
turn before it could act. A truncated result says exactly how to proceed
(grep to locate, then re-read with start_line), so a weak model can recover
instead of stalling.
"""
from __future__ import annotations

from pathlib import Path

from .registry import Tool, register

# ~2K tokens at the chars/4 heuristic: a sane single bite of a 16K window
# that still leaves room for the conversation, a grep, and an edit.
DEFAULT_MAX_BYTES = 8_000
DEFAULT_MAX_LINES = 200


def _handler(path: str, start_line: int = 1, max_lines: int = DEFAULT_MAX_LINES,
             max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return {"ok": False, "error": f"no such file: {path}"}
    if p.is_dir():
        return {"ok": False, "error": f"is a directory, use list_dir: {path}"}
    try:
        data = p.read_bytes()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": f"binary file ({len(data)} bytes)"}

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    if start_line < 1:
        start_line = 1
    window = lines[start_line - 1:start_line - 1 + max_lines]
    content = "".join(window)
    line_truncated = (start_line - 1 + len(window)) < total_lines or start_line > 1

    byte_truncated = len(content.encode("utf-8")) > max_bytes
    if byte_truncated:
        content = content.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")

    truncated = line_truncated or byte_truncated
    end_line = start_line - 1 + content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    result = {
        "ok": True,
        "path": str(p),
        "content": content,
        "truncated": truncated,
        "total_lines": total_lines,
        "showing": f"lines {start_line}-{end_line} of {total_lines}",
    }
    if truncated:
        result["hint"] = (
            f"PARTIAL VIEW: this file has {total_lines} lines; you received "
            f"lines {start_line}-{end_line}. Do NOT try to read the whole "
            f"file. Use grep to find the function or text you need, then "
            f"call read_file again with start_line=<match line> to view just "
            f"that region."
        )
    return result


register(Tool(
    name="read_file",
    description=("Read a text file. Returns a line window (default: first "
                 f"{DEFAULT_MAX_LINES} lines, max {DEFAULT_MAX_BYTES} bytes). "
                 "For large files: grep first to find the right line, then "
                 "pass start_line to read that region."),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "absolute or relative file path"},
            "start_line": {"type": "integer",
                           "description": "1-based first line to return (default 1)"},
            "max_lines": {"type": "integer",
                          "description": f"lines to return (default {DEFAULT_MAX_LINES})"},
            "max_bytes": {"type": "integer",
                          "description": f"hard cap on returned bytes (default {DEFAULT_MAX_BYTES})"},
        },
        "required": ["path"],
    },
    handler=_handler,
    mutating=False,
))
