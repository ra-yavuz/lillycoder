"""bash tool — run a shell command.

Two modes:
  - safe subset: stdin-free read-only commands (git status, ls, cat,
    pwd, which, --version checks). No permission prompt needed; safety
    classifier still runs.
  - full: any other command. Permission gate + safety classifier both
    run upstream of execution.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from .registry import Tool, register


# Conservative allowlist for `bash_safe`: command must START with one of
# these words, and contain none of: ;, &&, ||, |, >, <, $(), backticks.
_SAFE_PREFIXES = {
    "ls", "pwd", "whoami", "date", "uname", "echo",
    "cat", "head", "tail", "wc", "file", "stat", "tree",
    "git", "node", "python", "python3", "pip", "npm", "yarn",
    "which", "type", "command",
}
_BAD_SHELL = re.compile(r"[;&|<>`$]")


def _is_safe(cmd: str) -> bool:
    if _BAD_SHELL.search(cmd):
        return False
    parts = shlex.split(cmd)
    if not parts:
        return False
    head = parts[0]
    if head not in _SAFE_PREFIXES:
        return False
    # Disallow git push/pull/commit etc — those mutate.
    if head == "git" and len(parts) > 1 and parts[1] in {
        "push", "pull", "commit", "merge", "rebase", "reset", "checkout",
        "branch", "rm", "mv", "add", "stash", "tag", "fetch",
    }:
        return False
    # Disallow `node script.js` actually executing arbitrary code.
    if head in ("node", "python", "python3") and len(parts) > 1 and parts[1] not in (
        "--version", "-v", "-V"
    ):
        return False
    if head in ("npm", "yarn", "pip") and len(parts) > 1 and parts[1] not in (
        "--version", "-v", "list", "ls", "outdated", "info", "view", "show",
    ):
        return False
    return True


def _handler(cmd: str, cwd: str = ".", timeout_s: int = 60) -> dict:
    workdir = Path(cwd).expanduser().resolve()
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            cwd=str(workdir), timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout_s}s"}
    out = r.stdout
    err = r.stderr
    return {
        "ok": r.returncode == 0,
        "exit_code": r.returncode,
        "stdout": out[:8000],
        "stderr": err[:4000],
        "stdout_truncated": len(out) > 8000,
        "cmd": cmd,
    }


# Public registration: ONE tool, but the agent loop knows to skip the
# permission prompt when _is_safe(cmd) is True (see agent.py).
bash_tool = register(Tool(
    name="bash",
    description="Run a shell command. Read-only commands like 'git status' or 'ls' "
                "skip the permission prompt; everything else asks first. "
                "Hard-banned: sudo, rm -rf /, mkfs, fork bombs (always refused).",
    parameters={
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "shell command to run"},
            "cwd": {"type": "string", "default": "."},
            "timeout_s": {"type": "integer", "default": 60},
        },
        "required": ["cmd"],
    },
    handler=_handler,
    mutating=True,           # treated as mutating by default
    safe_subset=True,        # agent will check _is_safe() to maybe skip prompt
    danger="medium",
))


def is_safe_cmd(cmd: str) -> bool:
    return _is_safe(cmd)
