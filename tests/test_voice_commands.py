from __future__ import annotations

import json
import re
from typing import List

import numpy as np
import pytest

from saidcue.voice_commands.actions import ActionRunner, tab_number
from saidcue.voice_commands.ai_agent import (
    AIAgent,
    PlanRejected,
    check_plan,
    extract_json,
)
from saidcue.voice_commands.audio import parse_percent
from saidcue.voice_commands.catalog import (
    COMMANDS,
    DEFAULT_WAKE_WORD,
    WAKE_WORD_PRESETS,
    CatalogCommand,
)
from saidcue.voice_commands.config import (
    ActionError,
    VoiceCommandsConfig,
    validate_action,
)
from saidcue.voice_commands.elements import (
    Candidate,
    ElementHit,
    best_element,
)
from saidcue.voice_commands.dictation_edits import (
    build_lookup,
    match_edit,
)
from saidcue.voice_commands.parser import (
    CommandMatcher,
    fill_slots,
    normalize,
    split_wake_word,
)
from saidcue.voice_commands.service import VoiceCommandService, choice_index
from saidcue.voice_commands.tabs import (
    TabCandidate,
    TabMatch,
    best_tab,
    clean_tab_name,
    rank_tabs,
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


class FakeWindows:
    def __init__(self, windows, foreground=None):
        self.windows = windows
        self.fg = foreground
        self.focused: List[int] = []

    def list_windows(self):
        return list(self.windows)

    def foreground(self):
        return self.fg

    def focus(self, hwnd):
        self.focused.append(hwnd)
        return True


class FakeTabs:
    def __init__(self, tabs):
        self.tabs = tabs  # {app: [(hwnd, name)]}
        self.selected: List[str] = []

    def _windows(self, app):
        """``[(kind, hwnd, [tab names])]`` in first-seen order."""

        kinds = ("browser", "terminal") if app == "any" else (app,)
        out: List[tuple] = []
        index: dict = {}
        for kind in kinds:
            for hwnd, name in self.tabs.get(kind, []):
                key = (kind, hwnd)
                if key not in index:
                    index[key] = len(out)
                    out.append((kind, hwnd, []))
                out[index[key]][2].append(name)
        return out

    def list_tabs(self, app):
        return [n for _h, n in self.tabs.get(app, [])]

    def find(self, app, query):
        found = []
        for window_order, (kind, hwnd, names) in enumerate(self._windows(app)):
            for rank, idx in rank_tabs(names, query):
                found.append(
                    (
                        rank,
                        window_order,
                        idx,
                        TabCandidate(
                            app=kind,
                            hwnd=hwnd,
                            window_title=f"win {hwnd}",
                            tab_name=names[idx],
                            tab_index=idx,
                            tab_count=len(names),
                            rank=rank,
                        ),
                    )
                )
        found.sort(key=lambda entry: entry[:3])
        return [entry[3] for entry in found]

    def select_candidate(self, candidate):
        for kind, hwnd, names in self._windows(candidate.app):
            if hwnd != candidate.hwnd:
                continue
            idx = candidate.tab_index
            if idx >= len(names) or names[idx] != candidate.tab_name:
                same = [i for i, n in enumerate(names) if n == candidate.tab_name]
                if not same:
                    return None
                idx = same[0]
            self.selected.append(names[idx])
            return TabMatch(hwnd, f"win {hwnd}", names[idx])
        return None

    def select(self, app, query):
        candidates = self.find(app, query)
        if not candidates:
            return None
        return self.select_candidate(candidates[0])


class FakePicker:
    """Same API as TabPicker, without Qt."""

    def __init__(self):
        self.shown: List[tuple] = []
        self.dismiss_count = 0

    def show_candidates(self, candidates, *, query=""):
        self.shown.append((list(candidates), query))

    def dismiss(self):
        self.dismiss_count += 1


class FakeEngine:
    def __init__(self, typer=None):
        self.is_active = False
        self.typer = typer

    def set_active(self, active):
        self.is_active = active


class FakeTyper:
    def __init__(self, last=""):
        self.typed: List[str] = []
        self.last = last

    def type_text(self, text, *, append_space=False):
        self.typed.append(text)
        return text

    def forget_last(self):
        last, self.last = self.last, ""
        return last


TERMINAL = WindowInfo(10, "PowerShell", "CASCADIA_HOSTING_WINDOW_CLASS")
CHROME = WindowInfo(20, "Gmail - Google Chrome", "Chrome_WidgetWin_1")
NOTEPAD = WindowInfo(30, "notes.txt - Notepad", "Notepad")


class FakeElements:
    """Same API as UiaElements; candidates are ranked with the real ranker."""

    def __init__(self, candidates=(), scrollable=True):
        self.candidates = list(candidates)
        self.scrollable = scrollable
        self.clicked: List[tuple] = []
        self.scrolled: List[tuple] = []
        self.wheeled: List[tuple] = []

    def click(self, hwnd, queries, exact=False, button="left"):
        idx = best_element(self.candidates, queries, exact)
        if idx is None:
            return None
        c = self.candidates[idx]
        self.clicked.append(
            (hwnd, c.name) if button == "left" else (hwnd, c.name, button)
        )
        return ElementHit(c.name, c.kind)

    def scroll(self, hwnd, direction, amount="small"):
        if not self.scrollable:
            return False
        self.scrolled.append((hwnd, direction, amount))
        return True

    def wheel(self, hwnd, direction, amount="small"):
        self.wheeled.append((hwnd, direction, amount))
        return True


def make_runner(windows=None, tabs=None, typer=None, engine=None, elements=None):
    kb = FakeKeyboard()
    opened: List[str] = []
    runner = ActionRunner(
        keyboard_factory=lambda: kb,
        windows=windows or FakeWindows([TERMINAL, CHROME, NOTEPAD]),
        tabs_factory=lambda: tabs or FakeTabs({}),
        launcher=opened.append,
        typer=typer,
        engine_getter=lambda: engine,
        sleep=lambda _s: None,
        elements_factory=lambda: elements or FakeElements(),
    )
    return runner, kb, opened


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_normalize_strips_case_accents_punctuation():
    assert normalize("Computer,  Tab  Café!") == "computer tab cafe"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Computer, open YouTube.", "open youtube"),
        ("Hey computer, next song", "next song"),
        ("Computer.", ""),
        ("My computer is slow", None),  # wake word must come first
        ("Interesting.", None),
    ],
)
def test_split_wake_word(text, expected):
    assert split_wake_word(text, ["computer", "hey computer"]) == expected


