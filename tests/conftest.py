from __future__ import annotations

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

for path in (PROJECT_ROOT, SRC_ROOT):
    as_str = str(path)
    if as_str not in sys.path:
        sys.path.insert(0, as_str)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_desktop_input(monkeypatch):
    """Voice-command tests must never drive the real keyboard, mouse,
    windows or Start menu: a forgotten fake would press keys and click on
    the developer's desktop. Any attempt fails the test instead."""

    attempts = []

    def refuse(what):
        def _raise(*_a, **_k):
            # Recorded as well as raised: the action runner turns
            # exceptions into failed results, which a test might accept.
            attempts.append(what)
            raise AssertionError(f"test tried to use the real {what}")

        return _raise

    import saidcue.voice_commands.actions as actions
    import saidcue.voice_commands.apps as apps
    import saidcue.voice_commands.mouse as mouse
    import saidcue.voice_commands.screenshot as screenshot
    import saidcue.voice_commands.windows as windows

    monkeypatch.setattr(actions, "_default_keyboard", refuse("keyboard"))
    monkeypatch.setattr(actions, "_default_launcher", refuse("launcher"))
    monkeypatch.setattr(mouse.Win32Mouse, "__init__", refuse("mouse"))
    monkeypatch.setattr(apps, "_read_start_apps", refuse("Start menu list"))
    monkeypatch.setattr(apps, "_default_launch", refuse("app launcher"))
    monkeypatch.setattr(screenshot, "_grab_bgra", refuse("screen capture"))
    for op in ("focus", "minimize", "maximize", "restore", "close"):
        monkeypatch.setattr(windows.Win32Windows, op, refuse(f"window {op}"))

    yield
    assert not attempts, f"test used the real desktop: {attempts}"
