"""Connection layer.

Replaces the old docker-compose engine wrapper. LillyCoder consumes any
compatible server directly and can optionally ask hydra-llm to start an
already-downloaded model. This module:

  - resolves an Endpoint via discovery, manual --api, or saved config
  - offers guided Hydra startup when no endpoint is running
  - opens an httpx client against it
  - exposes a tiny ModelInfo passed downstream so the agent loop knows
    which model it is talking to (for tool-call format selection later)
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import time
from typing import Optional
from urllib.parse import urlsplit

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
    multimodal: bool = False  # server reports image/audio input support


def _endpoint_key(endpoint: Endpoint) -> tuple[str, Optional[int], str]:
    """Canonical identity, treating common loopback spellings as equal."""
    parsed = urlsplit(endpoint.base_url)
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        host = "loopback"
    return host, parsed.port, parsed.path.rstrip("/")


def _pick_model(console: Console, endpoint: Endpoint,
                preferred: Optional[str], force: bool,
                interactive: bool = True) -> str:
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

    if interactive and not preferred and len(models) > 1:
        console.print(f"🦊 [bold]{endpoint.label}[/bold] offers {len(models)} models:")
        for i, name in enumerate(models, 1):
            supports_tools = endpoint.tool_capable is True or is_tool_capable(name)
            capable = " [green]tools[/green]" if supports_tools else ""
            console.print(f"   [bold]{i}[/bold]. [cyan]{name}[/cyan]{capable}")
        try:
            answer = input("   model number or name [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if not answer:
            preferred = models[0]
        elif answer.isdigit() and 1 <= int(answer) <= len(models):
            preferred = models[int(answer) - 1]
        elif answer in models:
            preferred = answer
        else:
            console.print(f"[yellow]unknown model {answer!r}; using {models[0]}[/yellow]")
            preferred = models[0]
        return preferred

    # Prefer a tool-capable model if one is available.
    capable = list(models) if endpoint.tool_capable is True else [
        m for m in models if is_tool_capable(m)
    ]
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
                               endpoints: list[Endpoint],
                               default: Optional[Endpoint] = None,
                               interactive: bool = True) -> Optional[Endpoint]:
    if len(endpoints) == 1:
        ep = endpoints[0]
        console.print(f"🦊 found [cyan]{ep.base_url}[/cyan] "
                      f"([dim]{ep.label}[/dim], {len(ep.models)} models)")
        return ep
    if not interactive:
        return default or (endpoints[0] if endpoints else None)
    console.print(f"🦊 found {len(endpoints)} endpoints:")
    for i, ep in enumerate(endpoints, 1):
        marker = " [green](saved)[/green]" if default is ep else ""
        names = ", ".join(ep.models[:2])
        if len(ep.models) > 2:
            names += f", +{len(ep.models) - 2}"
        console.print(f"   [{i}] [cyan]{ep.base_url}[/cyan]  "
                      f"[dim]{ep.label}, {names or 'model list unavailable'}[/dim]"
                      f"{marker}")
    default_index = endpoints.index(default) + 1 if default in endpoints else 1
    try:
        ans = input(f"   endpoint number [{default_index}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not ans:
        return endpoints[default_index - 1]
    try:
        idx = int(ans) - 1
        if 0 <= idx < len(endpoints):
            return endpoints[idx]
    except ValueError:
        for ep in endpoints:
            if ans in (ep.base_url, ep.label):
                return ep
    console.print(f"[yellow]unknown endpoint selection: {ans}[/yellow]")
    return None


def _wait_for_endpoint(base_url: str, timeout_s: float = 60.0) -> Endpoint:
    """Wait for a newly started local model to expose /v1/models."""
    deadline = time.monotonic() + timeout_s
    last = discovery.manual_endpoint(base_url)
    while time.monotonic() < deadline:
        if last.models or _ping(last.base_url):
            return last
        time.sleep(1.0)
        last = discovery.manual_endpoint(base_url)
    return last


def _interactive_start_hydra(console: Console) -> Optional[Endpoint]:
    """Offer downloaded Hydra models and start the user's selection."""
    try:
        from . import hydra
        if not hydra.available():
            return None
        models = hydra.downloaded_models()
    except Exception as exc:
        console.print(f"[yellow]Hydra is installed but its model list failed: {exc}[/yellow]")
        return None

    console.print()
    console.print("🦊 [bold]Hydra model manager detected.[/bold]")
    if not models:
        console.print("   No chat models are downloaded yet.")
        console.print("   Browse them with [dim]hydra-llm list-online[/dim], then use "
                      "[dim]hydra-llm download <id>[/dim].")
        return None

    console.print("   Choose a downloaded model for LillyCoder:")
    for i, item in enumerate(models, 1):
        alias = item.get("id") or "?"
        size = f"{item.get('size_gb')} GB" if item.get("size_gb") else "size unknown"
        fit = item.get("fit") or "?"
        port = item.get("running_port")
        running = f", running on {port}" if port else ""
        console.print(
            f"   [bold]{i}[/bold]. [cyan]{alias}[/cyan]  "
            f"[dim]{size}, fit: {fit}{running}[/dim]"
        )
    try:
        answer = input("   model number or alias (Enter to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return None
    if not answer:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(models):
        selected = models[int(answer) - 1]
    else:
        selected = next((m for m in models if m.get("id") == answer), None)
        if selected is None:
            console.print(f"[yellow]unknown Hydra model: {answer}[/yellow]")
            return None

    alias = selected.get("id")
    try:
        running_port = selected.get("running_port")
        if isinstance(running_port, int):
            base_url = f"http://127.0.0.1:{running_port}/v1"
            console.print(f"[dim]   waiting for {alias} on port {running_port}...[/dim]")
        else:
            console.print(f"[dim]   starting {alias} with hydra-llm...[/dim]")
            started = hydra.start(str(alias))
            base_url = started["base_url"]
        endpoint = _wait_for_endpoint(base_url)
    except Exception as exc:
        console.print(f"[red]✗ could not start {alias}: {exc}[/red]")
        return None
    if not endpoint.models and not _ping(endpoint.base_url):
        console.print(f"[red]✗ {alias} did not become ready at {endpoint.base_url}[/red]")
        return None
    endpoint.label = f"hydra:{alias}"
    console.print(f"[green]✓ {alias} is ready at {endpoint.base_url}[/green]")
    return endpoint


def _no_endpoint_help(console: Console, hydra_offered: bool = False) -> None:
    console.print()
    console.print("[yellow]🦊 no LLM server detected on this machine.[/yellow]")
    if hydra_offered:
        console.print("   No Hydra model was selected.")
    console.print("   You can also point LillyCoder at a remote OpenAI-compatible URL:")
    console.print("        [dim]lillycoder --api http://your.host:8080/v1[/dim]")
    console.print("   Or start llama.cpp, Ollama, or LM Studio and run LillyCoder again.")
    console.print()


@contextmanager
def acquire(api_url: Optional[str] = None,
            preferred_model: Optional[str] = None,
            force: bool = False,
            save_choice: bool = True,
            interactive: bool = True,
            console: Optional[Console] = None):
    """Yield (model_info, httpx_client) for the chosen endpoint.

    Resolution order:
      1. --api URL passed in
      2. saved endpoint plus discovery of all local/Hydra endpoints
      3. guided startup of a downloaded Hydra model
      4. helpful error
    """
    cons = console or Console()

    # 1. explicit
    endpoint: Optional[Endpoint] = None
    if api_url:
        endpoint = discovery.manual_endpoint(api_url)

    # 2. saved plus discovery. A live saved endpoint remains the default, but
    # other running ports are no longer hidden from the user.
    if endpoint is None:
        cfg = config.load()
        saved = (cfg.get("endpoint") or {}).get("url")
        saved_ep = None
        if saved:
            cons.print(f"[dim]🦊 checking saved endpoint: {saved}[/dim]")
            candidate = discovery.manual_endpoint(saved)
            if candidate.models or _ping(saved):
                saved_ep = candidate
            else:
                cons.print("[dim]   saved endpoint unreachable[/dim]")
        cons.print("[dim]🦊 scanning localhost for LLM servers…[/dim]")
        candidates = discovery.discover()
        combined = []
        if saved_ep is not None:
            combined.append(saved_ep)
        seen = {_endpoint_key(saved_ep)} if saved_ep is not None else set()
        for candidate in candidates:
            key = _endpoint_key(candidate)
            if key in seen:
                existing = next(
                    ep for ep in combined if _endpoint_key(ep) == key
                )
                # Keep the saved default object, but enrich it with Hydra's
                # alias and server capability probe.
                if candidate.label.startswith("hydra:"):
                    existing.label = candidate.label
                if candidate.tool_capable is not None:
                    existing.tool_capable = candidate.tool_capable
                if candidate.model_meta:
                    existing.model_meta.update(candidate.model_meta)
                continue
            combined.append(candidate)
            seen.add(key)
        if combined:
            endpoint = _interactive_pick_endpoint(
                cons, combined, default=saved_ep, interactive=interactive,
            )

    # 3. guided Hydra startup.
    hydra_offered = False
    if endpoint is None and interactive:
        try:
            from . import hydra
            hydra_offered = hydra.available()
        except Exception:
            hydra_offered = False
        if hydra_offered:
            endpoint = _interactive_start_hydra(cons)

    # 4. fail
    if endpoint is None:
        _no_endpoint_help(cons, hydra_offered=hydra_offered)
        raise RuntimeError("no endpoint")

    # Persist for next run.
    if save_choice:
        cfg = config.load()
        cfg.setdefault("endpoint", {})["url"] = endpoint.base_url
        config.save(cfg)

    chosen_model = _pick_model(
        cons, endpoint, preferred_model, force, interactive=interactive,
    )
    cons.print(f"[green]✓ {endpoint.label} · {chosen_model}[/green]")

    info = ModelInfo(
        alias=chosen_model,
        endpoint=endpoint,
        context_window=endpoint.context_for(chosen_model),
        multimodal=_probe_multimodal(endpoint.base_url, chosen_model),
    )
    client = httpx.Client(base_url=endpoint.base_url, timeout=None)
    try:
        yield info, client
    finally:
        try:
            client.close()
        except Exception:
            pass


def _probe_multimodal(base_url: str, alias: str) -> bool:
    """Ask the server whether `alias` accepts image/audio input.

    llama-server's /v1/models reports per-model `capabilities`, e.g.
    ["completion", "multimodal"]. Best-effort: any failure -> False, so a
    server that doesn't expose capabilities simply offers no /image command.
    """
    try:
        r = httpx.get(base_url.rstrip("/") + "/models", timeout=2.0)
        if r.status_code != 200:
            return False
        for m in r.json().get("models") or r.json().get("data") or []:
            if m.get("id") == alias or m.get("model") == alias or m.get("name") == alias:
                caps = m.get("capabilities") or []
                return "multimodal" in caps or "vision" in caps or "audio" in caps
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return False
    return False


def _ping(url: str) -> bool:
    try:
        r = httpx.get(url.rstrip("/") + "/models", timeout=1.5)
        return r.status_code == 200
    except httpx.HTTPError:
        return False
