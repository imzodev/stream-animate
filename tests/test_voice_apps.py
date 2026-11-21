"""Opening apps by name, picking windows, and answering pickers."""

from __future__ import annotations

from typing import List

from saidcue.voice_commands.actions import ActionRunner
from saidcue.voice_commands.apps import (
    AppEntry,
    StartApps,
    best_apps,
    parse_start_apps,
)
from saidcue.voice_commands.config import VoiceCommandsConfig
from saidcue.voice_commands.modes import ListMode
from saidcue.voice_commands.service import VoiceCommandService

from test_computer_control import (
    NOTEPAD,
    SPOTIFY,
    FakeElements,
    FakeKeyboard,
    FakeMouse,
    FakeWindows,
)

APPS = [
    AppEntry("Google Chrome", "Chrome"),
    AppEntry("Chrome Remote Desktop", "CRD"),
    AppEntry("OBS Studio", "OBS"),
    AppEntry("Visual Studio Code", "Code"),
    AppEntry("Visual Studio 2022", "VS"),
    AppEntry("Notepad", "Notepad"),
]


def names(apps):
    return [a.name for a in apps]


# ---------------------------------------------------------------------------
# Matching and the Start-menu list
# ---------------------------------------------------------------------------


def test_best_apps_prefers_whole_words_and_fewer_extra_words():
    assert names(best_apps(APPS, "chrome")) == ["Google Chrome"]
    assert names(best_apps(APPS, "obs")) == ["OBS Studio"]
    assert names(best_apps(APPS, "code")) == ["Visual Studio Code"]
    assert names(best_apps(APPS, "notepad")) == ["Notepad"]
    assert best_apps(APPS, "excel") == []


def test_best_apps_returns_ties_for_the_picker():
    assert names(best_apps(APPS, "visual studio")) == [
        "Visual Studio Code",
        "Visual Studio 2022",
    ]


def test_parse_start_apps_skips_junk_and_duplicates():
    raw = (
        '[{"Name":"OBS Studio","AppID":"obs"},{"Name":"Uninstall OBS","AppID":"u"},'
        '{"Name":"OBS Studio","AppID":"obs"},{"Name":"OBS on the Web","AppID":"w"}]'
    )
    assert names(parse_start_apps(raw)) == ["OBS Studio"]
    assert names(parse_start_apps('{"Name":"Solo","AppID":"s"}')) == ["Solo"]
    assert parse_start_apps("") == []


def test_start_apps_caches_and_rereads_once_on_a_miss():
    calls = []
    lists = iter(
        [
            '[{"Name":"Notepad","AppID":"n"}]',
            '[{"Name":"Notepad","AppID":"n"},{"Name":"Spotify","AppID":"s"}]',
        ]
    )
    now = [0.0]

    def reader():
        calls.append(1)
        return next(lists)

    apps = StartApps(reader=reader, launcher=lambda _id: None, clock=lambda: now[0])
    assert names(apps.find("notepad")) == ["Notepad"]
    assert names(apps.find("notepad")) == ["Notepad"]
    assert len(calls) == 1
    now[0] = 10.0  # installed since the list was read: a miss re-reads once
    assert names(apps.find("spotify")) == ["Spotify"]
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Through the voice service
# ---------------------------------------------------------------------------


class FakeApps:
    def __init__(self, apps):
        self.list = list(apps)
        self.launched: List[str] = []

    def find(self, query):
        return best_apps(self.list, query)

    def launch(self, app):
        self.launched.append(app.name)


class FakePicker:
    def __init__(self):
        self.shown: List[tuple] = []
        self.dismissed = 0

    def show_candidates(self, candidates, *, query="", noun="tab"):
        self.shown.append((list(candidates), query, noun))

    def dismiss(self):
        self.dismissed += 1


def make_service(apps=(), windows=None, agent=None, **cfg):
    kb = FakeKeyboard()
    opened: List[str] = []
    fake_apps = FakeApps(apps)
    windows = windows or FakeWindows([NOTEPAD, SPOTIFY], foreground=NOTEPAD)
    runner = ActionRunner(
        keyboard_factory=lambda: kb,
        windows=windows,
        launcher=opened.append,
        sleep=lambda _s: None,
        elements_factory=FakeElements,
        mouse_factory=FakeMouse,
        apps_factory=lambda: fake_apps,
    )
    picker = FakePicker()
    feedback: List[tuple] = []
    svc = VoiceCommandService(
        VoiceCommandsConfig.from_dict({"enabled": True, **cfg}),
        runner=runner,
        engine_getter=lambda: None,
        agent=agent,
        on_feedback=lambda t, k: feedback.append((t, k)),
        picker=picker,
    )
    return svc, fake_apps, picker, feedback, opened, windows


