"""Tiny carriage-return spinner that plays nicely with prompt_toolkit's
patch_stdout(). Rich's console.status() uses rich.live.Live which moves
the cursor with ANSI escapes; under patch_stdout that fights for control
of the screen and flickers. This spinner only writes plain text to
stdout, and clears itself on stop.

Usage:
    with Spinner("lilly is thinking..."):
        do_work()

Safe to nest? No. Only one at a time.
"""
from __future__ import annotations

import sys
import threading
import time

_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_INTERVAL = 0.08


class Spinner:
    def __init__(self, label: str, stream=None):
        self.label = label
        self.stream = stream or sys.stdout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_len = 0

    def _draw(self, frame: str) -> None:
        line = f"\r{frame} {self.label}"
        self.stream.write(line)
        self.stream.flush()
        self._last_len = max(self._last_len, len(line))

    def _clear(self) -> None:
        if self._last_len:
            self.stream.write("\r" + " " * self._last_len + "\r")
            self.stream.flush()

    def _run(self) -> None:
        i = 0
        while not self._stop.is_set():
            self._draw(_FRAMES[i % len(_FRAMES)])
            i += 1
            self._stop.wait(_INTERVAL)
        self._clear()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=0.5)
        self._thread = None

    def __enter__(self) -> "Spinner":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
