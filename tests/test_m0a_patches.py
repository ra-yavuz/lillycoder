"""Unit tests for the M0a Tier-A patches: output sink, tool-subset, no-action
heuristic, and the workdir path-fix. These run with NO model (fast, hermetic).

Run: PYTHONPATH=lib python3 tests/test_m0a_patches.py   (exits 0 on success)
or with pytest if available.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from lillycoder.sink import RecordingSink, ConsoleSink, as_sink, _plain  # noqa: E402
from lillycoder.tools.registry import schemas_for_model  # noqa: E402
from lillycoder import agent  # noqa: E402


def test_recording_sink_strips_markup():
    s = RecordingSink()
    s.print("[cyan]   ⏳ write_file[/cyan]", end="")
    assert s.lines[-1] == "   ⏳ write_file", s.lines[-1]


def test_recording_sink_renders_panel_as_text():
    from rich.panel import Panel
    s = RecordingSink()
    s.print(Panel("hello body", title="about to write"))
    joined = "\n".join(s.lines)
    assert "hello body" in joined
    assert "rich.panel" not in joined  # no object repr leaked


def test_recording_sink_records_structured_events():
    s = RecordingSink()
    s.on_tool_call("write_file", {"path": "x.py", "content": "..."})
    s.on_tool_result("write_file", True, {"ok": True})
    s.on_token("hello", is_thought=False)
    s.on_token("(think)", is_thought=True)
    s.on_turn_end(stopped_reason=None)
    kinds = [e["kind"] for e in s.events]
    assert kinds == ["tool_call", "tool_result", "token", "token", "turn_end"]
    assert s.text == "hello"  # thought tokens excluded from .text


def test_console_coerces_to_sink_with_hooks():
    from rich.console import Console
    s = as_sink(Console())
    assert hasattr(s, "on_tool_call") and hasattr(s, "on_token")
    # hooks are no-ops, must not raise
    s.on_tool_call("read_file", {"path": "a"})
    s.on_turn_end()


def test_tool_subset_filters_schemas():
    full = schemas_for_model()
    subset = schemas_for_model(["read_file", "write_file"])
    names = {t["function"]["name"] for t in subset}
    assert names == {"read_file", "write_file"}, names
    assert len(subset) < len(full)


def test_tool_subset_typo_falls_back_to_all():
    # An unknown-only subset must not leave the model toolless.
    res = schemas_for_model(["does_not_exist"])
    assert len(res) == len(schemas_for_model())


def test_looks_complete_distinguishes_done_from_narration():
    assert agent._looks_complete("done. wrote x.py ✨") is True
    assert agent._looks_complete("i'll read the file first") is False
    assert agent._looks_complete("Let me create the file") is False
    assert agent._looks_complete("") is False


def test_in_workdir_resolves_relative_to_workdir(tmp_path=None):
    import tempfile, os
    base = tempfile.mkdtemp()
    sub = os.path.join(base, "ws")
    os.makedirs(sub, exist_ok=True)
    start = os.getcwd()
    with agent._in_workdir(Path(sub)):
        # inside the context, a relative path resolves under the workspace
        assert Path("foo.txt").resolve() == Path(sub).resolve() / "foo.txt"
    # restored afterward
    assert os.getcwd() == start


def test_read_file_windows_large_files():
    # A large file must come back as a bounded window with a how-to-proceed
    # hint, never whole: a 200KB read into a 16K context destroyed agent
    # turns (observed live against a 4,500-line file: the model re-read the
    # whole file every repair round and never got to edit).
    import tempfile
    from lillycoder.tools.read import _handler, DEFAULT_MAX_BYTES
    big = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
    for i in range(1, 3001):
        big.write(f"line_{i} = {i}\n")
    big.close()
    r = _handler(big.name)
    assert r["ok"] and r["truncated"], r
    assert len(r["content"].encode()) <= DEFAULT_MAX_BYTES, len(r["content"])
    assert "grep" in r["hint"] and "start_line" in r["hint"], r["hint"]
    assert r["total_lines"] == 3000, r["total_lines"]
    # a window lands exactly where asked
    w = _handler(big.name, start_line=2000, max_lines=5)
    assert w["content"].startswith("line_2000"), w["content"][:40]
    assert w["truncated"], "a partial view must say so"
    # small files are unaffected
    small = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
    small.write("x = 1\n")
    small.close()
    s = _handler(small.name)
    assert s["ok"] and not s["truncated"], s


def test_repeat_guard_suppresses_read_loops_but_not_verification():
    # Identical read-only calls within a turn are answered with a stub
    # (observed live: a worker re-read the same 400-line region three times
    # and died of context bloat instead of editing). A mutating call resets
    # the guard so the edit-then-re-read verification pattern keeps working.
    from lillycoder.agent import RepeatGuard
    g = RepeatGuard()
    assert g.is_repeat("read_file", {"path": "a.py"}, mutating=False) is False
    assert g.is_repeat("read_file", {"path": "a.py"}, mutating=False) is True
    assert g.is_repeat("read_file", {"path": "b.py"}, mutating=False) is False
    # different args are not a repeat
    assert g.is_repeat("read_file", {"path": "a.py", "start_line": 50},
                       mutating=False) is False
    # an edit resets: re-reading the same file afterwards is legitimate
    assert g.is_repeat("edit_file", {"path": "a.py"}, mutating=True) is False
    assert g.is_repeat("read_file", {"path": "a.py"}, mutating=False) is False
    stub = g.stub_result()
    assert stub["ok"] and stub["repeat_suppressed"] and "edit_file" in stub["note"]


def test_bash_deny_policy_blocks_matching_commands():
    # An embedder (e.g. an orchestrator) can forbid bash commands by regex
    # without touching the always-on safety classifier or affecting the
    # standalone REPL (which passes no deny list).
    import re
    from lillycoder.agent import _gate_tool_call
    from pathlib import Path
    deny = [re.compile(r"\bmake\b"), re.compile(r"\bpip\s+install\b")]
    # matching commands are refused with a policy reason
    ok, reason = _gate_tool_call(None, "bash", {"cmd": "make venv"},
                                 bypass_perms=True, workdir=Path("."),
                                 bash_deny=deny)
    assert not ok and "policy" in reason, (ok, reason)
    ok2, reason2 = _gate_tool_call(None, "bash", {"cmd": "pip install foo"},
                                   bypass_perms=True, workdir=Path("."),
                                   bash_deny=deny)
    assert not ok2 and "policy" in reason2, (ok2, reason2)
    # a non-matching command is NOT blocked by the policy layer (it may still
    # hit the permission prompt, but the policy gate lets it through)
    ok3, reason3 = _gate_tool_call(None, "bash", {"cmd": "python3 -m unittest"},
                                   bypass_perms=True, workdir=Path("."),
                                   bash_deny=deny)
    assert ok3, (ok3, reason3)
    # no deny list (standalone REPL) = no policy denial
    ok4, _ = _gate_tool_call(None, "bash", {"cmd": "make venv"},
                             bypass_perms=True, workdir=Path("."))
    assert ok4, "standalone REPL must be unaffected by the policy layer"


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
