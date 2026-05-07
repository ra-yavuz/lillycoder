"""Smoke test for the per-folder session manager.

Exercises:
  - SessionStore.ensure() in a fresh tmp dir creates .lillycoder/sessions/
  - legacy .lillycoder/history.jsonl gets migrated to a sessions file
  - .git/info/exclude gets the **/.lillycoder/ line when cwd is in a
    git repo, exactly once
  - new(), list(), set_active(), resolve() round-trip
  - load_messages + append_message round-trip
  - project_label() picks the right name from a repo root

Pure Python, no LLM. Runs in or out of the container.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from lillycoder.sessions import (
    SessionStore,
    append_message,
    load_messages,
)


def main() -> int:
    failures = 0

    # --- case 1: fresh dir, no git ---
    fresh = Path(tempfile.mkdtemp(prefix="lilly-sess-fresh-"))
    store = SessionStore(fresh)
    store.ensure()
    assert (fresh / ".lillycoder" / "sessions").is_dir(), "sessions dir not created"
    assert store.git_excluded_message() is None, (
        "should NOT touch git outside a repo"
    )
    print("✓ fresh dir: .lillycoder/sessions/ created, no git change")

    # --- case 2: legacy history.jsonl migrates to sessions/ ---
    legacy_dir = Path(tempfile.mkdtemp(prefix="lilly-sess-legacy-"))
    legacy = legacy_dir / ".lillycoder" / "history.jsonl"
    legacy.parent.mkdir()
    legacy.write_text(
        json.dumps({"role": "user", "content": "old hi"}) + "\n"
        + json.dumps({"role": "assistant", "content": "old hello"}) + "\n"
    )
    store2 = SessionStore(legacy_dir)
    store2.ensure()
    assert not legacy.exists(), "legacy file should be moved"
    migrated = list((legacy_dir / ".lillycoder" / "sessions").glob("*-legacy.jsonl"))
    assert len(migrated) == 1, f"expected 1 migrated file, got {migrated}"
    state = json.loads((legacy_dir / ".lillycoder" / "state.json").read_text())
    assert state.get("active") == migrated[0].name, "migrated file not active"
    print(f"✓ legacy migration: {migrated[0].name}")
    msgs = load_messages(store2.active(), "system test")
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "old hi"}
    assert msgs[2] == {"role": "assistant", "content": "old hello"}
    print("✓ load_messages reads migrated session")

    # --- case 3: git repo gets .git/info/exclude line, once only ---
    git_dir = Path(tempfile.mkdtemp(prefix="lilly-sess-git-"))
    subprocess.run(
        ["git", "init", "-q", str(git_dir)],
        check=True,
        cwd=git_dir,
        env={"GIT_TERMINAL_PROMPT": "0", **{
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        }},
    )
    subdir = git_dir / "deep" / "nested"
    subdir.mkdir(parents=True)
    store3 = SessionStore(subdir)
    store3.ensure()
    msg = store3.git_excluded_message()
    assert msg is not None, "should announce a git exclude write"
    print(f"✓ git repo subdir: {msg}")
    excl = (git_dir / ".git" / "info" / "exclude").read_text()
    assert "**/.lillycoder/" in excl, "exclude line not added"
    # Re-init: should NOT add again.
    store4 = SessionStore(subdir)
    store4.ensure()
    assert store4.git_excluded_message() is None, (
        "second init should not re-add the line"
    )
    excl2 = (git_dir / ".git" / "info" / "exclude").read_text()
    assert excl == excl2, "second init modified the file"
    print("✓ exclude line is idempotent")

    # --- case 4: new/list/load round-trip ---
    rt = Path(tempfile.mkdtemp(prefix="lilly-sess-rt-"))
    s = SessionStore(rt)
    s.ensure()
    p1 = s.new(label="first")
    append_message(p1, "user", "in first")
    p2 = s.new(label="second")
    append_message(p2, "user", "in second")
    listing = s.list()
    assert len(listing) == 2, f"expected 2 sessions, got {len(listing)}"
    assert listing[0].is_active, "newest should be active by default"
    assert listing[0].label.startswith("second"), f"newest label: {listing[0].label}"
    print(f"✓ list shows {[s2.label for s2 in listing]}")
    # Resolve by index, by label, by id.
    assert s.resolve("1") == p2, "index 1 = newest"
    assert s.resolve("first") == p1, "label 'first' resolves"
    assert s.resolve(p1.stem) == p1, "id resolves"
    assert s.resolve("nope") is None, "nonexistent returns None"
    print("✓ resolve handles index, label, id, miss")
    # Switch active and verify load_messages.
    s.set_active(p1)
    msgs = load_messages(s.active(), "sys")
    assert msgs[1] == {"role": "user", "content": "in first"}
    print("✓ set_active + load_messages round-trip")

    # --- case 5: project_label ---
    label = s.project_label()
    assert label.startswith(rt.name), f"unexpected project label: {label}"
    label_git = SessionStore(subdir).project_label()
    assert label_git.startswith(git_dir.name), f"git label: {label_git}"
    assert "/" in label_git, "git label should include subdir path"
    print(f"✓ project_label: rt={label!r} git={label_git!r}")

    # cleanup
    for d in (fresh, legacy_dir, git_dir, rt):
        shutil.rmtree(d, ignore_errors=True)

    print()
    print(f"{'PASS' if failures == 0 else 'FAIL'}: sessions smoke")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