def _cmd(phrases, *actions):
    return CatalogCommand("x", "g", "d", tuple(phrases), tuple(actions))


def test_matcher_slots_and_most_specific_wins():
    m = CommandMatcher(
        [
            _cmd(["open {app}"], {"type": "open", "target": "{app}"}),
            _cmd(["open youtube"], {"type": "open", "target": "yt"}),
            _cmd(["save"], {"type": "keys", "keys": "<ctrl>+s"}),
        ]
    )
    cmd, slots = m.match("open youtube")
    assert cmd.actions[0]["target"] == "yt" and slots == {}
    cmd, slots = m.match("Open the calculator")
    assert slots == {"app": "the calculator"}
    assert m.match("save")[0].actions[0]["keys"] == "<ctrl>+s"
    assert m.match("save everything") is None  # exact templates need an exact match
    assert m.match("something odd") is None


def test_matcher_accepts_a_slot_before_a_trailing_word():
    """People say both "tab youtube" and "the youtube tab"."""

    m = CommandMatcher(
        [
            _cmd(["tab {name}"], {"type": "switch_tab", "app": "any", "tab": "{name}"}),
            _cmd(
                ["the {name} tab"],
                {"type": "switch_tab", "app": "any", "tab": "{name}"},
            ),
        ]
    )
    assert m.match("tab youtube")[1] == {"name": "youtube"}
    assert m.match("the youtube tab")[1] == {"name": "youtube"}
    # The more specific template wins: "the … tab" over "tab …".
    assert m.match("the youtube tab")[0].phrases == ("the {name} tab",)
    # A slot with no words around it is meaningless.
    with pytest.raises(ValueError):
        CommandMatcher([_cmd(["{x}"], {"type": "wait", "ms": 1})])


def test_fill_slots_recurses():
    action = {"type": "switch_tab", "app": "terminal", "tab": "{name}"}
    assert fill_slots(action, {"name": "server"})["tab"] == "server"


def test_catalog_is_english_unique_and_matchable():
    ids = [c.id for c in COMMANDS]
    assert len(ids) == len(set(ids))
    m = CommandMatcher(COMMANDS)  # raises if any template is malformed
    assert m.match("next song")[0].id == "next_track"
    assert m.match("new tab")[0].id == "new_tab"
    cmd, slots = m.match("tab gmail")
    assert cmd.id == "browser_tab" and slots == {"name": "gmail"}
    cmd, slots = m.match("go to terminal two")
    assert cmd.id == "terminal_tab" and slots == {"name": "two"}
    cmd, slots = m.match("go to notepad")
    assert cmd.id == "focus_window" and slots == {"name": "notepad"}
    assert m.match("switch to notepad")[0].id == "focus_window"
    # "focus ..." means focus a UI element (e.g. a text box), not switch
    # windows — that's "go to"/"switch to" above, so there's no clash.
    cmd, slots = m.match("focus search")
    assert cmd.id == "click" and slots == {"name": "search"}
    cmd, slots = m.match("focus the search box")
    assert cmd.id == "click" and slots == {"name": "search box"}
    assert m.match("dictate in notes")[0].id == "dictate_in"
    assert DEFAULT_WAKE_WORD in WAKE_WORD_PRESETS


# ---------------------------------------------------------------------------
# Config / validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        {"type": "shell", "cmd": "rm -rf /"},
        {"type": "keys"},
        {"type": "media", "key": "explode"},
        {"type": "switch_tab", "app": "excel", "tab": "x"},
        {"type": "wait", "ms": 999999},
        {"type": "dictation", "state": "maybe"},
        "open notepad",
    ],
)
def test_validate_action_rejects_bad_actions(action):
    with pytest.raises(ActionError):
        validate_action(action)


def test_config_round_trip_and_errors():
    raw = {
        "enabled": True,
        "wake_word": "  hey   assistant ",
        "ai_fallback": True,
        "disabled_commands": ["open_twitch", "no_such_command"],
    }
    cfg = VoiceCommandsConfig.from_dict(raw)
    again = VoiceCommandsConfig.from_dict(cfg.to_dict())
    assert again.wake_word == "hey assistant" and again.ai_fallback
    assert again.disabled_commands == ("open_twitch", "no_such_command")
    ids = {c.id for c in again.commands}
    assert "open_twitch" not in ids and "open_youtube" in ids
    assert len(again.commands) == len(COMMANDS) - 1
    assert VoiceCommandsConfig().wake_word == "computer"
    with pytest.raises(ActionError):
        VoiceCommandsConfig.from_dict({"wake_word": "  "})
    with pytest.raises(ActionError):
        VoiceCommandsConfig.from_dict({"disabled_commands": "save"})


# ---------------------------------------------------------------------------
# Windows / tabs helpers
# ---------------------------------------------------------------------------


def test_find_window_prefers_prefix_then_front_most():
    wins = [CHROME, NOTEPAD, WindowInfo(40, "My important notes", "X")]
    assert find_window(wins, "notes").hwnd == 30  # starts with "notes"
    assert find_window(wins, "GMAIL").hwnd == 20
    assert find_window(wins, "excel") is None


def test_best_tab_ranking():
    names = ["Windows PowerShell", "server web", "server"]
    assert best_tab(names, "Server") == 2  # exact beats prefix
    assert best_tab(names, "power") == 0
    assert best_tab(names, "nothing") is None


