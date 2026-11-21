"""Number labels: "show numbers", then say a number to click it."""

from __future__ import annotations

import os
from typing import List

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from saidcue.voice_commands.actions import ActionRunner
from saidcue.voice_commands.config import VoiceCommandsConfig
from saidcue.voice_commands.elements import (
    Candidate,
    ElementHit,
    Target,
    best_element,
    tied_elements,
)
from saidcue.voice_commands.modes import LabelsMode, parse_label_choice
from saidcue.voice_commands.service import VoiceCommandService
from saidcue.voice_commands.spoken_keys import parse_number

from test_computer_control import NOTEPAD, FakeKeyboard, FakeMouse, FakeWindows


def target(name, i, kind="button"):
    return Target(name, kind, (10 * i, 20, 30, 12), (i,), True)


class LabelElements:
    """Fake UI Automation with targets, ties and clicks recorded."""

    def __init__(self, targets):
        self.targets = list(targets)
        self.activated: List[tuple] = []
        self.scans = 0

    def list_targets(self, hwnd):
        self.scans += 1
        return list(self.targets)

    def activate_target(self, hwnd, t, button="left"):
        self.activated.append((t.name, button))
        return "invoke"

    def click(self, hwnd, queries, exact=False, button="left", allow_ties=False):
        cands = [Candidate(t.name, t.kind, True) for t in self.targets]
        if allow_ties:
            tied = tied_elements(cands, queries, exact)
            if len(tied) > 1:
                return ElementHit(
                    cands[tied[0]].name, "button", tuple(self.targets[i] for i in tied)
                )
        idx = best_element(cands, queries, exact)
        if idx is None:
            return None
        self.activated.append((cands[idx].name, button))
        return ElementHit(cands[idx].name, cands[idx].kind)


class FakeOverlay:
    def __init__(self):
        self.labels: List[list] = []
        self.hidden = 0

    def show_labels(self, items):
        self.labels.append(list(items))

    def hide(self):
        self.hidden += 1


def make(targets, **cfg):
    elements = LabelElements(targets)
    runner = ActionRunner(
        keyboard_factory=FakeKeyboard,
        windows=FakeWindows([NOTEPAD], foreground=NOTEPAD),
        sleep=lambda _s: None,
        elements_factory=lambda: elements,
        mouse_factory=FakeMouse,
    )
    feedback: List[tuple] = []
    svc = VoiceCommandService(
        VoiceCommandsConfig.from_dict({"enabled": True, **cfg}),
        runner=runner,
        engine_getter=lambda: None,
        on_feedback=lambda t, k: feedback.append((t, k)),
    )
    overlay = FakeOverlay()
    svc.set_screen_overlay(overlay)
    return svc, elements, overlay, feedback


@pytest.fixture(autouse=True)
def no_refresh_delay(monkeypatch):
    monkeypatch.setattr(LabelsMode, "REFRESH_DELAY", 0.0)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("12", ("left", 12)),
        ("twelve", ("left", 12)),
        ("click 12", ("left", 12)),
        ("Click 12.", ("left", 12)),
        ("right click 3", ("right", 3)),
        ("double click forty two", ("double", 42)),
        ("number 7", ("left", 7)),
        ("click on number 7", ("left", 7)),
        ("hello", None),
        ("click save", None),
        ("500", None),  # more than there are labels
    ],
)
def test_parse_label_choice(spoken, expected):
    assert parse_label_choice(spoken, 50) == expected


def test_parse_number():
    assert parse_number("forty two") == 42
    assert parse_number("one hundred five") == 105
    assert parse_number("treinta y dos") == 32
    assert parse_number("five six") is None
    assert parse_number("0") is None


# ---------------------------------------------------------------------------
# Through the service
# ---------------------------------------------------------------------------


def test_show_numbers_then_a_bare_number_clicks_it():
    svc, elements, overlay, feedback = make([target("Save", 1), target("", 2)])
    assert svc.handle("computer, show numbers", False)
    assert isinstance(svc.mode, LabelsMode)
    assert overlay.labels[-1] == [("1", (10, 20, 30, 12)), ("2", (20, 20, 30, 12))]
    # No wake word needed while the labels are up; unnamed icons work too.
    assert svc.handle("2", False)
    assert elements.activated == [("", "left")]
    # Sticky: still open, redrawn after the click.
    assert isinstance(svc.mode, LabelsMode)
    assert elements.scans == 2
    assert svc.handle("right click 1", False)
    assert elements.activated[-1] == ("Save", "right")


