"""Hard-deny safety classifier.

Always-on. Even --bypass-permissions does NOT override this. Only an
explicit --unsafe flag (not yet exposed) can.

Coverage:
  - sudo / doas
  - rm -rf against root, home, $HOME, ~
  - mkfs, dd writing to /dev/*
  - recursive chmod / chown of root or home
  - fork bombs
  - writes / deletes outside the configured workspace tree
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SafetyVerdict:
    allowed: bool
    reason: str = ""        # human-readable; shown to user when blocked


# Patterns matched against a normalised single-line command string.
_DENY_RE = [
    (re.compile(r"\bsudo\b"),
     "uses sudo — root operations are blocked"),
    (re.compile(r"\bdoas\b"),
     "uses doas — root operations are blocked"),
    (re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+(/|~|\$HOME|\${HOME})(\s|$|/[^a-zA-Z0-9_])"),
     "rm -rf against /, ~, or $HOME — refused"),
    (re.compile(r"\bmkfs\."),
     "mkfs — filesystem creation is blocked"),
    (re.compile(r"\bdd\b[^|]*\bof=/dev/"),
     "dd writing to a device — blocked"),
    (re.compile(r"\bchmod\s+-[a-zA-Z]*R[^\s]*\s+(/|~|\$HOME|\${HOME})(\s|$|/[^a-zA-Z0-9_])"),
     "recursive chmod against /, ~, or $HOME — blocked"),
    (re.compile(r"\bchown\s+-[a-zA-Z]*R[^\s]*\s+\S+\s+(/|~|\$HOME|\${HOME})(\s|$|/[^a-zA-Z0-9_])"),
     "recursive chown against /, ~, or $HOME — blocked"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
     "fork bomb pattern — blocked"),
    (re.compile(r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b"),
     "shutdown/reboot — blocked"),
]


def classify_command(cmd: str) -> SafetyVerdict:
    """Return verdict for a shell command."""
    if not cmd or not cmd.strip():
        return SafetyVerdict(allowed=False, reason="empty command")
    flat = " " + cmd.strip() + " "
    for pat, reason in _DENY_RE:
        if pat.search(flat):
            return SafetyVerdict(allowed=False, reason=reason)
    return SafetyVerdict(allowed=True)


def classify_path_write(path: str, workspace: Optional[Path] = None) -> SafetyVerdict:
    """Block writes/deletes outside the workspace tree.

    workspace defaults to the current working directory when not given.
    Operator can widen by setting LILLY_ALLOW_OUTSIDE_CWD=1.
    """
    if os.environ.get("LILLY_ALLOW_OUTSIDE_CWD") == "1":
        return SafetyVerdict(allowed=True)
    target = Path(path).expanduser().resolve()
    base = (workspace or Path.cwd()).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return SafetyVerdict(
            allowed=False,
            reason=f"target {target} is outside workspace {base}; "
                   f"set LILLY_ALLOW_OUTSIDE_CWD=1 to allow")
    return SafetyVerdict(allowed=True)
