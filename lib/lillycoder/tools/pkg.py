"""pkg_install tool — gated package installation.

Always prompts (independent of bash gating) so installing libraries is
intentional. Supports npm, pip, cargo, apt (apt requires sudo and will
be refused by safety.py — kept in registry so the user gets a clean
error message instead of the model going off-script).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .registry import Tool, register


_MANAGERS = {
    "npm":   ["npm", "install"],
    "yarn":  ["yarn", "add"],
    "pip":   ["pip", "install"],
    "pip3":  ["pip3", "install"],
    "cargo": ["cargo", "add"],
    "apt":   ["apt", "install", "-y"],   # will be blocked by safety (no sudo)
    "brew":  ["brew", "install"],
}


def _handler(packages: list[str], manager: str = "npm",
             cwd: str = ".", flags: list[str] | None = None) -> dict:
    if manager not in _MANAGERS:
        return {"ok": False, "error": f"unknown manager '{manager}'; "
                                       f"supported: {', '.join(_MANAGERS)}"}
    if not packages:
        return {"ok": False, "error": "packages list is empty"}
    cmd = _MANAGERS[manager] + (flags or []) + list(packages)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=str(Path(cwd).expanduser().resolve()),
                           timeout=600)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "package install timed out after 10 minutes"}
    return {
        "ok": r.returncode == 0,
        "manager": manager,
        "packages": packages,
        "exit_code": r.returncode,
        "stdout": r.stdout[-4000:],
        "stderr": r.stderr[-2000:],
    }


register(Tool(
    name="pkg_install",
    description="Install packages with a known package manager (npm/yarn/pip/cargo/brew). "
                "apt is recognised but will be refused (no sudo).",
    parameters={
        "type": "object",
        "properties": {
            "packages": {"type": "array", "items": {"type": "string"}},
            "manager": {"type": "string", "enum": list(_MANAGERS.keys()), "default": "npm"},
            "cwd": {"type": "string", "default": "."},
            "flags": {"type": "array", "items": {"type": "string"}, "default": []},
        },
        "required": ["packages"],
    },
    handler=_handler,
    mutating=True,
    danger="high",
))