def test_ordinary_speech_passes_through_while_labels_are_up():
    svc, elements, _overlay, _fb = make([target("Save", 1)])
    svc.handle("computer, show numbers", False)
    assert not svc.handle("this is just dictation", True)
    assert elements.activated == []
    assert isinstance(svc.mode, LabelsMode)


def test_hide_numbers_closes_the_labels():
    svc, _elements, overlay, _fb = make([target("Save", 1)])
    svc.handle("computer, show numbers", False)
    assert svc.handle("hide numbers", False)
    assert svc.mode is None and overlay.hidden == 1


def test_a_wake_word_command_closes_the_labels():
    svc, _elements, overlay, _fb = make([target("Save", 1)])
    svc.handle("computer, show numbers", False)
    svc.handle("computer, scroll down", False)
    assert svc.mode is None and overlay.hidden == 1


def test_focusing_a_text_box_closes_the_labels():
    svc, elements, _overlay, _fb = make([target("Search", 1, kind="input")])
    svc.handle("computer, show numbers", False)
    svc.handle("1", False)
    assert elements.activated == [("Search", "left")]
    assert svc.mode is None  # typing comes next


def test_out_of_range_number_is_reported():
    svc, elements, _overlay, feedback = make([target("Save", 1), target("Open", 2)])
    svc.handle("computer, show numbers", False)
    # "9" can't be a label here, so it isn't taken as an answer at all.
    assert not svc.handle("9", False)
    assert svc.handle("computer, number 9", False) is True
    assert elements.activated == []


def test_labels_time_out(monkeypatch):
    svc, elements, _overlay, _fb = make([target("Save", 1)])
    svc.handle("computer, show numbers", False)
    mode = svc.mode
    mode._deadline = 0.0
    assert not svc.handle("1", False)
    assert svc.mode is None and elements.activated == []


def test_click_with_same_named_buttons_shows_labels_on_just_those():
    targets = [target("Reply", 1), target("Like", 2), target("Reply", 3)]
    svc, elements, overlay, feedback = make(targets)
    assert svc.handle("computer, click reply", False)
    assert elements.activated == []  # nothing clicked yet
    assert [t for t, _r in overlay.labels[-1]] == ["1", "2"]
    assert "say a number" in feedback[-1][0]
    assert svc.handle("2", False)
    assert elements.activated == [("Reply", "left")]
    assert svc.mode is None  # a one-off choice


def test_a_unique_label_is_clicked_directly():
    svc, elements, _overlay, _fb = make([target("Reply", 1), target("Like", 2)])
    svc.handle("computer, click like", False)
    assert elements.activated == [("Like", "left")]
    assert svc.mode is None


def test_skip_ad_never_asks():
    targets = [target("Skip", 1), target("Skip", 2)]
    svc, elements, _overlay, _fb = make(targets)
    svc.handle("computer, skip ad", False)
    assert elements.activated == [("Skip", "left")]


# ---------------------------------------------------------------------------
# The real overlay widget (offscreen Qt)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_screen_overlay_draws_labels_on_the_right_monitor(qapp):
    from saidcue.voice_commands.screen_overlay import ScreenOverlay

    overlay = ScreenOverlay()
    try:
        overlay._on_labels([("1", (100, 120, 40, 20)), ("2", (-50000, 0, 10, 10))])
        canvas = overlay._canvases[0]
        geo = canvas.geometry()
        # Offscreen Qt has scale 1, so local = physical - screen origin.
        assert [t for t, _r in canvas.labels] == ["1"]
        assert canvas.labels[0][1].topLeft().x() == 100 - geo.left()
        assert canvas.isVisible()
        overlay._on_grid((0, 0, 300, 300))
        assert canvas.labels == [] and canvas.grid is not None
        canvas.grab()  # paints without errors
        overlay._on_hide()
        assert not canvas.isVisible()
    finally:
        overlay.close()
