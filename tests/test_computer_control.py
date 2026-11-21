"""Computer control by voice: spoken keys, mouse, windows, screen mapping."""

from __future__ import annotations

from typing import List

import pytest

from saidcue.voice_commands.actions import ActionRunner
from saidcue.voice_commands.catalog import COMMANDS
from saidcue.voice_commands.config import ActionError, validate_action
from saidcue.voice_commands.elements import ElementHit, best_element, Candidate
from saidcue.voice_commands.parser import CommandMatcher, fill_slots
from saidcue.voice_commands.screen_map import (
    Monitor,
    rect_to_logical,
    to_logical,
    to_physical,
)
from saidcue.voice_commands.spoken_keys import (
    parse_count,
    parse_key_press,
    parse_spoken_combo,
    split_count,
)
from saidcue.voice_commands.windows import WindowInfo, find_window

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeKeyboard:
    def __init__(self):
        self.events: List[tuple] = []

    def press(self, k):
        self.events.append(("press", str(k)))

    def release(self, k):
        self.events.append(("release", str(k)))

    def presses(self):
        return [k for kind, k in self.events if kind == "press"]


class FakeMouse:
    def __init__(self, pos=(100, 100)):
        self.pos = pos
        self.clicks: List[tuple] = []
        self.drags: List[tuple] = []

    def position(self):
        return self.pos

    def move(self, x, y):
        self.pos = (x, y)

    def click(self, x, y, button="left"):
        self.pos = (x, y)
        self.clicks.append((x, y, button))

    def drag(self, start, end):
        self.drags.append((start, end))


class FakeWindows:
    def __init__(self, windows, foreground=None):
        self.windows = windows
        self.fg = foreground
        self.calls: List[tuple] = []

    def list_windows(self):
        return list(self.windows)

    def foreground(self):
        return self.fg

    def focus(self, hwnd):
        self.calls.append(("focus", hwnd))
        return True

    def __getattr__(self, op):
        if op in ("minimize", "maximize", "restore", "close"):
            return lambda hwnd: self.calls.append((op, hwnd)) or True
        raise AttributeError(op)


class FakeElements:
    def __init__(self, candidates=()):
        self.candidates = list(candidates)
        self.clicked: List[tuple] = []

    def click(self, hwnd, queries, exact=False, button="left"):
        idx = best_element(self.candidates, queries, exact)
        if idx is None:
            return None
        c = self.candidates[idx]
        self.clicked.append((hwnd, c.name, button))
        return ElementHit(c.name, c.kind)


NOTEPAD = WindowInfo(3, "notes.txt - Notepad", "Notepad", "notepad")
SPOTIFY = WindowInfo(4, "Daft Punk - One More Time", "Chrome_WidgetWin_0", "spotify")


def keys_of(combo):
    from pynput.keyboard import HotKey

    return [str(k) for k in HotKey.parse(combo)]


def make_runner(elements=None, windows=None):
    kb = FakeKeyboard()
    mouse = FakeMouse()
    windows = windows or FakeWindows([NOTEPAD, SPOTIFY], foreground=NOTEPAD)
    runner = ActionRunner(
        keyboard_factory=lambda: kb,
        windows=windows,
        sleep=lambda _s: None,
        elements_factory=lambda: elements or FakeElements(),
        mouse_factory=lambda: mouse,
    )
    return runner, kb, mouse, windows


def run_phrase(runner, phrase):
    matched = CommandMatcher(COMMANDS).match(phrase)
    assert matched is not None, phrase
    command, slots = matched
    return command, runner.run([fill_slots(a, slots) for a in command.actions])


# ---------------------------------------------------------------------------
# Spoken keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spoken, combo",
    [
        ("escape", "<esc>"),
        ("Escape.", "<esc>"),
        ("control shift t", "<ctrl>+<shift>+t"),
        ("Ctrl+Shift+T", "<ctrl>+<shift>+t"),
        ("control see", "<ctrl>+c"),
        ("alt f4", "<alt>+<f4>"),
        ("alt f 4", "<alt>+<f4>"),
        ("page down", "<page_down>"),
        ("windows", "<cmd>"),
        ("windows d", "<cmd>+d"),
        ("the tab key", "<tab>"),
        ("control 1", "<ctrl>+1"),
        ("shift tab", "<shift>+<tab>"),
        ("down arrow", "<down>"),
    ],
)
def test_parse_spoken_combo(spoken, combo):
    assert parse_spoken_combo(spoken) == combo