@pytest.mark.parametrize(
    "text, n",
    [("2", 2), ("two", 2), ("Second", 2), ("3rd", 3), ("10", None), ("web", None)],
)
def test_tab_number(text, n):
    assert tab_number(text) == n


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def test_switch_tab_by_name_selects_and_focuses():
    tabs = FakeTabs({"terminal": [(10, "PowerShell"), (10, "server")]})
    windows = FakeWindows([TERMINAL, CHROME])
    runner, _kb, _ = make_runner(windows=windows, tabs=tabs)
    result = runner.run([{"type": "switch_tab", "app": "terminal", "tab": "Server"}])
    assert result.ok and tabs.selected == ["server"] and windows.focused == [10]


def test_switch_tab_by_number_uses_shortcut():
    windows = FakeWindows([CHROME, TERMINAL])
    runner, kb, _ = make_runner(windows=windows)
    assert runner.run([{"type": "switch_tab", "app": "terminal", "tab": "three"}]).ok
    assert windows.focused == [10]
    assert "'3'" in kb.presses()[-1] or kb.presses()[-1] == "3"


def test_switch_tab_missing_reports_failure():
    runner, _kb, _ = make_runner(tabs=FakeTabs({"browser": [(20, "Gmail")]}))
    result = runner.run([{"type": "switch_tab", "app": "browser", "tab": "youtube"}])
    assert not result.ok and "youtube" in result.message


def test_switch_tab_with_several_matches_does_not_guess():
    """An ambiguous query hands the candidates over instead of picking one."""

    tabs = FakeTabs(
        {"browser": [(20, "YouTube"), (20, "YouTube - Lo-fi"), (30, "YouTube")]}
    )
    ambiguous = []
    runner, _kb, _ = make_runner(tabs=tabs)
    runner.set_ambiguous_handler(
        lambda candidates, query: ambiguous.append((candidates, query))
    )
    result = runner.run([{"type": "switch_tab", "app": "browser", "tab": "youtube"}])
    assert result.ok and "3 tabs" in result.message
    assert tabs.selected == []  # nothing was picked behind the user's back
    candidates, query = ambiguous[0]
    assert query == "youtube"
    # Exact matches first, then the prefix match; windows in Z-order.
    assert [c.rank for c in candidates] == [0, 0, 1]
    assert [c.hwnd for c in candidates] == [20, 30, 20]
    assert [c.tab_index for c in candidates] == [0, 0, 1]


def test_switch_tab_with_one_match_still_switches_directly():
    tabs = FakeTabs({"browser": [(20, "Gmail")]})
    ambiguous = []
    runner, _kb, _ = make_runner(tabs=tabs)
    runner.set_ambiguous_handler(
        lambda candidates, query: ambiguous.append((candidates, query))
    )
    result = runner.run([{"type": "switch_tab", "app": "browser", "tab": "gmail"}])
    assert result.ok and tabs.selected == ["Gmail"] and ambiguous == []


def test_ambiguous_tab_opens_the_picker_and_a_spoken_number_picks_it():
    tabs = FakeTabs({"browser": [(20, "YouTube"), (20, "YouTube - Lo-fi")]})
    picker = FakePicker()
    svc, _kb, _opened, feedback, _engine = _service(tabs=tabs, picker=picker)

    assert svc.handle("computer, tab youtube", False)
    candidates, query = picker.shown[0]
    assert query == "youtube" and len(candidates) == 2
    assert tabs.selected == []  # still waiting for the user

    assert svc.handle("computer, two", False)
    assert tabs.selected == ["YouTube - Lo-fi"]
    assert feedback[-1][1] == "ok"


def test_a_spoken_choice_out_of_range_is_reported():
    tabs = FakeTabs({"browser": [(20, "YouTube"), (20, "YouTube - Lo-fi")]})
    picker = FakePicker()
    svc, _kb, _opened, feedback, _engine = _service(tabs=tabs, picker=picker)
    svc.handle("computer, tab youtube", False)
    assert svc.handle("computer, seven", False)
    assert tabs.selected == []
    assert "only 2" in feedback[-1][0]


def test_clicking_a_picker_row_selects_that_tab():
    """The mouse path goes through the same service entry point."""

    tabs = FakeTabs({"browser": [(20, "YouTube"), (20, "YouTube - Lo-fi")]})
    picker = FakePicker()
    svc, _kb, _opened, _feedback, _engine = _service(tabs=tabs, picker=picker)
    svc.handle("computer, tab youtube", False)
    svc.resolve_choice(1)
    assert tabs.selected == ["YouTube"]


def test_the_app_name_can_lead_the_choice():
    """A terminal picker answers to "terminal two", a browser one to "browser two"."""

    tabs = FakeTabs({"terminal": [(10, "PowerShell server"), (10, "PowerShell logs")]})
    picker = FakePicker()
    svc, _kb, _opened, _feedback, _engine = _service(tabs=tabs, picker=picker)
    svc.handle("computer, terminal powershell", False)
    assert len(picker.shown) == 1
    svc.handle("computer, terminal two", False)
    assert tabs.selected == ["PowerShell logs"]


def test_resolving_twice_only_switches_once():
    """The spoken choice and the click race for the same list."""

    tabs = FakeTabs({"browser": [(20, "YouTube"), (20, "YouTube - Lo-fi")]})
    picker = FakePicker()
    svc, _kb, _opened, _feedback, _engine = _service(tabs=tabs, picker=picker)
    svc.handle("computer, tab youtube", False)
    svc.resolve_choice(1)
    svc.resolve_choice(2)
    assert tabs.selected == ["YouTube"]


