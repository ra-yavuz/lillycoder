"""XDG-compliant config + state paths.

Layout:
  ~/.config/lillycoder/
    config.toml              global settings (last endpoint, default persona, etc.)
    personas/<name>.md       persona files (override the bundled default)
  ~/.cache/lillycoder/
    models/                  optional bootstrap model downloads
  <project>/.lillycoder/
    history.jsonl            per-project chat history
    permissions.json         per-project always-allow choices

We use stdlib tomllib for parsing and a hand-rolled writer (no `tomli_w`
dependency) since the config schema is small and known.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore


def _xdg(env_name: str, default: Path) -> Path:
    v = os.environ.get(env_name)
    return Path(v) if v else default


CONFIG_HOME = _xdg("XDG_CONFIG_HOME", Path.home() / ".config") / "lillycoder"
CACHE_HOME = _xdg("XDG_CACHE_HOME", Path.home() / ".cache") / "lillycoder"
CONFIG_FILE = CONFIG_HOME / "config.toml"
PERSONAS_DIR = CONFIG_HOME / "personas"
BUNDLED_PERSONAS_DIR = Path(__file__).parent / "persona"


def ensure_dirs() -> None:
    CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    PERSONAS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_HOME.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        with CONFIG_FILE.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(cfg: dict) -> None:
    """Hand-rolled TOML writer for our flat schema."""
    ensure_dirs()
    lines: list[str] = []
    # Top-level scalars first.
    for k, v in cfg.items():
        if isinstance(v, dict):
            continue
        if isinstance(v, str):
            lines.append(f"{k} = {_quote(v)}")
        else:
            lines.append(f"{k} = {v}")
    # Then [section] blocks.
    for section, sub in cfg.items():
        if not isinstance(sub, dict):
            continue
        lines.append("")
        lines.append(f"[{section}]")
        for k, v in sub.items():
            if isinstance(v, str):
                lines.append(f"{k} = {_quote(v)}")
            elif isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
            elif isinstance(v, list):
                items = ", ".join(_quote(x) if isinstance(x, str) else str(x) for x in v)
                lines.append(f"{k} = [{items}]")
    CONFIG_FILE.write_text("\n".join(lines) + "\n")


def parse_max_tokens(value: object) -> Optional[int]:
    """Normalise a max_tokens setting from CLI or config into either
    None (auto - send no max_tokens field) or a positive int (cap).

    Accepts: None, '', 'auto', 'AUTO', '0' (treated as auto), int, str
    of int. Raises ValueError on unparseable input."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid max_tokens: {value!r}")
    if isinstance(value, int):
        if value <= 0:
            return None
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("", "auto", "none", "0", "-1"):
            return None
        try:
            n = int(s)
        except ValueError as e:
            raise ValueError(f"invalid max_tokens: {value!r}") from e
        if n <= 0:
            return None
        return n
    raise ValueError(f"invalid max_tokens: {value!r}")


_FALLBACK_PERSONA = (
    "You are Lilly, a friendly local-first coder assistant. Use your "
    "tools to read, write, and run things in the current folder. Be "
    "concise. Ask before doing anything destructive."
)


def list_personas() -> list[str]:
    """All available personas: bundled (shipped with the package) plus
    user-defined ones under ~/.config/lillycoder/personas/. A user file
    with the same stem as a bundled one shadows it."""
    names: set[str] = set()
    if BUNDLED_PERSONAS_DIR.exists():
        for p in BUNDLED_PERSONAS_DIR.glob("*.md"):
            names.add(p.stem)
    if PERSONAS_DIR.exists():
        for p in PERSONAS_DIR.glob("*.md"):
            # Skip bundled-base sidecars (<name>.bundled-base.md). They
            # exist alongside user shadows for diff purposes; they are
            # not selectable personas in their own right.
            if p.name.endswith(".bundled-base.md"):
                continue
            names.add(p.stem)
    names.add("default")
    return sorted(names)


