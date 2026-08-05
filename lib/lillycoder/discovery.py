"""Probe localhost for OpenAI-compatible /v1 endpoints.

LillyCoder accepts any OpenAI-compatible server. This module probes common
ports and, when hydra-llm is installed, asks it for the actual ports of every
running model. The Hydra lookup matters because custom aliases commonly live
outside the original 18080 compatibility port.

What we look for:

  port  | path        | typical server
  ------|-------------|--------------------------------------
  11434 | /api/tags   | ollama (its native API; we adapt to /v1)
  11435 | /api/tags   | ollama secondary instance
  8080  | /v1/models  | llama.cpp llama-server default
  8000  | /v1/models  | vLLM, text-generation-webui openai ext, generic FastAPI
  5001  | /v1/models  | koboldcpp default
  1234  | /v1/models  | LM Studio
  4891  | /v1/models  | GPT4All
  18080 | /v1/models  | hydra-llm (compatibility, not dependency)
  18092 | /v1/models  | allm
  8201  | /v1/models  | the legacy CONSOLE/ default port
  9090  | /v1/models  | mlx-lm and friends
  9091  | /v1/models  | mlx-lm secondary

Any server that returns a non-empty model list is a candidate. Lillycoder
does not care which family the server belongs to: as long as it speaks
OpenAI-compatible /v1, it works.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

import httpx


# Each probe: (port, path, friendly_label_when_detected, list_of_model_names_field)
KNOWN_PROBES: list[tuple[int, str, str, str]] = [
    (11434, "/api/tags",  "ollama",        "models"),
    (11435, "/api/tags",  "ollama",        "models"),
    (8080,  "/v1/models", "openai-compat", "data"),
    (8000,  "/v1/models", "openai-compat", "data"),
    (5001,  "/v1/models", "koboldcpp",     "data"),
    (1234,  "/v1/models", "lm-studio",     "data"),
    (4891,  "/v1/models", "gpt4all",       "data"),
    (18080, "/v1/models", "openai-compat", "data"),
    (18092, "/v1/models", "allm",          "data"),
    (8201,  "/v1/models", "openai-compat", "data"),
    (9090,  "/v1/models", "openai-compat", "data"),
    (9091,  "/v1/models", "openai-compat", "data"),
]


@dataclass
class Endpoint:
    base_url: str          # the OpenAI-compat base, e.g. http://localhost:8080/v1
    label: str             # friendly server label
    models: list[str]      # model names exposed
    raw_url: str           # the URL we probed (informational)
    # Per-model metadata harvested from /v1/models (id -> dict). Optional;
    # may be empty if the server doesn't expose meta. Known keys:
    #   n_ctx_train  - max trained context length (best signal we have)
    #   n_ctx        - runtime context length (from llama.cpp /props if probed)
    model_meta: dict = None
    tool_capable: Optional[bool] = None  # reported by server chat-template caps

    def __post_init__(self):
        if self.model_meta is None:
            self.model_meta = {}

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def models_url(self) -> str:
        return self.base_url.rstrip("/") + "/models"

    def context_for(self, model_id: str) -> Optional[int]:
        """Best-effort context window for a model id, in tokens. Prefers
        n_ctx (runtime) over n_ctx_train (theoretical max). Returns None
        if the server didn't tell us."""
        m = self.model_meta.get(model_id) or {}
        for key in ("n_ctx", "n_ctx_train"):
            v = m.get(key)
            if isinstance(v, int) and v > 0:
                return v
        return None


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


def _extract_meta(m: dict) -> dict:
    """Pull useful per-model metadata from a /v1/models entry. Servers
    expose this in different places; we look at the common ones and
    normalise to a small shared shape."""
    meta: dict = {}
    src_meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
    # llama.cpp / allm: meta.n_ctx_train, meta.n_ctx
    for key in ("n_ctx", "n_ctx_train", "n_embd", "n_params", "n_vocab", "size"):
        if key in src_meta and isinstance(src_meta[key], int):
            meta[key] = src_meta[key]
    # Some servers expose context_length / max_context_length / context_window
    for src_key, dst_key in (
        ("context_length", "n_ctx_train"),
        ("max_context_length", "n_ctx_train"),
        ("context_window", "n_ctx_train"),
    ):
        v = m.get(src_key)
        if isinstance(v, int) and v > 0 and dst_key not in meta:
            meta[dst_key] = v
    return meta


