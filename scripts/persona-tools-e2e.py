"""End-to-end test for persona-admin tools through the agent loop.

Drives a single user turn through agent.run_turn against a real LLM
endpoint, with isolated XDG_CONFIG_HOME, and verifies the model
actually invokes the new persona tools (add_persona, set_active_persona)
when asked to create and activate a personality.

We don't assert anything about the persona TEXT the model generates;
this test is about whether the tool plumbing works end to end. A
robustness pass on prompt phrasing belongs in a separate script.

Usage (inside the container):
  python3 /opt/lillycoder/scripts/persona-tools-e2e.py \
      --api http://host.docker.internal:18092/v1 \
      --out /workspace/persona-tools-e2e.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "lib" / "lillycoder" / "persona"

ISOLATED_HOME = Path(tempfile.mkdtemp(prefix="lilly-tools-e2e-"))
os.environ["XDG_CONFIG_HOME"] = str(ISOLATED_HOME)

sys.path.insert(0, str(ROOT / "lib"))

# Force tool registration. Imports trigger side-effect register() calls.
from lillycoder.tools import registry  # noqa: F401
from lillycoder.tools import persona as persona_tool  # noqa: F401
from lillycoder.tools import persona_admin
from lillycoder.tools.registry import by_name
from lillycoder.endpoint import ModelInfo
from lillycoder.discovery import Endpoint
from lillycoder import agent
from lillycoder import config as lcfg

from rich.console import Console


PROBE = (
    "please create a brand-new personality for yourself called "
    "'pirate' (yes the seafaring kind, that's the name). after "
    "creating it, switch to it. don't ask me to confirm; just do it "
    "with your tools."
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=2048)
    args = ap.parse_args()

    out: list[str] = []

    def emit(line: str = "") -> None:
        out.append(line)
        print(line, flush=True)

    emit(f"# persona-tools e2e (isolated config: {ISOLATED_HOME})")
    emit()

    # Discover the model id the server is currently serving.
    with httpx.Client(base_url=args.api) as probe:
        resp = probe.get("/models", timeout=10.0)
        data = resp.json()
        models_list = data.get("data") or []
        if not models_list:
            emit("✗ server has no models")
            return 1
        model_id = models_list[0]["id"]
        # Try to get the context window from the meta block.
        meta = models_list[0].get("meta") or {}
        ctx = meta.get("n_ctx") or meta.get("n_ctx_train") or 8192
    emit(f"model: {model_id}, context: {ctx}")
    emit()

    endpoint = Endpoint(
        base_url=args.api,
        label="smoke",
        models=[model_id],
        raw_url=args.api,
    )
    model = ModelInfo(alias=model_id, endpoint=endpoint, context_window=ctx)

    # Install recording hooks on persona_admin so we don't have to spin
    # up the real REPL closure. These fake hooks just remember what was
    # asked of them and report success.
    seen = {"load_calls": [], "evolve_calls": []}

    def fake_load(name: str) -> dict:
        seen["load_calls"].append(name)
        return {"ok": True, "active": name, "chars": 100}

    def fake_evolve(enabled: bool) -> dict:
        seen["evolve_calls"].append(enabled)
        return {"ok": True, "evolve": enabled, "active": "pirate",
                "snapshotted_to": None}

    persona_admin.set_load_hook(fake_load)
    persona_admin.set_evolve_hook(fake_evolve)

    system_prompt = (PERSONA_DIR / "default.md").read_text()
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": PROBE},
    ]

    console = Console(force_terminal=False, width=120)
    workdir = Path("/tmp")
    bypass_perms = True  # no interactive prompts in a smoke

    emit(f"prompt: {PROBE}")
    emit()
    emit("--- agent turn output ---")
    try:
        with httpx.Client(base_url=args.api, timeout=600.0) as client:
            agent.run_turn(client, model, messages, console,
                           bypass_perms=bypass_perms,
                           workdir=workdir,
                           show_thoughts=False,
                           max_tokens=args.max_tokens)
    except KeyboardInterrupt:
        emit("✗ interrupted")
        return 1
    except Exception as e:
        emit(f"✗ run_turn raised: {type(e).__name__}: {e}")
        # Continue to inspect what we did capture.

    emit("--- end agent turn ---")
    emit()

    # Inspect the messages list for tool calls the model made.
    tool_call_log = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            tool_call_log.append({
                "name": fn.get("name"),
                "arguments": fn.get("arguments"),
            })

    emit("# tool calls observed")
    if not tool_call_log:
        emit("  (none)")
    for t in tool_call_log:
        name = t["name"]
        args_blob = t["arguments"] or ""
        try:
            parsed = json.loads(args_blob)
        except (TypeError, json.JSONDecodeError):
            parsed = args_blob
        # Truncate big text fields for readability.
        if isinstance(parsed, dict):
            for k, v in list(parsed.items()):
                if isinstance(v, str) and len(v) > 120:
                    parsed[k] = v[:120] + "..."
        emit(f"  - {name}: {parsed}")
    emit()

    # Verdict logic.
    called_names = [t["name"] for t in tool_call_log]
    add_called = "add_persona" in called_names
    activate_called = "set_active_persona" in called_names
    write_file_called = "write" in called_names or "write_file" in called_names

    emit("# verdict")
    emit(f"  add_persona invoked:        "
         f"{'PASS' if add_called else 'FAIL'}")
    emit(f"  set_active_persona invoked: "
         f"{'PASS' if activate_called else 'FAIL'}")
    emit(f"  did NOT write a raw file:   "
         f"{'PASS' if not write_file_called else 'FAIL'}")
    emit(f"  load hook recorded calls:   {seen['load_calls']}")

    Path(args.out).write_text("\n".join(out) + "\n")

    # PASS if both create and activate were called via tools, AND no
    # raw write_file was used to fake it.
    return 0 if (add_called and activate_called and not write_file_called) else 1


if __name__ == "__main__":
    sys.exit(main())
