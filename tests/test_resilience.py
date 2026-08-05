"""Hermetic regression tests for discovery, memory, and request recovery."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from lillycoder import agent, discovery, hydra  # noqa: E402
from lillycoder.context import (  # noqa: E402
    ContextTracker,
    memory_from_messages,
    safe_working_window,
    system_with_memory,
)
from lillycoder.endpoint import ModelInfo, _endpoint_key  # noqa: E402
from lillycoder.repl import _persist_assistant_msgs  # noqa: E402
from lillycoder.sessions import (  # noqa: E402
    append_message,
    append_snapshot,
    load_messages,
)
from lillycoder.sink import RecordingSink  # noqa: E402
from lillycoder.toolcheck import tools_for_prompt  # noqa: E402


def test_hydra_bridge_finds_arbitrary_running_port():
    status_payload = {
        "ok": True,
        "rows": [{
            "alias": "custom-coder",
            "state": "running",
            "port": 18137,
        }],
    }
    completed = subprocess.CompletedProcess(
        ["hydra-llm", "status", "--json"], 0,
        stdout=json.dumps(status_payload), stderr="",
    )
    with patch.object(hydra, "executable", return_value="/usr/bin/hydra-llm"), \
            patch.object(hydra.subprocess, "run", return_value=completed) as run:
        probes = hydra.running_probes()
    assert probes == [
        (18137, "/v1/models", "hydra:custom-coder", "data"),
    ]
    assert run.call_args.args[0] == [
        "/usr/bin/hydra-llm", "status", "--json",
    ]


def test_discovery_merges_hydra_ports_with_known_ports():
    def fake_probe(host, port, path, label, timeout_s=1.0):
        if port != 18137:
            return None
        return discovery.Endpoint(
            base_url="http://localhost:18137/v1",
            label=label,
            models=["coder.gguf"],
            raw_url="http://localhost:18137/v1/models",
        )

    with patch.object(hydra, "running_probes", return_value=[
        (18137, "/v1/models", "hydra:custom-coder", "data"),
    ]), patch.object(discovery, "probe_one", side_effect=fake_probe):
        endpoints = discovery.discover()
    assert len(endpoints) == 1
    assert endpoints[0].base_url.endswith(":18137/v1")
    assert endpoints[0].label == "hydra:custom-coder"


def test_loopback_url_spellings_are_same_endpoint():
    first = discovery.Endpoint(
        base_url="http://127.0.0.1:18102/v1", label="saved", models=[],
        raw_url="",
    )
    second = discovery.Endpoint(
        base_url="http://localhost:18102/v1", label="hydra", models=[],
        raw_url="",
    )
    assert _endpoint_key(first) == _endpoint_key(second)


def test_hydra_start_parses_endpoint():
    payload = {"ok": True, "port": 18103, "health": {"state": "ready"}}
    completed = subprocess.CompletedProcess(
        ["hydra-llm", "start", "fern", "--json"], 0,
        stdout=json.dumps(payload), stderr="",
    )
    with patch.object(hydra, "executable", return_value="hydra-llm"), \
            patch.object(hydra.subprocess, "run", return_value=completed):
        started = hydra.start("fern")
    assert started["base_url"] == "http://127.0.0.1:18103/v1"


class _FailingSummaryClient:
    def post(self, *args, **kwargs):
        raise httpx.ConnectError("model unavailable")


def test_compaction_has_local_fallback_and_one_system_message():
    messages = [{"role": "system", "content": "persona"}]
    for index in range(5):
        messages.extend([
            {"role": "user", "content": f"goal {index}: preserve decision-{index}"},
            {"role": "assistant", "content": f"handled decision-{index}"},
        ])
    tracker = ContextTracker(model_window=4096)
    model = type("Model", (), {"alias": "fake"})()
    changed = tracker.compact(
        messages, "persona", _FailingSummaryClient(), model,
        keep_last_turns=2,
    )
    assert changed
    assert sum(m.get("role") == "system" for m in messages) == 1
    assert memory_from_messages(messages)
    assert tracker.last_compaction_used_fallback
    assert any(m.get("content", "").startswith("goal 3") for m in messages)
    tracker.replace_system_prompt(messages, "new persona")
    assert messages[0]["content"].startswith("new persona")
    assert memory_from_messages(messages)


def test_tool_payloads_are_bounded_inside_single_long_turn():
    huge = "x" * 30000
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "inspect the project"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": "big.py", "content": huge}),
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": json.dumps({"ok": True, "content": huge}),
        },
    ]
    tracker = ContextTracker(model_window=8192)
    model = type("Model", (), {"alias": "fake"})()
    assert tracker.compact(
        messages, "persona", client=None, model=model, use_model=False,
    )
    assert len(messages[2]["tool_calls"][0]["function"]["arguments"]) < 2600
    assert len(messages[3]["content"]) < 6500


def test_prompt_tool_routing_keeps_normal_menu_focused():
    normal = tools_for_prompt("implement the parser and run its tests")
    assert "read_file" in normal and "edit_file" in normal and "bash" in normal
    assert "pkg_install" not in normal and "set_persona" not in normal
    package = tools_for_prompt("install the missing npm dependency")
    assert "pkg_install" in package
    persona = tools_for_prompt("create a sweet persona and switch to it")
    assert "add_persona" in persona and "set_active_persona" in persona


def test_request_estimate_includes_tool_schemas():
    tracker = ContextTracker(model_window=1000)
    messages = [{"role": "system", "content": "short"}]
    tools = [{"function": {"description": "x" * 3500}}]
    assert tracker.needs_compaction(messages, tools=tools)


def test_huge_server_context_is_bounded_for_local_working_memory():
    assert safe_working_window(262144) == 32768
    assert safe_working_window(16384) == 16384
    assert safe_working_window(None) == 8192


def test_snapshot_restores_bounded_working_set_and_newer_turns():
    root = Path(tempfile.mkdtemp())
    path = root / "session.jsonl"
    append_message(path, "user", "very old request")
    append_message(path, "assistant", "very old answer")
    snapshot_messages = [
        {
            "role": "system",
            "content": system_with_memory("persona", "remember project alpha"),
        },
        {"role": "user", "content": "recent request"},
        {"role": "assistant", "content": "recent answer"},
    ]
    append_snapshot(path, snapshot_messages)
    append_message(path, "user", "new request")
    append_message(path, "assistant", "new answer")

    loaded = load_messages(path, "updated persona")
    joined = "\n".join(str(m.get("content") or "") for m in loaded)
    assert "very old request" not in joined
    assert "recent request" in joined and "new request" in joined
    assert loaded[0]["content"].startswith("updated persona")
    assert "remember project alpha" in loaded[0]["content"]


def test_failed_turn_does_not_duplicate_previous_assistant():
    path = Path(tempfile.mkdtemp()) / "session.jsonl"
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new but failed"},
    ]
    _persist_assistant_msgs(path, messages)
    assert not path.exists()
    messages.append({"role": "assistant", "content": "new answer"})
    _persist_assistant_msgs(path, messages)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records == [{"role": "assistant", "content": "new answer"}]


def test_http_error_is_raised_instead_of_becoming_empty_reply():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"error": {"message": "KV cache out of memory"}},
            request=request,
        )

    endpoint = discovery.Endpoint(
        base_url="http://test/v1", label="test", models=["model"],
        raw_url="http://test/v1/models",
    )
    model = ModelInfo(alias="model", endpoint=endpoint, context_window=8192)
    client = httpx.Client(
        base_url="http://test/v1", transport=httpx.MockTransport(handler),
    )
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "hello"},
    ]
    try:
        asyncio.run(agent._stream_one_completion(
            client, model, messages, RecordingSink(),
        ))
        raise AssertionError("expected ModelRequestError")
    except agent.ModelRequestError as exc:
        assert exc.status_code == 500
        assert exc.oom_related
    finally:
        client.close()


def test_explicit_output_cap_cannot_overflow_remaining_context():
    endpoint = discovery.Endpoint(
        base_url="http://test/v1", label="test", models=["model"],
        raw_url="http://test/v1/models",
    )
    model = ModelInfo(alias="model", endpoint=endpoint, context_window=1000)
    messages = [{"role": "user", "content": "x" * 3000}]
    budget = agent._resolve_max_tokens(model, messages, setting=900, tools=[])
    assert 32 <= budget < 300


def _run() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL {test.__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
