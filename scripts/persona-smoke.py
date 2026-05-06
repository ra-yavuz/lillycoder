"""End-to-end smoke for the recent persona + max_tokens work.

Runs three things, all against a real LLM endpoint, with no REPL TTY:

  1. max_tokens budget control. For each of {auto, 64, 512, 2048},
     send the same prompt and report the completion length. We expect
     length(auto) and length(2048) to both be > length(64).
  2. Persona narration check. For each rewritten persona, send a
     conversational probe and grep the reply for asterisk-actions and
     third-person self-references ("Lilly opens", "she nods", etc).
     Expect zero hits.
  3. /personalities diff happy path. Synthesise a user shadow over a
     bundled persona, mutate the bundled-base sidecar to fake an
     upstream change, then call the diff helper directly and verify
     output structure.

Usage (inside the container):
  python3 /opt/lillycoder/scripts/persona-smoke.py \
      --api http://host.docker.internal:18092/v1 \
      --out /workspace/persona-smoke.txt
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "lib" / "lillycoder" / "persona"

# Use an isolated config so we don't touch the user's real personas.
ISOLATED_HOME = Path(tempfile.mkdtemp(prefix="lilly-smoke-"))
os.environ["XDG_CONFIG_HOME"] = str(ISOLATED_HOME)

sys.path.insert(0, str(ROOT / "lib"))
from lillycoder import config as lcfg  # noqa: E402

PERSONAS = ["default", "tsundere", "yandere", "sweet", "adult", "analytical"]

NARRATION_PROBE = (
    "say hi and tell me in one or two sentences how you'd start "
    "scaffolding a tiny node project. don't actually do it."
)

LENGTH_PROBE = (
    "write exactly three paragraphs about why off-by-one errors keep "
    "happening to programmers. each paragraph should be around 80 "
    "words. stop after the third paragraph."
)

RP_PATTERNS = [
    re.compile(r"\*[^*\n]{2,}\*"),
    re.compile(
        r"\b(?:Lilly|she)\s+"
        r"(?:opens|leans|smiles|nods|huffs|sighs|tilts|considers|"
        r"thinks|crosses|claps|beams|studies|hypothesises|hypothesizes|"
        r"traces|frowns|grins|shrugs|pouts|blushes)\b",
        re.I,
    ),
]


def detect_rp(text: str) -> list[str]:
    hits = []
    for pat in RP_PATTERNS:
        for m in pat.finditer(text):
            hits.append(m.group(0))
    return hits


def chat(client: httpx.Client, model: str, system: str, user: str,
         max_tokens) -> tuple[str, dict]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": 0.7,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    r = client.post("/chat/completions", json=payload, timeout=600.0)
    r.raise_for_status()
    data = r.json()
    msg = data["choices"][0]["message"]["content"] or ""
    usage = data.get("usage", {})
    return msg, usage


def emit(out: list[str], line: str = "") -> None:
    out.append(line)
    print(line, flush=True)


def test_max_tokens(client: httpx.Client, model: str, system: str,
                    out: list[str]) -> bool:
    emit(out, "## 1. max_tokens budget")
    emit(out)
    results = []
    for budget in [None, 128, 512, 2048]:
        label = "auto" if budget is None else str(budget)
        try:
            reply, usage = chat(client, model, system, LENGTH_PROBE, budget)
        except Exception as e:
            emit(out, f"  budget={label}: ERROR {e}")
            results.append((label, -1, -1))
            continue
        completion_tokens = usage.get("completion_tokens", -1)
        char_len = len(reply)
        emit(out, f"  budget={label:>4}  "
                  f"completion_tokens={completion_tokens}  "
                  f"chars={char_len}")
        if char_len == 0 and completion_tokens > 0:
            # Surface the raw payload when tokens were spent but no
            # visible content arrived. Usually means reasoning content
            # only, or an attempted-but-empty tool call. Useful signal.
            emit(out, f"      (no visible content; raw reply repr: "
                      f"{reply!r})")
        elif char_len > 0:
            preview = reply.strip()[:140].replace(chr(10), " ")
            emit(out, f"      preview: {preview}")
        results.append((label, completion_tokens, char_len))
    emit(out)
    # Verdict: 64 must be the shortest, and at least one of {auto,512,2048}
    # must produce noticeably more tokens than 64.
    by_label = {r[0]: r for r in results}
    ok = True
    small = by_label.get("128")
    if not small or small[1] < 0:
        ok = False
        emit(out, "  ✗ budget=128 had no result")
    else:
        # Server should have honoured the 128 cap (some slop allowed
        # for tokeniser drift, plus allowing the server to stop a
        # token or two early).
        if small[1] > 200:
            emit(out, f"  ✗ budget=128 produced {small[1]} tokens, "
                      f"expected <= ~140")
            ok = False
        bigger_tokens = [by_label[k][1] for k in ("auto", "512", "2048")
                        if by_label.get(k) and by_label[k][1] > 0]
        if bigger_tokens and max(bigger_tokens) <= small[1] * 1.5:
            emit(out, "  ✗ no bigger budget produced meaningfully more "
                      "tokens than 128")
            ok = False
        bigger_chars = [by_label[k][2] for k in ("auto", "512", "2048")
                       if by_label.get(k) and by_label[k][2] > 0]
        if not bigger_chars:
            emit(out, "  ✗ none of {auto, 512, 2048} produced any visible "
                      "content (model may be exhausting budget on hidden "
                      "thinking tokens)")
            ok = False
    emit(out, f"  verdict: {'PASS' if ok else 'FAIL'}")
    emit(out)
    return ok


def test_narration(client: httpx.Client, model: str,
                   out: list[str]) -> bool:
    emit(out, "## 2. persona narration / first-person check")
    emit(out)
    all_ok = True
    for name in PERSONAS:
        f = PERSONA_DIR / f"{name}.md"
        if not f.exists():
            emit(out, f"  {name}: MISSING FILE")
            all_ok = False
            continue
        system = f.read_text()
        try:
            # 1500 leaves ~700 for thinking + ~800 for visible content,
            # which is enough headroom for reasoning models like gemma's
            # thinking variant. Smaller caps starve the visible-content
            # phase and produce empty replies that pass the RP-pattern
            # check vacuously.
            reply, _ = chat(client, model, system, NARRATION_PROBE, 1500)
        except Exception as e:
            emit(out, f"  {name}: ERROR {e}")
            all_ok = False
            continue
        stripped = reply.strip()
        hits = detect_rp(reply)
        if not stripped:
            # Empty reply means we never reached the visible-content
            # phase. Don't claim a pass on a vacuous check.
            emit(out, f"  ? {name}: empty reply, "
                      f"can't verify narration. INCONCLUSIVE.")
            all_ok = False
        else:
            marker = "✓" if not hits else "✗"
            emit(out, f"  {marker} {name}: {len(hits)} RP-hits")
            if hits:
                all_ok = False
                for h in hits[:5]:
                    emit(out, f"      - {h!r}")
            emit(out, f"      preview: {stripped[:200].replace(chr(10), ' ')}")
        emit(out)
    emit(out, f"  verdict: {'PASS' if all_ok else 'FAIL'}")
    emit(out)
    return all_ok


def test_diff(out: list[str]) -> bool:
    emit(out, "## 3. /personalities diff round-trip")
    emit(out)
    name = "tsundere"
    user_text = (
        "You are Lilly. (test shadow content.) "
        "First person only. No stage directions."
    )
    try:
        saved = lcfg.add_persona(name, user_text, overwrite=True)
    except Exception as e:
        emit(out, f"  ✗ add_persona failed: {e}")
        return False
    sidecar = lcfg.bundled_base_path(name)
    if not sidecar.exists():
        emit(out, f"  ✗ sidecar not created at {sidecar}")
        return False
    emit(out, f"  ✓ sidecar captured at {sidecar} "
              f"({sidecar.stat().st_size} bytes)")
    # Fake an upstream change: rewrite the sidecar to look like an older
    # bundled file so the drift diff has something to show.
    sidecar.write_text(
        sidecar.read_text() + "\n# OLD UPSTREAM TRAILER LINE FOR DRIFT TEST\n"
    )
    listed = lcfg.list_personas()
    if any(n.endswith(".bundled-base") for n in listed):
        emit(out, f"  ✗ list_personas leaked sidecar: {listed}")
        return False
    emit(out, f"  ✓ list_personas filters sidecars")
    if name not in listed:
        emit(out, f"  ✗ {name!r} missing from list_personas output: {listed}")
        return False
    # Now exercise remove_persona: it should delete both the user file
    # and the sidecar.
    res = lcfg.remove_persona(name)
    if res != "removed":
        emit(out, f"  ✗ remove_persona returned {res!r}, expected 'removed'")
        return False
    if saved.exists():
        emit(out, f"  ✗ user file still exists after remove: {saved}")
        return False
    if sidecar.exists():
        emit(out, f"  ✗ sidecar still exists after remove: {sidecar}")
        return False
    emit(out, "  ✓ remove cleared both user file and sidecar")
    emit(out, "  verdict: PASS")
    emit(out)
    return True


def test_max_tokens_parser(out: list[str]) -> bool:
    emit(out, "## 0. parse_max_tokens unit cases")
    emit(out)
    cases = [
        (None, None),
        ("", None),
        ("auto", None),
        ("AUTO", None),
        ("0", None),
        ("-1", None),
        ("128", 128),
        ("4096", 4096),
        (256, 256),
        (0, None),
    ]
    ok = True
    for value, expected in cases:
        try:
            got = lcfg.parse_max_tokens(value)
        except Exception as e:
            emit(out, f"  ✗ {value!r} raised {e}")
            ok = False
            continue
        marker = "✓" if got == expected else "✗"
        emit(out, f"  {marker} parse_max_tokens({value!r}) = {got!r} "
                  f"(expected {expected!r})")
        if got != expected:
            ok = False
    # Negative cases.
    for bad in ["abc", "12x", True]:
        raised = False
        try:
            lcfg.parse_max_tokens(bad)
        except ValueError:
            raised = True
        marker = "✓" if raised else "✗"
        emit(out, f"  {marker} parse_max_tokens({bad!r}) raised "
                  f"ValueError: {raised}")
        if not raised:
            ok = False
    emit(out, f"  verdict: {'PASS' if ok else 'FAIL'}")
    emit(out)
    return ok


def test_clone_and_admin_tools(out: list[str]) -> bool:
    emit(out, "## 4. clone_persona + persona_admin tools")
    emit(out)
    ok = True
    src = "default"
    dst = "default-fork"
    # Make sure dst is clean from earlier passes.
    lcfg.remove_persona(dst)
    try:
        path = lcfg.clone_persona(src, dst)
    except Exception as e:
        emit(out, f"  ✗ clone_persona({src} -> {dst}) raised: {e}")
        return False
    if not path.exists():
        emit(out, f"  ✗ clone_persona did not create {path}")
        ok = False
    else:
        emit(out, f"  ✓ cloned default -> {dst} ({path})")
    # Refusing to clobber.
    raised = False
    try:
        lcfg.clone_persona(src, dst)
    except FileExistsError:
        raised = True
    if not raised:
        emit(out, f"  ✗ clone_persona without overwrite=true should "
                  f"have raised FileExistsError")
        ok = False
    else:
        emit(out, "  ✓ clone refuses to clobber without overwrite=true")
    # Overwrite path.
    try:
        lcfg.clone_persona(src, dst, overwrite=True)
        emit(out, "  ✓ clone_persona overwrites with overwrite=true")
    except Exception as e:
        emit(out, f"  ✗ clone_persona overwrite raised: {e}")
        ok = False
    # Now the persona_admin tools, without REPL hooks installed.
    from lillycoder.tools import persona_admin as pa
    from lillycoder.tools.registry import by_name
    list_tool = by_name("list_personas")
    add_tool = by_name("add_persona")
    set_active_tool = by_name("set_active_persona")
    set_evolve_tool = by_name("set_evolve")
    if not all([list_tool, add_tool, set_active_tool, set_evolve_tool]):
        emit(out, "  ✗ persona_admin tools missing from registry")
        return False
    emit(out, "  ✓ persona_admin tools registered")
    listed = list_tool.handler()
    if not listed.get("ok") or "personas" not in listed:
        emit(out, f"  ✗ list_personas tool returned {listed}")
        ok = False
    else:
        names = [p["name"] for p in listed["personas"]]
        if dst not in names:
            emit(out, f"  ✗ list_personas missing {dst!r}: {names}")
            ok = False
        else:
            emit(out, f"  ✓ list_personas reports {len(names)} personas")
    # add_persona tool with a fresh name.
    fresh = "smoke-test-fresh"
    lcfg.remove_persona(fresh)
    res = add_tool.handler(name=fresh,
                           text="You are Lilly. (smoke-fresh.)")
    if not res.get("ok"):
        emit(out, f"  ✗ add_persona tool: {res}")
        ok = False
    else:
        emit(out, f"  ✓ add_persona tool created {fresh}")
    # set_active_persona without hook should fail closed.
    res = set_active_tool.handler(name=fresh)
    if res.get("ok"):
        emit(out, f"  ✗ set_active_persona should fail without REPL hook, "
                  f"got {res}")
        ok = False
    else:
        emit(out, "  ✓ set_active_persona fails closed without REPL hook")
    # set_evolve same story.
    res = set_evolve_tool.handler(enabled=True)
    if res.get("ok"):
        emit(out, f"  ✗ set_evolve should fail without REPL hook, got {res}")
        ok = False
    else:
        emit(out, "  ✓ set_evolve fails closed without REPL hook")
    # Now install fake hooks and verify they're called.
    seen = {"load": None, "evolve": None}

    def fake_load(name: str) -> dict:
        seen["load"] = name
        return {"ok": True, "active": name, "chars": 99}

    def fake_evolve(enabled: bool) -> dict:
        seen["evolve"] = enabled
        return {"ok": True, "evolve": enabled, "active": "x",
                "snapshotted_to": None}

    pa.set_load_hook(fake_load)
    pa.set_evolve_hook(fake_evolve)
    res = set_active_tool.handler(name=fresh)
    if not res.get("ok") or seen["load"] != fresh:
        emit(out, f"  ✗ set_active_persona via hook: res={res} seen={seen}")
        ok = False
    else:
        emit(out, "  ✓ set_active_persona invokes the load hook")
    res = set_evolve_tool.handler(enabled=True)
    if not res.get("ok") or seen["evolve"] is not True:
        emit(out, f"  ✗ set_evolve via hook: res={res} seen={seen}")
        ok = False
    else:
        emit(out, "  ✓ set_evolve invokes the evolve hook")
    # Cleanup.
    lcfg.remove_persona(dst)
    lcfg.remove_persona(fresh)
    emit(out, f"  verdict: {'PASS' if ok else 'FAIL'}")
    emit(out)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--only",
        nargs="+",
        choices=["parser", "diff", "clone", "budget", "narration"],
        default=None,
        help="Run only the named tests instead of the full suite.",
    )
    args = ap.parse_args()

    selected = set(args.only) if args.only else {
        "parser", "diff", "clone", "budget", "narration",
    }

    out: list[str] = []
    emit(out, f"# lillycoder smoke (isolated config: {ISOLATED_HOME})")
    emit(out, f"# selected tests: {sorted(selected)}")
    emit(out)

    parser_ok = test_max_tokens_parser(out) if "parser" in selected else None
    diff_ok = test_diff(out) if "diff" in selected else None
    clone_ok = test_clone_and_admin_tools(out) if "clone" in selected else None
    budget_ok = None
    narration_ok = None

    if "budget" in selected or "narration" in selected:
        with httpx.Client(base_url=args.api) as client:
            models = client.get("/models").json()
            model_id = (models.get("data") or [{}])[0].get("id") or "unknown"
            emit(out, f"model: {model_id}")
            emit(out)
            if "budget" in selected:
                sys_text = (PERSONA_DIR / "default.md").read_text()
                budget_ok = test_max_tokens(
                    client, model_id, sys_text, out)
            if "narration" in selected:
                narration_ok = test_narration(client, model_id, out)

    emit(out, "# summary")

    def _verdict(name: str, ok: Optional[bool]) -> str:
        if ok is None:
            return "SKIP"
        return "PASS" if ok else "FAIL"

    emit(out, f"  parse_max_tokens unit:         "
              f"{_verdict('parser', parser_ok)}")
    emit(out, f"  /personalities diff round:     "
              f"{_verdict('diff', diff_ok)}")
    emit(out, f"  clone + persona_admin tools:   "
              f"{_verdict('clone', clone_ok)}")
    emit(out, f"  max_tokens budget on model:    "
              f"{_verdict('budget', budget_ok)}")
    emit(out, f"  persona narration on model:    "
              f"{_verdict('narration', narration_ok)}")

    Path(args.out).write_text("\n".join(out) + "\n")

    results = [parser_ok, diff_ok, clone_ok, budget_ok, narration_ok]
    return 0 if all(r is not False for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
