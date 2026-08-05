"""Optional bridge to the installed hydra-llm command.

LillyCoder remains usable with any OpenAI-compatible server. When hydra-llm
is installed, this module adds two conveniences without creating a Python
package dependency:

* discover every running Hydra model, including ports outside LillyCoder's
  built-in probe list;
* list downloaded models and start one selected by the user.

The bridge consumes hydra-llm's stable JSON CLI output. This also works with
the Debian layout, where hydra_llm is intentionally not importable on the
normal Python path.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional


class HydraError(RuntimeError):
    """A hydra-llm command could not be completed."""


def executable() -> Optional[str]:
    """Return the hydra-llm executable, if installed.

    LILLY_HYDRA_BIN is mainly useful to package tests and development builds.
    """
    override = os.environ.get("LILLY_HYDRA_BIN")
    if override:
        return override
    return shutil.which("hydra-llm")


def available() -> bool:
    return executable() is not None


def _run_json(*args: str, timeout: float = 15.0) -> dict:
    exe = executable()
    if not exe:
        raise HydraError("hydra-llm is not installed")
    try:
        result = subprocess.run(
            [exe, *args, "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise HydraError(f"hydra-llm {' '.join(args)} timed out") from exc
    except OSError as exc:
        raise HydraError(f"could not run hydra-llm: {exc}") from exc

    raw = result.stdout.strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        detail = result.stderr.strip() or raw or "no output"
        raise HydraError(f"invalid response from hydra-llm: {detail}") from exc
    if result.returncode != 0 or payload.get("ok") is False:
        detail = payload.get("error") or result.stderr.strip()
        health = payload.get("health") or {}
        if not detail and health.get("state"):
            detail = f"model startup ended in state {health['state']}"
        raise HydraError(detail or f"hydra-llm exited {result.returncode}")
    return payload


def status() -> list[dict]:
    """Return Hydra status rows. Fail-soft when an older install misbehaves."""
    if not available():
        return []
    try:
        payload = _run_json("status")
    except HydraError:
        return []
    rows = payload.get("rows") or []
    return [row for row in rows if isinstance(row, dict)]


def running_probes() -> list[tuple[int, str, str, str]]:
    """Discovery probes for every running Hydra chat model."""
    probes = []
    for row in status():
        port = row.get("port")
        if row.get("state") != "running" or not isinstance(port, int):
            continue
        alias = row.get("alias") or "model"
        probes.append((port, "/v1/models", f"hydra:{alias}", "data"))
    return probes


def downloaded_models() -> list[dict]:
    """Downloaded Hydra catalog entries, with fit and running annotations."""
    if not available():
        return []
    payload = _run_json("list")
    models = payload.get("models") or []
    downloaded = [
        model for model in models
        if isinstance(model, dict) and model.get("downloaded")
    ]
    return sorted(downloaded, key=lambda model: str(model.get("id") or ""))


def start(alias: str, timeout: float = 90.0) -> dict:
    """Start a downloaded Hydra model and return its JSON startup record."""
    if not alias or not alias.strip():
        raise HydraError("empty model alias")
    payload = _run_json("start", alias.strip(), timeout=timeout)
    port = payload.get("port")
    if not isinstance(port, int):
        raise HydraError("hydra-llm started the model but returned no port")
    payload.setdefault("base_url", f"http://127.0.0.1:{port}/v1")
    return payload
