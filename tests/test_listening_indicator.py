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


def test_shows_while_typing_active_and_hides_when_off(qapp):
    ind = ListeningIndicator()
    engine = FakeEngine(active=True)
    ind.attach(engine)
    assert ind.is_shown and ind.isVisible()

    engine.is_active = False
    ind._tick()
    assert not ind.is_shown


def test_hidden_when_disabled_by_setting(qapp):
    ind = ListeningIndicator()
    ind.attach(FakeEngine(active=True), enabled=False)
    assert not ind.is_shown
    ind.set_enabled(True)
    assert ind.is_shown


def test_hidden_when_engine_not_running_or_detached(qapp):
    ind = ListeningIndicator()
    ind.attach(FakeEngine(active=True, running=False))
    assert not ind.is_shown
    ind.attach(FakeEngine(active=True))
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
