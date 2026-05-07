"""Per-folder conversation session manager.

Storage layout, per cwd:

    <cwd>/.lillycoder/
      sessions/
        20260507T053100-pirate-flag.jsonl     # one file per session
        20260506T194000-legacy.jsonl
      state.json                              # {"active": "<filename>"}

Each session file is jsonl: one message per line, {"role": ..., "content": ...}.
Loading a session means reading back its messages and prepending the
current system prompt (which is owned by the persona system, not the
session). Sessions are append-only on the wire: a new turn appends to
the active session.

Migration: an existing pre-0.2.0 layout (.lillycoder/history.jsonl)
gets renamed once into sessions/legacy-<date>.jsonl and that becomes
the active session.

Git integration: when cwd is inside a git repo, append a single line
('**/.lillycoder/') to .git/info/exclude on first session-store init.
This is the local-only ignore file, never committed, so collaborators
are not affected. We never modify the tracked .gitignore.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


_TIMESTAMP_RE = re.compile(r"^(\d{8}T\d{6})-(.+)\.jsonl$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(label: str, fallback: str = "session") -> str:
    """Make a safe filename slug. Empty/punctuation-only inputs fall
    back to the supplied default."""
    s = _SLUG_RE.sub("-", label.lower()).strip("-")
    return s[:60] if s else fallback


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S")


@dataclass(frozen=True)
class SessionInfo:
    id: str          # the bare filename without .jsonl, e.g. "20260507T053100-pirate"
    path: Path       # full path to the .jsonl file
    timestamp: str   # YYYYMMDDTHHMMSS portion
    label: str       # the slug portion
    turn_count: int  # number of user/assistant message pairs (rough)
    is_active: bool


def _walk_to_repo_root(start: Path) -> Optional[Path]:
    """Return the git repo root containing `start`, or None if not in
    a git repo. Worktrees use a `.git` file (not a directory); both
    count."""
    cur = start.resolve()
    while True:
        candidate = cur / ".git"
        if candidate.exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


class SessionStore:
    """Manages a per-cwd session directory and its state pointer."""

    LEGACY_FILE = "history.jsonl"
    SESSIONS_SUBDIR = "sessions"
    STATE_FILE = "state.json"
    EXCLUDE_LINE = "**/.lillycoder/"

    def __init__(self, cwd: Path):
        self.cwd = cwd
        self.root = cwd / ".lillycoder"
        self.sessions_dir = self.root / self.SESSIONS_SUBDIR
        self.state_path = self.root / self.STATE_FILE
        self._ensured = False
        self._git_excluded_msg: Optional[str] = None

    # --- setup ----------------------------------------------------------

    def ensure(self) -> None:
        """Create the directory, migrate legacy state, set up git
        exclusion. Idempotent. Safe to call repeatedly."""
        if self._ensured:
            return
        self.root.mkdir(exist_ok=True)
        self.sessions_dir.mkdir(exist_ok=True)
        self._migrate_legacy()
        self._ensure_state()
        self._git_excluded_msg = self._ensure_git_exclude()
        self._ensured = True

    def git_excluded_message(self) -> Optional[str]:
        """One-line note about the .git/info/exclude write (if we did
        one), suitable to print at startup. Returns None if nothing
        was written."""
        return self._git_excluded_msg

    def _migrate_legacy(self) -> None:
        """If a pre-0.2.0 .lillycoder/history.jsonl exists, move it
        into sessions/legacy-<timestamp>.jsonl. The legacy file
        becomes the active session pointer."""
        legacy = self.root / self.LEGACY_FILE
        if not legacy.exists():
            return
        if legacy.stat().st_size == 0:
            try:
                legacy.unlink()
            except OSError:
                pass
            return
        ts = _timestamp()
        dest = self.sessions_dir / f"{ts}-legacy.jsonl"
        try:
            legacy.rename(dest)
        except OSError:
            return
        # Make it the active session.
        try:
            self.state_path.write_text(json.dumps({"active": dest.name}))
        except OSError:
            pass

    def _ensure_state(self) -> None:
        """If there is no state.json, point to the most-recent session
        file (if any) or leave the pointer empty so a fresh session
        is created on first turn."""
        if self.state_path.exists():
            return
        existing = sorted(self.sessions_dir.glob("*.jsonl"))
        active = existing[-1].name if existing else None
        try:
            self.state_path.write_text(
                json.dumps({"active": active}, ensure_ascii=False),
            )
        except OSError:
            pass

    def _ensure_git_exclude(self) -> Optional[str]:
        """If cwd is inside a git repo, append '**/.lillycoder/' to
        .git/info/exclude unless already present. Returns a one-line
        note describing what we did, or None if nothing happened."""
        repo_root = _walk_to_repo_root(self.cwd)
        if repo_root is None:
            return None
        info_dir = repo_root / ".git"
        # Worktree: .git is a file pointing at the real gitdir.
        if info_dir.is_file():
            try:
                content = info_dir.read_text()
            except OSError:
                return None
            for line in content.splitlines():
                if line.startswith("gitdir:"):
                    info_dir = Path(line.split(":", 1)[1].strip())
                    break
            else:
                return None
        exclude_path = info_dir / "info" / "exclude"
        try:
            exclude_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        existing = ""
        if exclude_path.exists():
            try:
                existing = exclude_path.read_text()
            except OSError:
                return None
        for line in existing.splitlines():
            if line.strip() == self.EXCLUDE_LINE:
                return None  # already there, nothing to do
        new_block = ""
        if existing and not existing.endswith("\n"):
            new_block += "\n"
        new_block += (
            "# Added by lillycoder so its session storage in any\n"
            "# subdirectory of this repo is hidden from `git status`.\n"
            "# This is .git/info/exclude, not the tracked .gitignore;\n"
            "# safe to remove or amend.\n"
            f"{self.EXCLUDE_LINE}\n"
        )
        try:
            with exclude_path.open("a") as f:
                f.write(new_block)
        except OSError:
            return None
        return f"hid .lillycoder/ via {exclude_path}"

    # --- active session ----------------------------------------------------

    def active(self) -> Optional[Path]:
        """Path to the currently active session file, if any. May not
        exist on disk yet (a session is created lazily on first
        write)."""
        if not self.state_path.exists():
            return None
        try:
            data = json.loads(self.state_path.read_text() or "{}")
        except (OSError, json.JSONDecodeError):
            return None
        name = data.get("active")
        if not name or not isinstance(name, str):
            return None
        return self.sessions_dir / name

    def set_active(self, path: Path) -> None:
        try:
            self.state_path.write_text(
                json.dumps({"active": path.name}, ensure_ascii=False),
            )
        except OSError:
            pass

    # --- operations -------------------------------------------------------

    def list(self) -> list[SessionInfo]:
        """Every session file in the dir, newest first."""
        active_path = self.active()
        active_name = active_path.name if active_path is not None else None
        result: list[SessionInfo] = []
        for p in sorted(self.sessions_dir.glob("*.jsonl"), reverse=True):
            m = _TIMESTAMP_RE.match(p.name)
            if m:
                ts, label = m.group(1), m.group(2)
            else:
                ts, label = "", p.stem
            turn_count = 0
            try:
                with p.open() as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            d = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if d.get("role") == "user":
                            turn_count += 1
            except OSError:
                pass
            result.append(SessionInfo(
                id=p.stem,
                path=p,
                timestamp=ts,
                label=label,
                turn_count=turn_count,
                is_active=(p.name == active_name),
            ))
        return result

    def new(self, label: Optional[str] = None) -> Path:
        """Create a new empty session file and make it active.
        Returns the new path."""
        slug = _slugify(label or "", fallback="session")
        ts = _timestamp()
        path = self.sessions_dir / f"{ts}-{slug}.jsonl"
        # Avoid clobbering an existing same-second filename.
        i = 2
        while path.exists():
            path = self.sessions_dir / f"{ts}-{slug}-{i}.jsonl"
            i += 1
        path.touch()
        self.set_active(path)
        return path

    def resolve(self, ident: str) -> Optional[Path]:
        """Look up a session by full id, label, or numeric prefix. The
        numeric prefix is the index in the listing (1-based, newest
        first), so `/session load 1` works."""
        ident = ident.strip()
        if not ident:
            return None
        listing = self.list()
        # 1-based index?
        if ident.isdigit():
            i = int(ident)
            if 1 <= i <= len(listing):
                return listing[i - 1].path
            return None
        # Exact id (timestamp-label) match?
        for s in listing:
            if s.id == ident:
                return s.path
        # Label match?
        for s in listing:
            if s.label == ident:
                return s.path
        return None

    def project_label(self) -> str:
        """Short human-readable label for the current cwd, suitable for
        the bottom toolbar. Prefers <repo-name>/<relative-path> when
        we're inside a git repo, otherwise project-marker root, else
        basename of cwd."""
        cwd = self.cwd.resolve()
        repo_root = _walk_to_repo_root(cwd)
        if repo_root is not None:
            try:
                rel = cwd.relative_to(repo_root)
            except ValueError:
                rel = Path(".")
            base = repo_root.name
            return base if str(rel) == "." else f"{base}/{rel}"
        # Project-marker fallback.
        for marker in ("pyproject.toml", "package.json", "Cargo.toml",
                       "go.mod", "pom.xml"):
            p = cwd
            while True:
                if (p / marker).exists():
                    try:
                        rel = cwd.relative_to(p)
                    except ValueError:
                        rel = Path(".")
                    base = p.name
                    return base if str(rel) == "." else f"{base}/{rel}"
                if p.parent == p:
                    break
                p = p.parent
        return cwd.name


def append_message(path: Path, role: str, content: str) -> None:
    """Append a message line to a session file. Creates the file if
    missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"role": role, "content": content},
                           ensure_ascii=False) + "\n")


def load_messages(path: Optional[Path], system_prompt: str) -> list[dict]:
    """Read a session file's user/assistant messages and prepend the
    current system prompt. If path is None or missing, return just
    the system prompt."""
    msgs = [{"role": "system", "content": system_prompt}]
    if path is None or not path.exists():
        return msgs
    try:
        with path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("role") in ("user", "assistant"):
                    msgs.append({"role": d["role"], "content": d["content"]})
    except OSError:
        return msgs
    return msgs
