"""Per-tool permission prompts.

Stored persistently per project at .lilly/permissions.json.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich.console import Console


PERM_FILE_NAME = "permissions.json"


@dataclass
class PermissionState:
    always_tools: list[str]              # tool names auto-approved
    always_paths: dict[str, list[str]]   # tool → [paths] auto-approved

    @staticmethod
    def empty() -> "PermissionState":
        return PermissionState(always_tools=[], always_paths={})

    def to_json(self) -> str:
        return json.dumps({
            "always_tools": self.always_tools,
            "always_paths": self.always_paths,
        }, indent=2)

    @staticmethod
    def from_json(s: str) -> "PermissionState":
        d = json.loads(s)
        return PermissionState(
            always_tools=list(d.get("always_tools", [])),
            always_paths={k: list(v) for k, v in d.get("always_paths", {}).items()},
        )


def _perm_path(workdir: Path) -> Path:
    d = workdir / ".lilly"
    d.mkdir(exist_ok=True)
    return d / PERM_FILE_NAME


def load(workdir: Optional[Path] = None) -> PermissionState:
    p = _perm_path(workdir or Path.cwd())
    if not p.exists():
        return PermissionState.empty()
    try:
        return PermissionState.from_json(p.read_text())
    except Exception:
        return PermissionState.empty()


def save(state: PermissionState, workdir: Optional[Path] = None) -> None:
    p = _perm_path(workdir or Path.cwd())
    p.write_text(state.to_json())


def is_pre_approved(state: PermissionState, tool_name: str,
                    target_path: Optional[str] = None) -> bool:
    if tool_name in state.always_tools:
        return True
    if target_path and target_path in state.always_paths.get(tool_name, []):
        return True
    return False


def ask(console: Console, tool_name: str, summary: str,
        target_path: Optional[str] = None,
        bypass: bool = False, workdir: Optional[Path] = None) -> bool:
    """Prompt the user. Returns True if the call should proceed.

    Updates persistent state when the user answers 'a' or 'p'.
    """
    if bypass:
        return True
    state = load(workdir)
    if is_pre_approved(state, tool_name, target_path):
        return True

    console.print()
    console.print(
        f"[bold yellow]🦊 lilly wants to:[/bold yellow] {tool_name}({summary})"
    )
    options = "[y]es  [n]o  [a]lways for this tool"
    if target_path:
        options += "  [p]ath: always for this exact target"
    console.print(f"   {options}")
    while True:
        try:
            answer = input("   > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]   (cancelled)[/dim]")
            return False
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no", ""):
            return False
        if answer in ("a", "always"):
            state.always_tools.append(tool_name)
            save(state, workdir)
            console.print(f"[dim]   (saved: always allow {tool_name} this project)[/dim]")
            return True
        if answer in ("p", "path") and target_path:
            state.always_paths.setdefault(tool_name, []).append(target_path)
            save(state, workdir)
            console.print(f"[dim]   (saved: always allow {tool_name} on {target_path})[/dim]")
            return True
        console.print("[yellow]   answer y/n/a" + ("/p" if target_path else "") + "[/yellow]")