@pytest.mark.parametrize(
    "phrase, command_id, name",
    [
        ("tab youtube", "browser_tab", "youtube"),
        ("tab with youtube", "browser_tab", "youtube"),
        ("youtube tab", "browser_tab", "youtube"),
        ("the youtube tab", "browser_tab", "youtube"),
        ("go to tab youtube", "browser_tab", "youtube"),
        ("browser tab gmail", "browser_tab", "gmail"),
        ("terminal powershell", "terminal_tab", "powershell"),
        ("terminal server", "terminal_tab", "server"),
        # The generic "… tab" searches every app that has tabs.
        ("powershell tab", "browser_tab", "powershell"),
    ],
)
def test_the_tab_phrasings_people_actually_say(phrase, command_id, name):
    cmd, slots = CommandMatcher(COMMANDS).match(phrase)
    assert cmd.id == command_id
    assert slots == {"name": name}


def test_the_generic_tab_command_searches_every_app():
    tabs = FakeTabs({"browser": [(20, "YouTube")], "terminal": [(10, "YouTube")]})
    ambiguous = []
    runner, _kb, _ = make_runner(tabs=tabs)
    runner.set_ambiguous_handler(
        lambda candidates, query: ambiguous.append((candidates, query))
    )
    result = runner.run([{"type": "switch_tab", "app": "any", "tab": "youtube"}])
    assert result.ok
    candidates = ambiguous[0][0]
    assert [c.app for c in candidates] == ["browser", "terminal"]


def test_a_bare_wake_word_waits_for_the_rest_of_the_command():
    """Whisper splits "computer, youtube tab" into two utterances."""

    tabs = FakeTabs({"browser": [(20, "YouTube"), (20, "YouTube - Lo-fi")]})
    picker = FakePicker()
    svc, _kb, _opened, _feedback, _engine = _service(tabs=tabs, picker=picker)

    assert svc.handle("Computer", False)
    assert svc.handle("youtube tab", False)
    assert len(picker.shown) == 1
    assert tabs.selected == []


def test_a_bare_wake_word_then_a_number_picks_from_the_picker():
    tabs = FakeTabs({"browser": [(20, "YouTube"), (20, "YouTube - Lo-fi")]})
    picker = FakePicker()
    svc, _kb, _opened, _feedback, _engine = _service(tabs=tabs, picker=picker)

    svc.handle("computer, tab youtube", False)
    svc.handle("Computer", False)
    assert svc.handle("two", False)
    assert tabs.selected == ["YouTube - Lo-fi"]


def test_a_bare_wake_word_does_not_swallow_ordinary_speech():
    svc, _kb, _opened, _feedback, _engine = _service()
    assert svc.handle("Computer", False)
    # "hello there" is not a command: it must be typed normally.
    assert not svc.handle("hello there", False)


def test_the_pending_wake_word_expires(monkeypatch):
    import saidcue.voice_commands.service as service_mod

    monkeypatch.setattr(service_mod, "WAKE_MERGE_SECONDS", 0.0)
    svc, _kb, _opened, _feedback, _engine = _service()
    assert svc.handle("Computer", False)
    assert not svc.handle("save", False)


def test_a_question_ending_in_tab_is_still_a_question():
    """ "ask what is a browser tab" must not be read as a tab command."""

    cmd, slots = CommandMatcher(COMMANDS).match("ask what is a browser tab")
    assert cmd.id == "ask"
    assert slots == {"question": "what is a browser tab"}


def test_saying_something_else_dismisses_the_picker():
    tabs = FakeTabs({"browser": [(20, "YouTube"), (20, "YouTube - Lo-fi")]})
    picker = FakePicker()
    svc, _kb, opened, _feedback, _engine = _service(tabs=tabs, picker=picker)
    svc.handle("computer, tab youtube", False)
    assert svc.handle("computer, open youtube", False)
    assert picker.dismiss_count == 1
    assert tabs.selected == []
    assert opened == ["https://www.youtube.com"]


def test_the_picker_is_dropped_when_it_closes_on_its_own():
    tabs = FakeTabs({"browser": [(20, "YouTube"), (20, "YouTube - Lo-fi")]})
    picker = FakePicker()
    svc, _kb, _opened, _feedback, _engine = _service(tabs=tabs, picker=picker)
    svc.handle("computer, tab youtube", False)
    svc.clear_pending()
    assert svc.handle("computer, two", False)
    assert tabs.selected == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("two", 2),
        ("2", 2),
        ("second", 2),
        ("the second one", 2),
        ("pick three", 3),
        ("tab 2", 2),
        ("el dos", 2),
        ("la segunda", 2),
        ("one", 1),
        ("pick one", 1),
        ("nine", 9),
        ("youtube", None),
        ("", None),
    ],
)
def test_choice_index(text, expected):
    assert choice_index(text) == expected


def test_rank_tabs_returns_every_match_best_first():
    names = ["YouTube", "YouTube - Lo-fi", "Gmail", "youtube"]
    ranked = rank_tabs(names, "youtube")
    assert [rank for rank, _ in ranked] == [0, 0, 1]
    assert [idx for _, idx in ranked] == [0, 3, 1]
    assert best_tab(names, "youtube") == 0


def test_clean_tab_name_drops_the_browser_suffix():
    assert (
        clean_tab_name("Some song - YouTube - Google Chrome") == "Some song - YouTube"
    )
    assert clean_tab_name("Windows PowerShell") == "Windows PowerShell"


def test_focus_then_dictation_on():
    engine = FakeEngine()
    windows = FakeWindows([TERMINAL, NOTEPAD])
    runner, _kb, _ = make_runner(windows=windows, engine=engine)
    result = runner.run(
        [
            {"type": "focus_window", "title": "notes"},
            {"type": "wait", "ms": 150},
            {"type": "dictation", "state": "on"},
        ]
    )
    assert result.ok and windows.focused == [30] and engine.is_active


def test_run_stops_at_first_failure():
    engine = FakeEngine()
    runner, _kb, _ = make_runner(engine=engine)
    result = runner.run(
        [
            {"type": "focus_window", "title": "nope"},
            {"type": "dictation", "state": "on"},
        ]
    )
    assert not result.ok and not engine.is_active


