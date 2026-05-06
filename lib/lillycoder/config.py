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


def list_personas() -> list[str]:
    """User-defined personas plus 'default' (the bundled lilly-coder)."""
    out = ["default"]
    if PERSONAS_DIR.exists():
        for p in sorted(PERSONAS_DIR.glob("*.md")):
            out.append(p.stem)
    return out


def load_persona(name: str = "default") -> str:
    """Resolve a persona name to text. 'default' = bundled lilly-coder.
    Anything else = file under ~/.config/lillycoder/personas/<name>.md"""
    if name and name != "default":
        p = PERSONAS_DIR / f"{name}.md"
        if p.exists():
            return p.read_text()
    # Fall back to bundled.
    bundled = Path(__file__).parent / "persona" / "default.md"
    if bundled.exists():
        return bundled.read_text()
    return (
        "You are Lilly, a friendly local-first coder assistant. Use your "
        "tools to read, write, and run things in the current folder. Be "
        "concise. Ask before doing anything destructive."
    )