def persona_path(name: str) -> Path | None:
    """Return the on-disk path for a persona, user dir winning over bundled.
    Returns None if no such persona exists. The bundled-base sidecar is
    not addressable here."""
    if name.endswith(".bundled-base"):
        return None
    user = PERSONAS_DIR / f"{name}.md"
    if user.exists():
        return user
    bundled = BUNDLED_PERSONAS_DIR / f"{name}.md"
    if bundled.exists():
        return bundled
    return None


def persona_origin(name: str) -> str:
    """One of 'user', 'bundled', 'missing' - useful for UI display."""
    if (PERSONAS_DIR / f"{name}.md").exists():
        return "user"
    if (BUNDLED_PERSONAS_DIR / f"{name}.md").exists():
        return "bundled"
    return "missing"


def load_persona(name: str = "default") -> str:
    """Resolve a persona name to text. User file wins over bundled file.
    Falls back to bundled 'default.md', then to a hardcoded string."""
    p = persona_path(name)
    if p is not None:
        try:
            return p.read_text()
        except OSError:
            pass
    bundled_default = BUNDLED_PERSONAS_DIR / "default.md"
    if bundled_default.exists():
        return bundled_default.read_text()
    return _FALLBACK_PERSONA


def bundled_base_path(name: str) -> Path:
    """Sidecar capturing the bundled persona text at the moment the user
    first shadowed it. Lives next to the user file as
    <name>.bundled-base.md. Used by `/personalities diff` to surface
    upstream drift after package updates."""
    return PERSONAS_DIR / f"{name}.bundled-base.md"


def add_persona(name: str, text: str, overwrite: bool = False) -> Path:
    """Save a new user persona to ~/.config/lillycoder/personas/<name>.md.
    Raises FileExistsError if it would clobber an existing user persona
    and overwrite is False. Bundled personas are never modified; this
    just creates a user-level shadow that takes precedence.

    If `name` matches a bundled persona and we don't already have a
    sidecar, snapshot the current bundled text alongside as
    <name>.bundled-base.md so future updates to the bundled file can be
    diffed against the user's shadow."""
    if not name or "/" in name or "\\" in name or name.startswith(".."):
        raise ValueError(f"invalid persona name: {name!r}")
    if not text or not text.strip():
        raise ValueError("empty persona text")
    PERSONAS_DIR.mkdir(parents=True, exist_ok=True)
    target = PERSONAS_DIR / f"{name}.md"
    if target.exists() and not overwrite:
        raise FileExistsError(str(target))
    target.write_text(text)
    bundled = BUNDLED_PERSONAS_DIR / f"{name}.md"
    sidecar = bundled_base_path(name)
    if bundled.exists() and not sidecar.exists():
        try:
            sidecar.write_text(bundled.read_text())
        except OSError:
            pass
    return target


def clone_persona(src: str, dst: str, overwrite: bool = False) -> Path:
    """Copy persona text from `src` (bundled or user) into a new user
    file named `dst`. Raises FileNotFoundError if src is missing,
    FileExistsError if dst already exists and overwrite is False.

    Sidecar capture follows add_persona's rules: if dst happens to
    shadow a bundled name, a bundled-base.md is written too."""
    src_path = persona_path(src)
    if src_path is None:
        raise FileNotFoundError(f"no such persona: {src!r}")
    return add_persona(dst, src_path.read_text(), overwrite=overwrite)


def remove_persona(name: str) -> str:
    """Delete a user persona. Returns one of:
      'removed'   - the user file was deleted
      'bundled'   - the name refers to a bundled persona; nothing removed
      'missing'   - no such persona at all
    Bundled personas are read-only and cannot be removed via this API."""
    user = PERSONAS_DIR / f"{name}.md"
    if user.exists():
        user.unlink()
        sidecar = bundled_base_path(name)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass
        return "removed"
    if (BUNDLED_PERSONAS_DIR / f"{name}.md").exists():
        return "bundled"
    return "missing"