def test_open_and_keys():
    runner, kb, opened = make_runner()
    assert runner.run([{"type": "open", "target": "https://youtube.com"}]).ok
    assert opened == ["https://youtube.com"]
    runner.run([{"type": "keys", "keys": "<ctrl>+s"}])
    assert len(kb.presses()) == 2  # ctrl + s


# ---------------------------------------------------------------------------
# Click & scroll
# ---------------------------------------------------------------------------

YOUTUBE = [
    Candidate("Close", "button", in_page=False),  # browser window frame
    Candidate("Reload", "button", in_page=False),
    Candidate("Skip navigation", "button", in_page=True),
    Candidate("Search", "input", in_page=True),
    Candidate("Search", "button", in_page=True),
    Candidate("Pause (k)", "button", in_page=True),
    Candidate("Next (SHIFT+n)", "button", in_page=True),
    Candidate("Subscribe to FUERZA REGIDA.", "button", in_page=True),
    Candidate("Skip", "button", in_page=True),
    Candidate("Close dialog", "button", in_page=True, offscreen=True),
]


@pytest.mark.parametrize(
    "query, expected",
    [
        ("pause", "Pause (k)"),  # word prefix
        ("Next.", "Next (SHIFT+n)"),
        ("subscribe", "Subscribe to FUERZA REGIDA."),
        ("regida", "Subscribe to FUERZA REGIDA."),  # contained word
        ("skip", "Skip"),  # exact beats "Skip navigation"
        ("close", "Close dialog"),  # the browser's own Close is never matched
        ("sub", None),  # whole words only
        ("reload", "Reload"),  # browser UI is fine when nothing else matches
    ],
)
def test_best_element_ranking(query, expected):
    idx = best_element(YOUTUBE, [query])
    assert (YOUTUBE[idx].name if idx is not None else None) == expected


def test_best_element_prefers_page_then_onscreen():
    cands = [
        Candidate("Search", "button", in_page=False),
        Candidate("Search", "input", in_page=True, offscreen=True),
        Candidate("Search", "input", in_page=True),
    ]
    assert best_element(cands, ["search"]) == 2


def test_best_element_exact_only():
    assert best_element(YOUTUBE, ["Skip ad", "skip"], exact=True) == 8
    assert best_element(YOUTUBE[:8], ["skip"], exact=True) is None


def test_best_element_matches_query_containing_name_as_whole_word():
    # Saying more than the label ("the search box") still finds "Search",
    # the input listed before the button of the same name.
    assert best_element(YOUTUBE, ["the search box"]) == 3
    assert best_element(YOUTUBE, ["skip the ad please"]) == 8
    # A real whole word is required, not just a substring: "search" is not
    # a word inside "researching".
    assert best_element(YOUTUBE, ["researching"]) is None


def test_click_presses_in_foreground_window():
    elements = FakeElements(YOUTUBE)
    windows = FakeWindows([CHROME, NOTEPAD], foreground=CHROME)
    runner, _kb, _ = make_runner(windows=windows, elements=elements)
    result = runner.run([{"type": "click", "target": "subscribe"}])
    assert result.ok and result.message == "Clicked “Subscribe to FUERZA REGIDA.”"
    assert elements.clicked == [(20, "Subscribe to FUERZA REGIDA.")]
    result = runner.run([{"type": "click", "target": "search"}])
    assert result.message == "Focused “Search”"  # the input wins the tie


def test_click_missing_and_no_window():
    runner, _kb, _ = make_runner(
        windows=FakeWindows([CHROME], foreground=CHROME), elements=FakeElements()
    )
    result = runner.run([{"type": "click", "target": "subscribe"}])
    assert not result.ok and "subscribe" in result.message
    runner, _kb, _ = make_runner(windows=FakeWindows([], foreground=None))
    assert not runner.run([{"type": "click", "target": "x"}]).ok


def test_skip_ad_command():
    skip = next(c for c in COMMANDS if c.id == "skip_ad")
    windows = FakeWindows([CHROME], foreground=CHROME)
    no_ad = FakeElements(YOUTUBE[:8])  # only "Skip navigation" on the page
    runner, _kb, _ = make_runner(windows=windows, elements=no_ad)
    result = runner.run(list(skip.actions))
    assert not result.ok and result.message == "No ad to skip" and not no_ad.clicked
    spanish = FakeElements(YOUTUBE[:8] + [Candidate("Omitir anuncio", "button", True)])
    runner, _kb, _ = make_runner(windows=windows, elements=spanish)
    assert runner.run(list(skip.actions)).ok
    assert spanish.clicked == [(20, "Omitir anuncio")]


def test_scroll_uses_ui_automation_then_falls_back():
    windows = FakeWindows([CHROME], foreground=CHROME)
    elements = FakeElements()
    runner, kb, _ = make_runner(windows=windows, elements=elements)
    assert (
        runner.run([{"type": "scroll", "direction": "down"}]).message == "Scrolled down"
    )
    assert elements.scrolled == [(20, "down", "small")]

    no_uia = FakeElements(scrollable=False)  # e.g. Firefox
    runner, kb, _ = make_runner(windows=windows, elements=no_uia)
    assert runner.run([{"type": "scroll", "direction": "up", "amount": "page"}]).ok
    assert no_uia.wheeled == [(20, "up", "page")]
    assert runner.run([{"type": "scroll", "direction": "bottom"}]).ok
    assert len(kb.presses()) == 2  # ctrl + end


@pytest.mark.parametrize(
    "action",
    [
        {"type": "click"},
        {"type": "click", "any": []},
        {"type": "click", "target": "x", "exact": "yes"},
        {"type": "scroll", "direction": "sideways"},
        {"type": "scroll", "direction": "down", "amount": "huge"},
    ],
)
def test_click_scroll_validation(action):
    with pytest.raises(ActionError):
        validate_action(action)


