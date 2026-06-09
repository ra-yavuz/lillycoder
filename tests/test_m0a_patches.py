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
