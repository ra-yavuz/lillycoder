"""Bottom-toolbar pinning during model turns.

Today the toolbar (model + ctx + persona + max_tokens + flags) is a
prompt_toolkit bottom_toolbar attached to the input-prompt
PromptSession. It is therefore visible only while the user is editing
the next prompt: the moment they hit enter, prompt_toolkit returns and
streamed assistant content scrolls up the screen with the toolbar
nowhere to be seen.

This module provides an opt-in alternative: while a turn is running,
spin up an async prompt_toolkit Application that owns the screen,
shows only the toolbar at the bottom, and uses patch_stdout() to
route output through it (so writes appear above the toolbar instead
of obliterating it). The model turn itself runs in a worker thread
inside the same event loop. When the turn finishes the Application
exits and we drop back to the normal session.prompt() flow.

Default off: LILLY_TOOLBAR_PIN=1 to enable. We'll consider flipping
the default after a release cycle of field testing. Known-bad
terminals (legacy screen, dumb terminals, no-VT-mode Windows
console) are auto-detected and silently fall back to the legacy
behaviour even when the env var is set."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from typing import Awaitable, Callable, Optional

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import HTML, AnyFormattedText
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.patch_stdout import patch_stdout


ENV_VAR = "LILLY_TOOLBAR_PIN"


def should_pin() -> bool:
    """Decide whether to use the pinned-toolbar runner.

    True only if the user opted in via env var AND the terminal looks
    capable. Returns False on any known-bad combination so the user
    cannot accidentally break their session by setting the flag in a
    weird shell."""
    if os.environ.get(ENV_VAR, "").strip().lower() not in ("1", "yes", "on", "true"):
        return False
    if not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "").lower()
    if term in ("", "dumb", "unknown"):
        return False
    # Nested tmux/screen with no fancy mode: pinning works *most* of
    # the time, but split panes and old screen versions can shift the
    # bottom row out from under us. Conservative: skip these. Users
    # who want it can override by also setting LILLY_TOOLBAR_PIN_FORCE=1.
    if os.environ.get("LILLY_TOOLBAR_PIN_FORCE", "").strip() in ("1", "yes", "on", "true"):
        return True
    if term.startswith("screen"):
        return False
    if os.environ.get("TMUX") and not term.startswith("tmux"):
        return False
    return True


def _build_toolbar_app(get_toolbar: Callable[[], AnyFormattedText]) -> Application:
    """Build an Application whose layout is just a tiny body and a
    bottom toolbar bound to `get_toolbar`. The body window has zero
    height (literally, height=0) so it doesn't paint a black bar
    over content above it. Output from patch_stdout flows into the
    scrollback above this layout."""
    body = Window(FormattedTextControl(""), height=0)
    toolbar = Window(
        FormattedTextControl(lambda: get_toolbar()),
        height=1,
        style="class:bottom-toolbar",
    )
    layout = Layout(HSplit([body, toolbar]))
    return Application(
        layout=layout,
        full_screen=False,
        mouse_support=False,
        enable_page_navigation_bindings=False,
    )


def run_with_pinned_toolbar(
    work: Callable[[], None],
    get_toolbar: Callable[[], AnyFormattedText],
) -> None:
    """Run `work()` (synchronous, expected to block until the turn is
    done) inside a context where prompt_toolkit holds a pinned
    bottom-toolbar Application and patch_stdout routes output above
    it.

    `work` runs on a worker thread; the Application runs on the main
    thread's asyncio loop. KeyboardInterrupt propagates: ctrl+c during
    a turn cancels the worker (best-effort) and ends the Application
    cleanly so the REPL drops back to its prompt.

    Falls back to plain `work()` when should_pin() returns False."""
    if not should_pin():
        work()
        return

    app = _build_toolbar_app(get_toolbar)

    error_holder: dict = {}

    async def _runner() -> None:
        # Start the work in a worker thread.
        loop = asyncio.get_event_loop()

        def _bg() -> None:
            try:
                work()
            except KeyboardInterrupt:
                error_holder["err"] = KeyboardInterrupt()
            except BaseException as e:
                error_holder["err"] = e

        worker = threading.Thread(target=_bg, daemon=True)
        worker.start()

        # Spin up the toolbar Application in parallel. We exit it as
        # soon as the worker finishes.
        async def _await_worker() -> None:
            while worker.is_alive():
                await asyncio.sleep(0.1)
            app.exit()

        watcher = asyncio.ensure_future(_await_worker())
        try:
            with patch_stdout(raw=True):
                await app.run_async()
        finally:
            if not watcher.done():
                watcher.cancel()
            # Make sure the worker is actually done before we return.
            worker.join(timeout=5.0)

    try:
        asyncio.run(_runner())
    except KeyboardInterrupt:
        # Re-raise so the REPL's existing handler can run its cleanup.
        raise

    if "err" in error_holder:
        err = error_holder["err"]
        if isinstance(err, KeyboardInterrupt):
            raise KeyboardInterrupt()
        raise err