def test_volume_commands_press_several_times():
    m = CommandMatcher(COMMANDS)
    for phrase, cmd_id in [
        ("Turn the volume down.", "volume_down"),
        ("turn down the volume", "volume_down"),
        ("lower the volume", "volume_down"),
        ("turn it up", "volume_up"),
        ("volume down a lot", "volume_down_more"),
        ("Turn off the sound", "mute"),
        ("volume off", "mute"),
        ("Unmute.", "unmute"),
        ("turn the sound on", "unmute"),
        ("set the volume to 50", "set_volume"),
    ]:
        assert m.match(phrase)[0].id == cmd_id, phrase
    down = next(c for c in COMMANDS if c.id == "volume_down")
    runner, kb, _ = make_runner()
    result = runner.run(list(down.actions))
    assert result.message == "Volume down 10%"
    assert len(kb.presses()) == 5
    with pytest.raises(ActionError):
        validate_action({"type": "media", "key": "volume_up", "repeat": 0})


@pytest.mark.parametrize(
    "text, expected",
    [
        ("50", 50),
        ("50%", 50),
        ("fifty", 50),
        ("Sixty-five percent.", 65),
        ("twenty five", 25),
        ("one hundred", 100),
        ("a hundred percent", 100),
        ("half", 50),
        ("max", 100),
        ("zero", 0),
        ("150", None),
        ("loud", None),
    ],
)
def test_parse_percent(text, expected):
    assert parse_percent(text) == expected


class FakeVolume:
    def __init__(self):
        self.level = 94
        self.muted = False

    def set_level(self, percent):
        self.level = percent

    def set_mute(self, muted):
        self.muted = muted


def _run_command(text, volume):
    m = CommandMatcher(COMMANDS)
    cmd, slots = m.match(text)
    kb = FakeKeyboard()
    runner = ActionRunner(
        keyboard_factory=lambda: kb,
        windows=FakeWindows([]),
        volume_factory=lambda: volume,
    )
    return runner.run([fill_slots(a, slots) for a in cmd.actions])


def test_set_volume_and_exact_mute():
    vol = FakeVolume()
    assert _run_command("set the volume to forty", vol).message == "Volume 40%"
    assert vol.level == 40
    assert _run_command("volume to 75%", vol).message == "Volume 75%"
    result = _run_command("set volume to loud", vol)
    assert not result.ok and "0 to 100" in result.message and vol.level == 75
    # Mute/unmute are exact, not toggles: saying "mute" twice stays muted.
    assert _run_command("mute", vol).message == "Muted"
    assert _run_command("sound off", vol).ok and vol.muted is True
    assert _run_command("unmute", vol).message == "Unmuted" and vol.muted is False


@pytest.mark.parametrize(
    "action",
    [
        {"type": "volume"},
        {"type": "volume", "level": 50, "mute": True},
        {"type": "volume", "level": 150},
        {"type": "volume", "level": True},
        {"type": "volume", "mute": "yes"},
    ],
)
def test_volume_validation(action):
    with pytest.raises(ActionError):
        validate_action(action)


def _strip_tags(html: str) -> str:
    import html as html_module

    return html_module.unescape(re.sub(r"<[^>]+>", "", html))


def test_guide_html_covers_every_command_and_filters():
    from saidcue.configurator.voice_guide import build_guide_html

    full = _strip_tags(build_guide_html())
    for command in COMMANDS:
        for phrase in command.phrases:
            assert phrase in full
        assert command.description in full
        assert command.group in full
    assert "How a command fires" in full
    assert "When nothing matches" in full

    only_volume = _strip_tags(build_guide_html("volume"))
    assert "set volume to" in only_volume
    assert "terminal" not in only_volume  # terminal_tab phrases don't mention volume
    assert "How a command fires" not in only_volume  # narrative sections hidden

    none_match = build_guide_html("xyzzyxyzzy")
    assert "No command matches" in none_match


def test_catalog_click_and_scroll_phrases():
    m = CommandMatcher(COMMANDS)
    assert m.match("skip ad")[0].id == "skip_ad"
    assert m.match("skip")[0].id == "next_track"
    cmd, slots = m.match("click on subscribe")
    assert cmd.id == "click" and slots == {"name": "subscribe"}
    assert m.match("scroll down")[0].id == "scroll_down"
    assert m.match("scroll down more")[0].id == "scroll_down_more"
    assert m.match("page up")[0].id == "page_up"
    assert m.match("scroll to the top")[0].id == "scroll_top"


# ---------------------------------------------------------------------------
# Dictation edits
# ---------------------------------------------------------------------------


def test_dictation_edit_requires_whole_utterance():
    lookup = build_lookup()
    assert match_edit("New line.", lookup) == "newline"
    assert match_edit("Scratch that", lookup) == "undo_phrase"
    assert match_edit("Write a new line of code", lookup) is None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _service(
    *, typer=None, engine=None, agent=None, tabs=None, windows=None, picker=None, **cfg
):
    config = VoiceCommandsConfig.from_dict({"enabled": True, **cfg})
    engine = engine or FakeEngine(typer=typer)
    runner, kb, opened = make_runner(
        typer=typer, engine=engine, tabs=tabs, windows=windows
    )
    feedback: List[tuple] = []
    svc = VoiceCommandService(
        config,
        runner=runner,
        engine_getter=lambda: engine,
        agent=agent,
        on_feedback=lambda t, k: feedback.append((t, k)),
        picker=picker,
    )
    return svc, kb, opened, feedback, engine


def test_service_ignores_normal_speech():
    svc, _kb, opened, feedback, _ = _service()
    assert svc.handle("today we will open youtube", typing_active=True) is False
    assert opened == [] and feedback == []


def test_service_runs_wake_word_command():
    svc, _kb, opened, feedback, _ = _service()
    assert svc.handle("Computer, open YouTube.", typing_active=False) is True
    assert opened == ["https://www.youtube.com"] and feedback[-1][1] == "ok"


