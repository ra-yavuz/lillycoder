"""Connection layer.

Replaces the old docker-compose engine wrapper. Lillycoder does not start
servers; it consumes them. This module:

  - resolves an Endpoint via discovery, manual --api, or saved config
  - opens an httpx client against it
  - exposes a tiny ModelInfo passed downstream so the agent loop knows
    which model it is talking to (for tool-call format selection later)
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

import httpx
from rich.console import Console

from . import config
from . import discovery
from .discovery import Endpoint
from .toolcheck import is_tool_capable


@dataclass
class ModelInfo:
    """Equivalent of the old ModelEntry. Just enough for the agent to pass
    to the chat-completions request."""
    alias: str          # the model id we send in payload["model"]
    endpoint: Endpoint  # which server it lives on
    context_window: Optional[int] = None  # tokens, from server meta if known


def _pick_model(console: Console, endpoint: Endpoint,
                preferred: Optional[str], force: bool) -> str:
    """Choose which model alias to use from the endpoint's catalog."""
    models = endpoint.models or []
    if preferred:
        if preferred in models:
            return preferred
        console.print(f"[yellow]model {preferred!r} not in {endpoint.label} catalog "
                       f"({len(models)} available); using first one[/yellow]")
    if not models:
        # Endpoint exposed no model list. Most servers still accept any
        # name in payload["model"]; we pass through whatever the user gave.
        return preferred or "default"

    # Prefer a tool-capable model if one is available.
    capable = [m for m in models if is_tool_capable(m)]
    if capable:
        chosen = capable[0]
        if not preferred:
            console.print(f"[dim]   using {chosen} (tool-capable)[/dim]")
        return chosen

    # Nothing recognised as tool-capable. Warn unless --force.
    chosen = models[0]
    if not force:
        console.print(
            f"[yellow]⚠ no model in {endpoint.label} matches the tool-capable "
            f"allowlist. Tools may misfire on {chosen!r}. Pass --force to "
            f"silence, or switch your server to a Qwen 2.5+ / Gemma 3+ / "
            f"Llama 3.1+ family.[/yellow]"
        )
    return chosen


def _interactive_pick_endpoint(console: Console,
                                endpoints: list[Endpoint]) -> Optional[Endpoint]:
    if len(endpoints) == 1:
        ep = endpoints[0]
        console.print(f"🦊 found 1 endpoint: [cyan]{ep.base_url}[/cyan] "
                       f"([dim]{ep.label}[/dim], {len(ep.models)} models)")
        try:
            ans = input("   use it? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if ans in ("", "y", "yes"):
            return ep
        return None
    console.print(f"🦊 found {len(endpoints)} endpoints:")
    for i, ep in enumerate(endpoints, 1):
        console.print(f"   [{i}] [cyan]{ep.base_url}[/cyan]  "
                       f"[dim]{ep.label}, {len(ep.models)} models[/dim]")
    try:
        ans = input("   pick: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    try:
        idx = int(ans) - 1
        if 0 <= idx < len(endpoints):
            return endpoints[idx]
    except ValueError:
        pass
    return None


def _no_endpoint_help(console: Console) -> None:
    console.print()
    console.print("[yellow]🦊 no LLM server detected on this machine.[/yellow]")
    console.print("   options:")
    console.print("     1) start one yourself (llama.cpp llama-server, ollama, "
                   "LM Studio, etc.) and re-run lillycoder")
    console.print("     2) point lillycoder at a remote OpenAI-compatible URL:")
    console.print("        [dim]lillycoder --api http://your.host:8080/v1[/dim]")
    console.print("     3) try a tiny bootstrap model. Suggestion:")
    console.print("        [dim]Qwen2.5-Coder-3B-Instruct (~2GB, native tools)[/dim]")
    console.print("        We don't auto-download yet. Future work.")
    console.print()


@contextmanager
def acquire(api_url: Optional[str] = None,
            preferred_model: Optional[str] = None,
            force: bool = False,
            save_choice: bool = True,
            console: Optional[Console] = None):
    """Yield (model_info, httpx_client) for the chosen endpoint.

    Resolution order:
      1. --api URL passed in
      2. last saved endpoint (config.toml -> endpoint.url)
      3. discovery probe of localhost
      4. helpful error
    """
    cons = console or Console()

    # 1. explicit
    endpoint: Optional[Endpoint] = None
    if api_url:
        endpoint = discovery.manual_endpoint(api_url)

    # 2. saved
    if endpoint is None:
        cfg = config.load()
        saved = (cfg.get("endpoint") or {}).get("url")
        if saved:
            cons.print(f"[dim]🦊 trying saved endpoint: {saved}[/dim]")
            ep = discovery.manual_endpoint(saved)
            if ep.models or _ping(saved):
                endpoint = ep
            else:
                cons.print("[dim]   saved endpoint unreachable, scanning…[/dim]")

    # 3. discovery
    if endpoint is None:
        cons.print("[dim]🦊 scanning localhost for LLM servers…[/dim]")
        candidates = discovery.discover()
        if candidates:
            endpoint = _interactive_pick_endpoint(cons, candidates)

    # 4. fail
    if endpoint is None:
        _no_endpoint_help(cons)
        raise RuntimeError("no endpoint")

    # Persist for next run.
    if save_choice:
        cfg = config.load()
        cfg.setdefault("endpoint", {})["url"] = endpoint.base_url
        config.save(cfg)

    chosen_model = _pick_model(cons, endpoint, preferred_model, force)
    cons.print(f"[green]✓ {endpoint.label} · {chosen_model}[/green]")

    info = ModelInfo(
        alias=chosen_model,
        endpoint=endpoint,
        context_window=endpoint.context_for(chosen_model),
    )
    client = httpx.Client(base_url=endpoint.base_url, timeout=None)
    try:
        yield info, client
    finally:
        try:
            client.close()
        except Exception:
            pass


def _ping(url: str) -> bool:
    try:
        r = httpx.get(url.rstrip("/") + "/models", timeout=1.5)
        return r.status_code == 200
    except httpx.HTTPError:
        return False