@pytest.mark.parametrize(
    "spoken",
    ["a", "you", "one", "hello", "save button", "control", "control shift", ""],
)
def test_parse_spoken_combo_rejects_ordinary_words(spoken):
    # A bare letter or number word is speech, not a key; a combo needs
    # exactly one non-modifier key at the end.
    assert parse_spoken_combo(spoken) is None


def test_counts():
    assert parse_count("3") == 3
    assert parse_count("three") == 3
    assert parse_count("twice") == 2
    assert parse_count("five times") == 5
    assert parse_count("100") is None
    assert parse_count("lots") is None
    assert split_count("backspace three times") == ("backspace", 3)
    assert split_count("tab twice") == ("tab", 2)
    assert split_count("escape") == ("escape", 1)
    assert parse_key_press("backspace 3 times") == ("<backspace>", 3)
    assert parse_key_press("the save button") is None


def test_bare_key_command_only_matches_real_keys():
    matcher = CommandMatcher(COMMANDS)
    assert matcher.match("escape")[0].id == "bare_key"
    assert matcher.match("control shift t")[0].id == "bare_key"
    assert matcher.match("backspace three times")[0].id == "bare_key"
    # Plain speech and other commands are not swallowed by "{key}".
    assert matcher.match("hello there") is None
    assert matcher.match("tab youtube")[0].id == "browser_tab"
    assert matcher.match("enter")[0].id == "press_enter"
    assert matcher.match("click")[0].id == "mouse_click"


def test_press_key_presses_keys_with_repeat():
    runner, kb, _mouse, _w = make_runner()
    _cmd, result = run_phrase(runner, "press tab 3 times")
    assert result.ok
    assert kb.presses().count(keys_of("<tab>")[0]) == 3
    _cmd, result = run_phrase(runner, "press control shift t")
    assert result.ok
    assert kb.presses()[-3:] == keys_of("<ctrl>+<shift>+t")


def test_press_lone_windows_key():
    runner, kb, _mouse, _w = make_runner()
    _cmd, result = run_phrase(runner, "start menu")
    assert result.ok
    assert kb.presses() == keys_of("<cmd>")


def test_press_falls_back_to_clicking_a_label():
    elements = FakeElements([Candidate("Save", "button", True)])
    runner, kb, _mouse, _w = make_runner(elements=elements)
    _cmd, result = run_phrase(runner, "press the save button")
    assert result.ok, result.message
    assert elements.clicked == [(3, "Save", "left")]
    assert kb.presses() == []


# ---------------------------------------------------------------------------
# Text navigation with counts
# ---------------------------------------------------------------------------


def test_word_navigation_counts():
    runner, kb, _mouse, _w = make_runner()
    _cmd, result = run_phrase(runner, "3 words right")
    assert result.ok
    right = keys_of("<right>")[0]
    assert kb.presses().count(right) == 3
    kb.events.clear()
    _cmd, result = run_phrase(runner, "next word")
    assert result.ok
    assert kb.presses().count(right) == 1


def test_count_slot_rejects_non_numbers():
    # "{count} words right" must not grab "move the words right".
    assert CommandMatcher(COMMANDS).match("move the words right") is None


def test_keys_repeat_accepts_spoken_counts():
    assert validate_action({"type": "keys", "keys": "<tab>", "repeat": "{count}"})
    with pytest.raises(ActionError):
        validate_action({"type": "keys", "keys": "<tab>", "repeat": 0})


# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------


def test_mouse_clicks_at_cursor():
    runner, _kb, mouse, _w = make_runner()
    for phrase, button in [
        ("click", "left"),
        ("right click", "right"),
        ("double click", "double"),
        ("middle click", "middle"),
    ]:
        _cmd, result = run_phrase(runner, phrase)
        assert result.ok
        assert mouse.clicks[-1] == (100, 100, button)