def test_service_custom_wake_word_and_disabled_command():
    svc, _kb, opened, feedback, _ = _service(
        wake_word="Hey Assistant", disabled_commands=["open_youtube"]
    )
    assert svc.handle("computer, open youtube", typing_active=False) is False
    assert svc.handle("Hey assistant, open YouTube.", typing_active=False) is True
    assert opened == [] and feedback[-1] == ("Unknown command: open youtube", "error")


def test_service_unknown_command_without_ai():
    svc, _kb, _o, feedback, _ = _service()
    assert svc.handle("computer do magic", typing_active=False) is True
    assert feedback[-1] == ("Unknown command: do magic", "error")


def test_service_disabled_consumes_nothing():
    svc, *_ = _service(enabled=False)
    assert svc.handle("computer save", typing_active=False) is False


def test_service_dictation_edits_only_while_typing():
    typer = FakeTyper(last="hello world ")
    svc, kb, _o, feedback, _ = _service(typer=typer)
    assert svc.handle("New line.", typing_active=False) is False
    assert svc.handle("Scratch that.", typing_active=True) is True
    # pynput parses <backspace> to virtual-key 8 (VK_BACK) on Windows.
    assert len(kb.presses()) == len("hello world ")
    assert feedback[-1] == ("Erased last phrase", "ok")


def test_service_stop_dictation_edit():
    engine = FakeEngine()
    engine.is_active = True
    svc, *_ = _service(engine=engine)
    assert svc.handle("Stop typing.", typing_active=True) is True
    assert engine.is_active is False


