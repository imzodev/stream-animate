from __future__ import annotations

import json
import re
from typing import List

import numpy as np
import pytest

from stream_companion.voice_commands.actions import ActionRunner, tab_number
from stream_companion.voice_commands.ai_agent import (
    AIAgent,
    PlanRejected,
    check_plan,
    extract_json,
)
from stream_companion.voice_commands.audio import parse_percent
from stream_companion.voice_commands.catalog import (
    COMMANDS,
    DEFAULT_WAKE_WORD,
    WAKE_WORD_PRESETS,
    CatalogCommand,
)
from stream_companion.voice_commands.config import (
    ActionError,
    VoiceCommandsConfig,
    validate_action,
)
from stream_companion.voice_commands.elements import (
    Candidate,
    ElementHit,
    best_element,
)
from stream_companion.voice_commands.dictation_edits import (
    build_lookup,
    match_edit,
)
from stream_companion.voice_commands.parser import (
    CommandMatcher,
    fill_slots,
    normalize,
    split_wake_word,
)
from stream_companion.voice_commands.service import VoiceCommandService
from stream_companion.voice_commands.tabs import TabMatch, best_tab
from stream_companion.voice_commands.windows import WindowInfo, find_window

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

    def list_tabs(self, app):
        return [n for _h, n in self.tabs.get(app, [])]

    def select(self, app, query):
        entries = self.tabs.get(app, [])
        idx = best_tab([n for _h, n in entries], query)
        if idx is None:
            return None
        hwnd, name = entries[idx]
        self.selected.append(name)
        return TabMatch(hwnd, "win", name)


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

    def click(self, hwnd, queries, exact=False):
        idx = best_element(self.candidates, queries, exact)
        if idx is None:
            return None
        c = self.candidates[idx]
        self.clicked.append((hwnd, c.name))
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


def test_matcher_rejects_slot_not_at_end():
    with pytest.raises(ValueError):
        CommandMatcher([_cmd(["{x} now"], {"type": "wait", "ms": 1})])


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
    assert runner.run([{"type": "scroll", "direction": "down"}]).message == "Scrolled down"
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
    from stream_companion.configurator.voice_guide import build_guide_html

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


def _service(*, typer=None, engine=None, agent=None, **cfg):
    config = VoiceCommandsConfig.from_dict({"enabled": True, **cfg})
    engine = engine or FakeEngine(typer=typer)
    runner, kb, opened = make_runner(typer=typer, engine=engine)
    feedback: List[tuple] = []
    svc = VoiceCommandService(
        config,
        runner=runner,
        engine_getter=lambda: engine,
        agent=agent,
        on_feedback=lambda t, k: feedback.append((t, k)),
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
    from stream_companion.models import STTConfig
    from stream_companion.stt import STTEngine, TextTyper, WhisperTranscriber

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