def test_open_any_app_by_name():
    svc, apps, _picker, feedback, _opened, _w = make_service(APPS)
    assert svc.handle("computer, open OBS", False)
    assert apps.launched == ["OBS Studio"]
    assert feedback[-1] == ("Opening OBS Studio", "ok")
    # The built-in "open youtube" still wins over the app search.
    assert svc.handle("computer, open youtube", False)
    assert apps.launched == ["OBS Studio"]


def test_several_apps_open_a_picker_answered_without_the_wake_word():
    svc, apps, picker, _fb, _opened, _w = make_service(APPS)
    assert svc.handle("computer, launch visual studio", False)
    candidates, query, noun = picker.shown[0]
    assert noun == "app" and query == "visual studio" and len(candidates) == 2
    assert isinstance(svc.mode, ListMode)
    # While the picker is open, "two" needs no wake word...
    assert svc.handle("two", False)
    assert apps.launched == ["Visual Studio 2022"]
    assert svc.mode is None
    # ...but afterwards "two" is ordinary speech again.
    assert not svc.handle("two", False)


def test_answers_without_the_wake_word_can_be_turned_off():
    svc, apps, _picker, _fb, _opened, _w = make_service(
        APPS, overlay_without_wake_word=False
    )
    svc.handle("computer, launch visual studio", False)
    assert not svc.handle("two", False)
    assert svc.handle("computer, two", False)
    assert apps.launched == ["Visual Studio 2022"]


def test_ordinary_speech_does_not_close_an_open_picker():
    svc, apps, picker, _fb, _opened, _w = make_service(APPS)
    svc.handle("computer, launch visual studio", False)
    assert not svc.handle("I think it is this one", True)
    assert isinstance(svc.mode, ListMode)
    assert svc.handle("cancel", False)
    assert svc.mode is None and picker.dismissed == 1
    assert apps.launched == []


def test_open_a_site_when_no_app_matches():
    svc, _apps, _picker, feedback, opened, _w = make_service(APPS)
    assert svc.handle("computer, open github dot com", False)
    assert opened == ["https://github.com"]
    assert svc.handle("computer, open nothing like that", False)
    assert feedback[-1] == ("No app called “nothing like that”", "error")


def test_an_unknown_app_goes_to_the_ai_when_it_is_on():
    from saidcue.voice_commands.ai_agent import Plan

    class Agent:
        def __init__(self):
            self.requests = []

        def plan(self, request, names=()):
            self.requests.append(request)
            return Plan(
                actions=[{"type": "open", "target": "https://weather.com"}], say="ok"
            )

    agent = Agent()
    svc, _apps, _picker, _fb, opened, _w = make_service(
        APPS, agent=agent, ai_fallback=True
    )
    assert svc.handle("computer, open the weather", False)
    assert agent.requests == ["open the weather"]
    assert opened == ["https://weather.com"]


def test_close_an_app_by_process_name():
    svc, _apps, _picker, _fb, _opened, windows = make_service(APPS)
    assert svc.handle("computer, close spotify", False)
    assert windows.calls[-1] == ("close", SPOTIFY.hwnd)
    # "close window" keeps its own meaning: the window in front.
    assert svc.handle("computer, close window", False)
    assert windows.calls[-1] == ("close", NOTEPAD.hwnd)


def test_switch_window_lists_windows_and_focuses_the_pick():
    svc, _apps, picker, _fb, _opened, windows = make_service(APPS)
    assert svc.handle("computer, switch window", False)
    items, _query, noun = picker.shown[0]
    assert noun == "window"
    assert [i.label for i in items] == [NOTEPAD.title, SPOTIFY.title]
    assert [i.detail for i in items] == ["notepad", "spotify"]
    svc.resolve_choice(2)  # by mouse
    assert windows.calls[-1] == ("focus", SPOTIFY.hwnd)
    assert svc.mode is None