# ---------------------------------------------------------------------------
# AI agent
# ---------------------------------------------------------------------------


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"actions": [], "say": "x"}\n```')["say"] == "x"
    assert extract_json('Sure! {"actions": [{"type": "wait", "ms": 1}]} ok')["actions"]
    with pytest.raises(PlanRejected):
        extract_json("no json here")


def test_check_plan_blocks_enter_and_terminal_typing():
    with pytest.raises(PlanRejected, match="Enter"):
        check_plan(
            {"actions": [{"type": "keys", "keys": "<enter>"}]},
            foreground=NOTEPAD,
            windows=[],
        )
    with pytest.raises(PlanRejected, match="terminal"):
        check_plan(
            {"actions": [{"type": "type_text", "text": "dir"}]},
            foreground=TERMINAL,
            windows=[],
        )
    with pytest.raises(PlanRejected, match="terminal"):
        check_plan(
            {
                "actions": [
                    {"type": "switch_tab", "app": "terminal", "tab": "web"},
                    {"type": "type_text", "text": "npm start"},
                ]
            },
            foreground=NOTEPAD,
            windows=[],
        )
    with pytest.raises(PlanRejected):
        check_plan(
            {"actions": [{"type": "shell", "cmd": "x"}]}, foreground=None, windows=[]
        )
    with pytest.raises(PlanRejected, match="max"):
        check_plan(
            {"actions": [{"type": "wait", "ms": 1}] * 9}, foreground=None, windows=[]
        )


def test_check_plan_blocks_clicks_allows_scroll():
    with pytest.raises(PlanRejected, match="click"):
        check_plan(
            {"actions": [{"type": "click", "target": "Buy now"}]},
            foreground=CHROME,
            windows=[],
        )
    plan = check_plan(
        {"actions": [{"type": "scroll", "direction": "down", "amount": "page"}]},
        foreground=CHROME,
        windows=[],
    )
    assert plan.actions[0]["amount"] == "page"


def test_check_plan_allows_typing_after_leaving_terminal():
    plan = check_plan(
        {
            "actions": [
                {"type": "focus_window", "title": "notes"},
                {"type": "type_text", "text": "hello"},
            ],
            "say": "Done",
        },
        foreground=TERMINAL,
        windows=[TERMINAL, NOTEPAD],
    )
    assert len(plan.actions) == 2 and plan.say == "Done"


def test_service_ai_fallback_runs_validated_plan():
    reply = json.dumps(
        {"actions": [{"type": "open", "target": "mspaint"}], "say": "Opening Paint"}
    )
    seen = {}

    def complete(system, user):
        seen["context"] = json.loads(user)
        return reply

    agent = AIAgent(
        complete, windows=FakeWindows([NOTEPAD], NOTEPAD), tabs=FakeTabs({})
    )
    svc, _kb, opened, feedback, _ = _service(agent=agent, ai_fallback=True)
    assert svc.handle("computer open paint please", typing_active=False)
    assert opened == ["mspaint"]
    assert feedback[0] == ("Thinking…", "busy")
    assert feedback[-1] == ("Opening Paint", "ok")
    assert seen["context"]["request"] == "open paint please"
    assert seen["context"]["foreground_window"] == "notes.txt - Notepad"


def test_service_ai_blocked_plan_runs_nothing():
    agent = AIAgent(
        lambda s, u: json.dumps(
            {"actions": [{"type": "keys", "keys": "<ctrl>+<enter>"}]}
        ),
        windows=FakeWindows([NOTEPAD], NOTEPAD),
    )
    svc, kb, _o, feedback, _ = _service(agent=agent, ai_fallback=True)
    svc.handle("computer send the message", typing_active=False)
    assert kb.events == []
    assert feedback[-1][1] == "error" and "Blocked" in feedback[-1][0]


# ---------------------------------------------------------------------------
# Engine hook
# ---------------------------------------------------------------------------


def test_engine_does_not_type_consumed_commands():
    from saidcue.models import STTConfig
    from saidcue.stt import STTEngine, TextTyper, WhisperTranscriber

    class Model:
        def __init__(self):
            self.responses = ["Computer, save.", "Hello world."]

        def transcribe(self, audio, **kw):
            return {"text": self.responses.pop(0)}

    class Controller:
        def __init__(self):
            self.typed = []

        def type(self, t):
            self.typed.append(t)

    controller = Controller()
    handled = []

    def handler(text, typing_active):
        handled.append((text, typing_active))
        return text.startswith("Computer")

    class _Audio:
        sample_rate = 16000
        chunk_seconds = 0.1

    engine = STTEngine(
        STTConfig(silence_rms_threshold=0.0),
        audio_capture=_Audio(),
        transcriber=WhisperTranscriber(model_loader=lambda _n: Model()),
        typer=TextTyper(controller_factory=lambda: controller),
        command_handler=handler,
    )
    chunk = np.ones(1600, dtype=np.float32) * 0.5
    assert engine._process_chunk(chunk, type_into_window=True) == "command"
    assert engine._process_chunk(chunk, type_into_window=True) == "transcribed"
    assert controller.typed == ["Hello world. "]
    assert handled[0] == ("Computer, save.", True)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("computer skip ad computer skip ad", "skip ad"),
        ("Computer, skip ad. Computer, skip ad.", "skip ad"),
        ("computer open computer", "open computer"),  # not a repeat: left alone
    ],
)
def test_repeated_command_is_collapsed(text, expected):
    assert split_wake_word(text, ["computer"]) == expected


def test_skip_ad_survives_a_repeat_and_a_mishearing():
    matcher = CommandMatcher(COMMANDS)
    for spoken in ("computer skip ad computer skip ad", "computer skip add"):
        rest = split_wake_word(spoken, ["computer"])
        matched = matcher.match(rest)
        assert matched is not None and matched[0].id == "skip_ad"


@pytest.mark.parametrize(
    "spoken", ["press enter", "Hit enter.", "submit", "Send it!", "presiona enter"]
)
def test_enter_phrases_press_enter_while_dictating(spoken):
    from saidcue.voice_commands.dictation_edits import build_lookup, match_edit

    assert match_edit(spoken, build_lookup()) == "submit"


def test_enter_command_beats_the_click_command():
    matcher = CommandMatcher(COMMANDS)
    for spoken in ("computer press enter", "computer submit", "computer enter"):
        matched = matcher.match(split_wake_word(spoken, ["computer"]))
        assert matched is not None and matched[0].id == "press_enter", spoken
    # "press …" with a label that isn't a key still clicks (see the
    # spoken-key tests in test_computer_control.py).
    assert matcher.match("press the save button")[0].id == "press_key"
    enter = next(c for c in COMMANDS if c.id == "press_enter")
    assert enter.actions[0]["keys"] == "<enter>"


# ---------------------------------------------------------------------------
# Asking the AI assistant by voice
# ---------------------------------------------------------------------------


class _FakeAssistant:
    def __init__(self, ask_ok=True, listen_ok=True):
        self.questions, self.listened = [], 0
        self._ask_ok, self._listen_ok = ask_ok, listen_ok

    def ask(self, question):
        self.questions.append(question)
        return self._ask_ok

    def start_listening(self):
        self.listened += 1
        return self._listen_ok


def _spoken(text):
    from saidcue.voice_commands.parser import fill_slots

    matcher = CommandMatcher(COMMANDS)
    rest = split_wake_word(text, ["computer"])
    matched = matcher.match(rest)
    assert matched is not None, text
    command, slots = matched
    return command.id, [fill_slots(a, slots) for a in command.actions]


@pytest.mark.parametrize(
    "spoken, command_id, question",
    [
        ("computer, ask how tall is Everest", "ask", "how tall is everest"),
        ("computer ask the assistant who won in 1998", "ask", "who won in 1998"),
        ("computer, explain quantum computing", "explain", "Explain quantum computing"),
        ("computer, what is a heron?", "what_is", "What is a heron"),
        (
            "computer, what's the capital of France",
            "what_is",
            "What is the capital of france",
        ),
        ("computer, what are black holes", "what_are", "What are black holes"),
        ("computer, who is Ada Lovelace", "who_is", "Who is ada lovelace"),
        ("computer, why is the sky blue", "why", "Why is the sky blue"),
        (
            "computer, how many moons does Mars have",
            "how",
            "How many moons does mars have",
        ),
        (
            "computer, pregunta cuántos años tiene la luna",
            "ask",
            "cuantos anos tiene la luna",
        ),
    ],
)
def test_spoken_questions_become_ask_actions(spoken, command_id, question):
    cid, actions = _spoken(spoken)
    assert cid == command_id
    assert actions[0]["question"] == question
    assert actions[0]["type"] == "ask"


@pytest.mark.parametrize(
    "spoken",
    [
        "computer, ask a question",
        "computer, I have a question",
        "computer, assistant",
        "computer, ask the assistant",
        "computer, ask",
        "computer, pregunta",
    ],
)
def test_bare_ask_phrases_start_listening(spoken):
    cid, actions = _spoken(spoken)
    assert cid == "ask_listen"
    assert actions[0]["type"] == "ask_listen"


def test_ask_actions_reach_the_assistant():
    assistant = _FakeAssistant()
    runner = ActionRunner(assistant_getter=lambda: assistant)
    result = runner.run([{"type": "ask", "question": "How tall is Everest"}])
    assert result.ok and assistant.questions == ["How tall is Everest"]
    result = runner.run([{"type": "ask_listen"}])
    assert result.ok and assistant.listened == 1


def test_ask_actions_report_missing_or_busy_assistant():
    assert not ActionRunner().run([{"type": "ask", "question": "hi"}]).ok
    assert not ActionRunner().run([{"type": "ask_listen"}]).ok
    busy = _FakeAssistant(ask_ok=False, listen_ok=False)
    runner = ActionRunner(assistant_getter=lambda: busy)
    assert "busy" in runner.run([{"type": "ask", "question": "hi"}]).message
    assert not runner.run([{"type": "ask_listen"}]).ok


def test_ask_action_needs_a_question():
    with pytest.raises(ActionError):
        validate_action({"type": "ask", "question": "  "})
    assert validate_action({"type": "ask_listen"}) == {"type": "ask_listen"}
