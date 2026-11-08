from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from saidcue.listening_indicator import (  # noqa: E402
    POSITIONS,
    ListeningIndicator,
    compute_position,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class FakeEngine:
    def __init__(self, active=True, running=True):
        self.is_active = active
        self.is_running = running
        self.input_level = 0.03
        self.is_transcribing = False


SCREEN = QRect(0, 0, 1920, 1040)  # available geometry (taskbar excluded)
SIZE = (216, 88)  # window = pill + shadow margin


@pytest.mark.parametrize(
    "position, expected",
    [
        ("top-center", ((1920 - 216) // 2, -4)),
        ("top-left", (-4, -4)),
        ("top-right", (1920 - 216 + 4, -4)),
        ("bottom-center", ((1920 - 216) // 2, 1040 - 88 + 4)),
        ("bottom-left", (-4, 1040 - 88 + 4)),
        ("bottom-right", (1920 - 216 + 4, 1040 - 88 + 4)),
    ],
)
def test_compute_position_places_pill_18px_from_edges(position, expected):
    # Shadow margin is 22 px, so the window is offset by 18 - 22 = -4 px.
    assert compute_position(position, SCREEN, SIZE, margin=18) == expected


def test_compute_position_respects_screen_offset():
    second = QRect(1920, 0, 1280, 1000)
    x, _ = compute_position("top-left", second, SIZE, margin=18)
    assert x == 1920 - 4


def test_unknown_position_falls_back_to_top_center():
    assert compute_position("middle", SCREEN, SIZE) == compute_position(
        "top-center", SCREEN, SIZE
    )


def test_window_never_takes_focus_or_input(qapp):
    ind = ListeningIndicator()
    flags = ind.windowFlags()
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert flags & Qt.WindowType.WindowTransparentForInput
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert ind.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_always_mode_shows_while_the_engine_runs(qapp):
    """The capsule doubles as "the app is running and listening"."""

    ind = ListeningIndicator()
    engine = FakeEngine(active=False)
    ind.attach(engine, mode="always")
    assert ind.is_shown and ind.isVisible()

    # Typing is off, but the mic is open: still visible.
    engine.is_active = False
    ind._tick()
    assert ind.is_shown


def test_dictating_mode_shows_only_while_typing(qapp):
    ind = ListeningIndicator()
    engine = FakeEngine(active=True)
    ind.attach(engine, mode="dictating")
    assert ind.is_shown and ind.isVisible()

    engine.is_active = False
    ind._tick()
    assert not ind.is_shown


def test_idle_capsule_shrinks_to_the_mic_alone(qapp):
    """Listening without dictating: a small red mic, nothing else."""

    ind = ListeningIndicator()
    engine = FakeEngine(active=False)
    ind.attach(engine, mode="always")
    assert ind.is_shown
    assert ind._idle is True
    # The pill animates towards the minimal width (just the badge).
    for _ in range(40):
        ind._tick()
    assert ind._pill_w == pytest.approx(44, abs=1)


def test_dictating_capsule_keeps_the_full_width(qapp):
    ind = ListeningIndicator()
    engine = FakeEngine(active=True)
    ind.attach(engine, mode="always")
    assert ind._idle is False
    for _ in range(40):
        ind._tick()
    assert ind._pill_w == pytest.approx(172, abs=1)


def test_the_capsule_grows_again_when_dictation_starts(qapp):
    ind = ListeningIndicator()
    engine = FakeEngine(active=False)
    ind.attach(engine, mode="always")
    for _ in range(40):
        ind._tick()
    assert ind._pill_w == pytest.approx(44, abs=1)

    engine.is_active = True
    for _ in range(40):
        ind._tick()
    assert ind._idle is False
    assert ind._pill_w == pytest.approx(172, abs=1)


def test_never_mode_hides_the_capsule_even_for_feedback(qapp):
    ind = ListeningIndicator()
    ind.attach(FakeEngine(active=True), mode="never")
    assert not ind.is_shown
    ind.flash("Tab: servidor", "ok")
    assert not ind.is_shown


def test_unknown_mode_falls_back_to_always(qapp):
    ind = ListeningIndicator()
    ind.attach(FakeEngine(active=False), mode="nonsense")
    assert ind.mode == "always"
    assert ind.is_shown


def test_set_mode_switches_the_behaviour_at_runtime(qapp):
    ind = ListeningIndicator()
    engine = FakeEngine(active=False)
    ind.attach(engine, mode="dictating")
    assert not ind.is_shown
    ind.set_mode("always")
    assert ind.is_shown


def test_hidden_when_engine_not_running_or_detached(qapp):
    ind = ListeningIndicator()
    ind.attach(FakeEngine(active=True, running=False), mode="always")
    assert not ind.is_shown
    ind.attach(FakeEngine(active=True), mode="always")
    assert ind.is_shown
    ind.detach()
    assert not ind.is_shown


def test_attach_applies_position(qapp):
    ind = ListeningIndicator()
    ind.attach(FakeEngine(active=False), position="bottom-right")
    assert ind.position == "bottom-right"
    ind.set_position("nonsense")
    assert ind.position == "top-center"
    assert set(POSITIONS) >= {"top-center", "bottom-right"}