def _v1_models_to_endpoint(host: str, port: int, label: str,
                            raw: dict) -> Optional[Endpoint]:
    models: list[str] = []
    model_meta: dict = {}
    for m in raw.get("data", []) or []:
        name = m.get("id") or m.get("name")
        if not name:
            continue
        models.append(name)
        meta = _extract_meta(m)
        if meta:
            model_meta[name] = meta
    if not models:
        return None
    ep = Endpoint(
        base_url=f"http://{host}:{port}/v1",
        label=label,
        models=models,
        raw_url=f"http://{host}:{port}/v1/models",
        model_meta=model_meta,
    )
    # Best-effort: ask llama.cpp's /props for the runtime n_ctx (the
    # context the server was actually started with, which can differ from
    # the model's trained max). If reachable, apply it to all models on
    # this endpoint - llama.cpp serves one model with one context.
    props = _probe_llamacpp_props(host, port)
    runtime_ctx = _runtime_ctx_from_props(props)
    if runtime_ctx:
        for name in models:
            ep.model_meta.setdefault(name, {})["n_ctx"] = runtime_ctx
    caps = props.get("chat_template_caps") if isinstance(props, dict) else None
    if isinstance(caps, dict) and isinstance(caps.get("supports_tools"), bool):
        ep.tool_capable = caps["supports_tools"]
    return ep


def _probe_llamacpp_props(host: str, port: int,
                          timeout_s: float = 0.5) -> dict:
    """Best-effort llama.cpp /props response."""
    url = f"http://{host}:{port}/props"
    try:
        r = httpx.get(url, timeout=timeout_s)
        if r.status_code != 200:
            return {}
        d = r.json()
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _runtime_ctx_from_props(d: dict) -> Optional[int]:
    """Extract the active context length from a llama.cpp /props response."""
    # Various llama.cpp versions: top-level n_ctx, or
    # default_generation_settings.n_ctx, or generation_settings.n_ctx
    for path in (
        ("n_ctx",),
        ("default_generation_settings", "n_ctx"),
        ("generation_settings", "n_ctx"),
    ):
        cur = d
        ok = True
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                ok = False
                break
        if ok and isinstance(cur, int) and cur > 0:
            return cur
    return None


def _probe_llamacpp_runtime_ctx(host: str, port: int,
                                 timeout_s: float = 0.5) -> Optional[int]:
    """Compatibility wrapper returning only runtime n_ctx."""
    return _runtime_ctx_from_props(_probe_llamacpp_props(host, port, timeout_s))


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
             extra_probes: Optional[list[tuple[int, str, str, str]]] = None,
             include_hydra: bool = True,
             ) -> list[Endpoint]:
    """Probe known and Hydra-reported ports. Returns live endpoints.

    Probes run concurrently. A dead port therefore costs roughly one timeout,
    rather than one timeout multiplied by the size of the probe list.
    """
    probes = list(KNOWN_PROBES)
    if include_hydra:
        try:
            from . import hydra
            probes.extend(hydra.running_probes())
        except Exception:
            # Optional integration must never break generic discovery.
            pass
    probes.extend(extra_probes or [])

    # Keep the first label for duplicate port/path pairs. Hydra-specific
    # labels are preferred because they name the actual alias.
    unique: dict[tuple[int, str], tuple[int, str, str, str]] = {}
    for probe in probes:
        port, path, label, field = probe
        key = (port, path)
        if key not in unique or label.startswith("hydra:"):
            unique[key] = (port, path, label, field)
    probes = list(unique.values())

    def run_probe(probe):
        port, path, label, _field = probe
        return probe_one(host, port, path, label)

    workers = min(16, max(1, len(probes)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_probe, probes))

    seen: set[str] = set()
    found: list[Endpoint] = []
    for ep in results:
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
    models: list[str] = []
    model_meta: dict = {}
    try:
        r = httpx.get(base + "/models", timeout=3.0)
        r.raise_for_status()
        raw = r.json()
        for m in raw.get("data", []) or []:
            name = m.get("id") or m.get("name")
            if not name:
                continue
            models.append(name)
            meta = _extract_meta(m)
            if meta:
                model_meta[name] = meta
    except Exception:
        pass
    ep = Endpoint(
        base_url=base, label="manual", models=models,
        raw_url=base + "/models", model_meta=model_meta,
    )
    # Try /props for runtime n_ctx if this is a llama.cpp-style server.
    parsed = base.rsplit("/", 1)[0]  # strip /v1
    try:
        host_port = parsed.split("://", 1)[1]
        host, port_str = host_port.split(":", 1)
        port = int(port_str)
        props = _probe_llamacpp_props(host, port)
        runtime_ctx = _runtime_ctx_from_props(props)
        if runtime_ctx:
            for name in models:
                ep.model_meta.setdefault(name, {})["n_ctx"] = runtime_ctx
        caps = props.get("chat_template_caps") if isinstance(props, dict) else None
        if isinstance(caps, dict) and isinstance(caps.get("supports_tools"), bool):
            ep.tool_capable = caps["supports_tools"]
    except (ValueError, IndexError):
        pass
    return ep
