"""The mouse grid: zoom with numbers, then click / move / drag."""

from __future__ import annotations

from typing import List

import pytest

from saidcue.voice_commands.actions import ActionRunner
from saidcue.voice_commands.config import VoiceCommandsConfig
from saidcue.voice_commands.mouse_grid import GridMode, GridState, parse_grid
from saidcue.voice_commands.service import VoiceCommandService

from test_computer_control import NOTEPAD, FakeKeyboard, FakeMouse, FakeWindows

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_cells_split_the_rect_in_reading_order():
    g = GridState((0, 0, 900, 600))
    assert g.cell(1) == (0, 0, 300, 200)
    assert g.cell(5) == (300, 200, 300, 200)
    assert g.cell(9) == (600, 400, 300, 200)
    assert g.center() == (450, 300)


def test_zoom_and_back():
    g = GridState((100, 0, 900, 900))
    g.zoom(9)
    assert g.rect == (700, 600, 300, 300)
    g.zoom(1)
    assert g.rect == (700, 600, 100, 100)
    assert g.center() == (750, 650)
    assert g.back() and g.rect == (700, 600, 300, 300)
    assert g.back() and g.rect == (100, 0, 900, 900)
    assert not g.back()


def test_zooming_stops_at_a_few_pixels():
    g = GridState((0, 0, 30, 30))
    assert g.can_zoom()
    g.zoom(5)
    assert not g.can_zoom()


def test_bad_cell():
    with pytest.raises(ValueError):
        GridState((0, 0, 9, 9)).cell(0)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spoken, parsed",
    [
        ("5", ("zoom", [5])),
        ("five", ("zoom", [5])),
        ("5 3", ("zoom", [5, 3])),
        ("53", ("zoom", [5, 3])),
        ("five three", ("zoom", [5, 3])),
        ("click", ("act", "left", [])),
        ("Click.", ("act", "left", [])),
        ("click 7", ("act", "left", [7])),
        ("right click", ("act", "right", [])),
        ("double click 2", ("act", "double", [2])),
        ("move", ("act", "move", [])),
        ("mark", ("act", "mark", [])),
        ("drag here", ("act", "drop", [])),
        ("back", ("act", "back", [])),
        ("cancel", ("cancel",)),
        ("10", None),
        ("hello", None),
        ("click the button", None),
    ],
)
def test_parse_grid(spoken, parsed):
    assert parse_grid(spoken) == parsed


# ---------------------------------------------------------------------------
# Through the service
# ---------------------------------------------------------------------------


class GridWindows(FakeWindows):
    def monitor_rect(self, hwnd):
        return (0, 0, 900, 900)

    def rect(self, hwnd):
        return (90, 90, 390, 390)


class FakeOverlay:
    def __init__(self):
        self.grids: List[tuple] = []
        self.hidden = 0

    def show_grid(self, rect):
        self.grids.append(rect)

    def hide(self):
        self.hidden += 1


def make():
    mouse = FakeMouse()
    runner = ActionRunner(
        keyboard_factory=FakeKeyboard,
        windows=GridWindows([NOTEPAD], foreground=NOTEPAD),
        sleep=lambda _s: None,
        mouse_factory=lambda: mouse,
    )
    svc = VoiceCommandService(
        VoiceCommandsConfig.from_dict({"enabled": True}),
        runner=runner,
        engine_getter=lambda: None,
    )
    overlay = FakeOverlay()
    svc.set_screen_overlay(overlay)
    return svc, mouse, overlay


def test_grid_zoom_then_click():
    svc, mouse, overlay = make()
    assert svc.handle("computer, mouse grid", False)
    assert isinstance(svc.mode, GridMode)
    assert overlay.grids[-1] == (0, 0, 900, 900)
    assert mouse.pos == (450, 450)  # the cursor follows the grid
    assert svc.handle("9", False)  # no wake word while the grid is up
    assert overlay.grids[-1] == (600, 600, 300, 300)
    assert mouse.pos == (750, 750)
    assert svc.handle("right click 1", False)
    assert mouse.clicks == [(650, 650, "right")]
    assert svc.mode is None and overlay.hidden == 1


def test_grid_over_the_window():
    svc, _mouse, overlay = make()
    svc.handle("computer, window grid", False)
    assert overlay.grids[-1] == (90, 90, 300, 300)


def test_grid_mark_and_drag():
    svc, mouse, _overlay = make()
    svc.handle("computer, grid", False)
    svc.handle("1", False)
    assert svc.handle("mark", False)
    assert isinstance(svc.mode, GridMode)  # back to the full grid to pick the drop
    svc.handle("9", False)
    assert svc.handle("drag here", False)
    assert mouse.drags == [((150, 150), (750, 750))]
    assert svc.mode is None


def test_grid_back_and_cancel():
    svc, mouse, overlay = make()
    svc.handle("computer, grid", False)
    svc.handle("5", False)
    svc.handle("back", False)
    assert overlay.grids[-1] == (0, 0, 900, 900)
    assert svc.handle("cancel", False)
    assert svc.mode is None and mouse.clicks == []


def test_dictation_is_not_swallowed_by_the_grid():
    svc, mouse, _overlay = make()
    svc.handle("computer, grid", False)
    assert not svc.handle("I will click on it later", True)
    assert mouse.clicks == []
