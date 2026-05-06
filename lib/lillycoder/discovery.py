"""Probe localhost for OpenAI-compatible /v1 endpoints.

Lillycoder does not boot models. It expects a local LLM server to already
be running somewhere on the machine (or to be told via --api). This module
finds those servers automatically by probing well-known ports.

What we look for:

  port  | path        | typical server
  ------|-------------|--------------------------------------
  11434 | /api/tags   | ollama (its native API; we adapt to /v1)
  8080  | /v1/models  | llama.cpp llama-server default
  1234  | /v1/models  | LM Studio
  18080 | /v1/models  | hydra-llm (compatibility, not dependency)
  8201  | /v1/models  | the legacy CONSOLE/ default port

Any server that returns a non-empty model list is a candidate. Lillycoder
does not care which family the server belongs to: as long as it speaks
OpenAI-compatible /v1, it works.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx


# Each probe: (port, path, friendly_label_when_detected, list_of_model_names_field)
KNOWN_PROBES: list[tuple[int, str, str, str]] = [
    (11434, "/api/tags",  "ollama",        "models"),
    (8080,  "/v1/models", "openai-compat", "data"),
    (1234,  "/v1/models", "lm-studio",     "data"),
    (18080, "/v1/models", "openai-compat", "data"),
    (8201,  "/v1/models", "openai-compat", "data"),
]


@dataclass
class Endpoint:
    base_url: str          # the OpenAI-compat base, e.g. http://localhost:8080/v1
    label: str             # friendly server label
    models: list[str]      # model names exposed
    raw_url: str           # the URL we probed (informational)

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def models_url(self) -> str:
        return self.base_url.rstrip("/") + "/models"


def _ollama_to_v1(host: str, port: int, raw: dict) -> Optional[Endpoint]:
    """Ollama exposes an OpenAI-compatible surface at /v1 alongside its
    native /api/tags. We discover via /api/tags (always works), then point
    the actual chat client at /v1."""
    models = []
    for m in raw.get("models", []) or []:
        name = m.get("name") or m.get("model")
        if name:
            models.append(name)
    if not models:
        return None
    return Endpoint(
        base_url=f"http://{host}:{port}/v1",
        label="ollama",
        models=models,
        raw_url=f"http://{host}:{port}/api/tags",
    )


def _v1_models_to_endpoint(host: str, port: int, label: str,
                            raw: dict) -> Optional[Endpoint]:
    models = []
    for m in raw.get("data", []) or []:
        name = m.get("id") or m.get("name")
        if name:
            models.append(name)
    if not models:
        return None
    return Endpoint(
        base_url=f"http://{host}:{port}/v1",
        label=label,
        models=models,
        raw_url=f"http://{host}:{port}/v1/models",
    )


def probe_one(host: str, port: int, path: str, label: str,
              timeout_s: float = 1.0) -> Optional[Endpoint]:
    """Try one (port, path) combo. Returns Endpoint or None."""
    url = f"http://{host}:{port}{path}"
    try:
        r = httpx.get(url, timeout=timeout_s)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        raw = r.json()
    except Exception:
        return None
    if path == "/api/tags":
        return _ollama_to_v1(host, port, raw)
    return _v1_models_to_endpoint(host, port, label, raw)


def discover(host: str = "localhost",
             extra_probes: Optional[list[tuple[int, str, str, str]]] = None
             ) -> list[Endpoint]:
    """Probe every known port. Returns list of live endpoints."""
    probes = KNOWN_PROBES + (extra_probes or [])
    seen: set[str] = set()
    found: list[Endpoint] = []
    for port, path, label, _field in probes:
        ep = probe_one(host, port, path, label)
        if ep and ep.base_url not in seen:
            seen.add(ep.base_url)
            found.append(ep)
    return found


def manual_endpoint(url: str) -> Endpoint:
    """User passed --api URL: assume it's OpenAI-compat, fetch model list."""
    # Normalise: trim trailing slashes and strip /chat/completions if pasted.
    base = url.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if not base.endswith("/v1"):
        base = base + "/v1"
    try:
        r = httpx.get(base + "/models", timeout=3.0)
        r.raise_for_status()
        raw = r.json()
        models = [m.get("id") or m.get("name") for m in raw.get("data", [])]
        models = [m for m in models if m]
    except Exception:
        models = []
    return Endpoint(base_url=base, label="manual", models=models, raw_url=base + "/models")