def test_mouse_nudge_and_drag():
    runner, _kb, mouse, _w = make_runner()
    run_phrase(runner, "mouse right")
    assert mouse.pos == (140, 100)
    run_phrase(runner, "mouse down 3")
    assert mouse.pos == (140, 220)
    _cmd, result = run_phrase(runner, "drag here")
    assert not result.ok  # nothing marked yet
    run_phrase(runner, "mark")
    run_phrase(runner, "mouse left 2")
    _cmd, result = run_phrase(runner, "drag here")
    assert result.ok
    assert mouse.drags == [((140, 220), (60, 220))]


def test_right_and_double_click_by_label():
    elements = FakeElements([Candidate("report.pdf", "button", True)])
    runner, _kb, _mouse, _w = make_runner(elements=elements)
    _cmd, result = run_phrase(runner, "right click report pdf")
    assert result.ok and result.message.startswith("Right-clicked")
    _cmd, result = run_phrase(runner, "double click report pdf")
    assert result.ok
    assert [c[2] for c in elements.clicked] == ["right", "double"]


def test_mouse_action_validation():
    with pytest.raises(ActionError):
        validate_action({"type": "mouse", "op": "explode"})
    with pytest.raises(ActionError):
        validate_action({"type": "mouse", "op": "click", "button": "fourth"})
    with pytest.raises(ActionError):
        validate_action({"type": "mouse", "op": "nudge", "direction": "sideways"})


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def test_window_state_commands_act_on_the_foreground_window():
    runner, kb, _mouse, windows = make_runner()
    for phrase, op in [
        ("minimize window", "minimize"),
        ("maximize", "maximize"),
        ("restore window", "restore"),
        ("close window", "close"),
    ]:
        _cmd, result = run_phrase(runner, phrase)
        assert result.ok
        assert windows.calls[-1] == (op, NOTEPAD.hwnd)
    _cmd, result = run_phrase(runner, "snap left")
    assert result.ok
    assert kb.presses()[-2:] == keys_of("<cmd>+<left>")


def test_find_window_falls_back_to_the_process_name():
    windows = [NOTEPAD, SPOTIFY]
    assert find_window(windows, "spotify") == SPOTIFY
    assert find_window(windows, "notepad") == NOTEPAD  # title wins
    assert find_window(windows, "excel") is None


# ---------------------------------------------------------------------------
# Screen mapping (mixed DPI)
# ---------------------------------------------------------------------------

# A 4K laptop panel at 150% on the left, a 1080p monitor at 100% on the right.
MONITORS = [
    Monitor("\\\\.\\DISPLAY1", (0, 0, 3840, 2160), (0, 0, 2560, 1440)),
    Monitor("\\\\.\\DISPLAY2", (3840, 0, 1920, 1080), (3840, 0, 1920, 1080)),
]


def test_to_logical_and_back_on_each_monitor():
    assert to_logical(MONITORS, 1500, 300) == (1000, 200)
    assert to_physical(MONITORS, 1000, 200) == (1500, 300)
    assert to_logical(MONITORS, 4000, 500) == (4000, 500)
    assert to_physical(MONITORS, 4000, 500) == (4000, 500)


def test_rect_to_logical_scales_size():
    assert rect_to_logical(MONITORS, (300, 150, 90, 60)) == (200, 100, 60, 40)


def test_points_off_every_monitor_use_the_nearest():
    assert to_logical(MONITORS, -10, 300) == (-7, 200)
    assert to_logical([], 5, 6) == (5, 6)


# ---------------------------------------------------------------------------
# The AI can't use the new actions to get around its rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        {"type": "press", "target": "enter"},
        {"type": "press", "target": "the buy button"},
        {"type": "mouse", "op": "click"},
        {"type": "mouse", "op": "drag_end"},
    ],
)
def test_ai_plan_blocks_enter_and_clicks_through_new_actions(action):
    from saidcue.voice_commands.ai_agent import PlanRejected, check_plan

    with pytest.raises(PlanRejected):
        check_plan({"actions": [action]}, foreground=NOTEPAD, windows=[NOTEPAD])


def test_ai_plan_allows_window_ops_and_plain_keys():
    from saidcue.voice_commands.ai_agent import check_plan

    plan = check_plan(
        {
            "actions": [
                {"type": "window", "op": "maximize"},
                {"type": "press", "target": "escape"},
            ]
        },
        foreground=NOTEPAD,
        windows=[NOTEPAD],
    )
    assert [a["type"] for a in plan.actions] == ["window", "press"]
