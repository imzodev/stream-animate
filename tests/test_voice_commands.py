from __future__ import annotations

import json
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
from stream_companion.voice_commands.config import (
    ActionError,
    VoiceCommandsConfig,
    validate_action,
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
NOTEPAD = WindowInfo(30, "notas.txt - Notepad", "Notepad")


def make_runner(windows=None, tabs=None, typer=None, engine=None):
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
    )
    return runner, kb, opened


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_normalize_strips_case_accents_punctuation():
    assert normalize("¡Computadora, Pestaña  Línea!") == "computadora pestana linea"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Computadora, abre YouTube.", "abre youtube"),
        ("compu abre spotify", "abre spotify"),
        ("Computadora.", ""),
        ("Hola computadora abre", None),  # wake word must come first
        ("Interesante.", None),
    ],
)
def test_split_wake_word(text, expected):
    assert split_wake_word(text, ["computadora", "compu"]) == expected


def _cmds(raw):
    return VoiceCommandsConfig.from_dict({"enabled": True, "commands": raw}).commands


def test_matcher_slots_and_most_specific_wins():
    commands = _cmds(
        [
            {
                "phrases": ["abre {app}"],
                "actions": [{"type": "open", "target": "{app}"}],
            },
            {
                "phrases": ["abre youtube"],
                "actions": [{"type": "open", "target": "yt"}],
            },
            {"phrases": ["guarda"], "actions": [{"type": "keys", "keys": "<ctrl>+s"}]},
        ]
    )
    m = CommandMatcher(commands)
    cmd, slots = m.match("abre youtube")
    assert cmd.actions[0]["target"] == "yt" and slots == {}
    cmd, slots = m.match("Abre la calculadora")
    assert slots == {"app": "la calculadora"}
    assert m.match("guarda")[0].actions[0]["keys"] == "<ctrl>+s"
    assert m.match("guarda todo") is None  # exact templates need an exact match
    assert m.match("algo raro") is None


def test_matcher_rejects_slot_not_at_end():
    with pytest.raises(ValueError):
        CommandMatcher(
            _cmds([{"phrases": ["{x} ahora"], "actions": [{"type": "wait", "ms": 1}]}])
        )


def test_fill_slots_recurses():
    action = {"type": "switch_tab", "app": "terminal", "tab": "{name}"}
    assert fill_slots(action, {"name": "servidor"})["tab"] == "servidor"


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
        "wake_words": ["computadora"],
        "ai_fallback": True,
        "commands": [
            {"phrases": ["guarda"], "actions": [{"type": "keys", "keys": "<ctrl>+s"}]}
        ],
    }
    cfg = VoiceCommandsConfig.from_dict(raw)
    again = VoiceCommandsConfig.from_dict(cfg.to_dict())
    assert again.wake_words == ("computadora",) and again.ai_fallback
    assert again.commands[0].actions[0]["keys"] == "<ctrl>+s"
    with pytest.raises(ActionError):
        VoiceCommandsConfig.from_dict({"commands": [{"phrases": ["x"], "actions": []}]})
    with pytest.raises(ActionError):
        VoiceCommandsConfig.from_dict({"wake_words": ["  "]})


# ---------------------------------------------------------------------------
# Windows / tabs helpers
# ---------------------------------------------------------------------------


def test_find_window_prefers_prefix_then_front_most():
    wins = [CHROME, NOTEPAD, WindowInfo(40, "Mis notas importantes", "X")]
    assert find_window(wins, "notas").hwnd == 30  # starts with "notas"
    assert find_window(wins, "GMAIL").hwnd == 20
    assert find_window(wins, "excel") is None


def test_best_tab_ranking():
    names = ["Windows PowerShell", "servidor web", "servidor"]
    assert best_tab(names, "Servidor") == 2  # exact beats prefix
    assert best_tab(names, "power") == 0
    assert best_tab(names, "nada") is None


@pytest.mark.parametrize(
    "text, n", [("2", 2), ("dos", 2), ("Segunda", 2), ("10", None), ("web", None)]
)
def test_tab_number(text, n):
    assert tab_number(text) == n


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def test_switch_tab_by_name_selects_and_focuses():
    tabs = FakeTabs({"terminal": [(10, "PowerShell"), (10, "servidor")]})
    windows = FakeWindows([TERMINAL, CHROME])
    runner, _kb, _ = make_runner(windows=windows, tabs=tabs)
    result = runner.run([{"type": "switch_tab", "app": "terminal", "tab": "Servidor"}])
    assert result.ok and tabs.selected == ["servidor"] and windows.focused == [10]


def test_switch_tab_by_number_uses_shortcut():
    windows = FakeWindows([CHROME, TERMINAL])
    runner, kb, _ = make_runner(windows=windows)
    assert runner.run([{"type": "switch_tab", "app": "terminal", "tab": "tres"}]).ok
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
            {"type": "focus_window", "title": "notas"},
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
# Dictation edits
# ---------------------------------------------------------------------------


def test_dictation_edit_requires_whole_utterance():
    lookup = build_lookup()
    assert match_edit("Nueva línea.", lookup) == "newline"
    assert match_edit("Borra eso", lookup) == "undo_phrase"
    assert match_edit("Escribe una nueva línea de código", lookup) is None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _service(raw_commands=(), *, typer=None, engine=None, agent=None, **cfg):
    config = VoiceCommandsConfig.from_dict(
        {
            "enabled": True,
            "wake_words": ["computadora"],
            "commands": list(raw_commands),
            **cfg,
        }
    )
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
    svc, _kb, opened, feedback, _ = _service(
        [{"phrases": ["abre youtube"], "actions": [{"type": "open", "target": "yt"}]}]
    )
    assert svc.handle("hoy vamos a abrir youtube", typing_active=True) is False
    assert opened == [] and feedback == []


def test_service_runs_wake_word_command():
    svc, _kb, opened, feedback, _ = _service(
        [{"phrases": ["abre youtube"], "actions": [{"type": "open", "target": "yt"}]}]
    )
    assert svc.handle("Computadora, abre YouTube.", typing_active=False) is True
    assert opened == ["yt"] and feedback[-1][1] == "ok"


def test_service_unknown_command_without_ai():
    svc, _kb, _o, feedback, _ = _service()
    assert svc.handle("computadora haz magia", typing_active=False) is True
    assert feedback[-1] == ("Unknown command: haz magia", "error")


def test_service_disabled_consumes_nothing():
    svc, *_ = _service(enabled=False)
    assert svc.handle("computadora guarda", typing_active=False) is False


def test_service_dictation_edits_only_while_typing():
    typer = FakeTyper(last="hola mundo ")
    svc, kb, _o, feedback, _ = _service(typer=typer)
    assert svc.handle("Nueva línea.", typing_active=False) is False
    assert svc.handle("Borra eso.", typing_active=True) is True
    # pynput parses <backspace> to virtual-key 8 (VK_BACK) on Windows.
    assert len(kb.presses()) == len("hola mundo ")
    assert feedback[-1] == ("Erased last phrase", "ok")


def test_service_stop_dictation_edit():
    engine = FakeEngine()
    engine.is_active = True
    svc, *_ = _service(engine=engine)
    assert svc.handle("Deja de escribir.", typing_active=True) is True
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


def test_check_plan_allows_typing_after_leaving_terminal():
    plan = check_plan(
        {
            "actions": [
                {"type": "focus_window", "title": "notas"},
                {"type": "type_text", "text": "hola"},
            ],
            "say": "Listo",
        },
        foreground=TERMINAL,
        windows=[TERMINAL, NOTEPAD],
    )
    assert len(plan.actions) == 2 and plan.say == "Listo"


def test_service_ai_fallback_runs_validated_plan():
    reply = json.dumps(
        {"actions": [{"type": "open", "target": "calc"}], "say": "Abriendo calculadora"}
    )
    seen = {}

    def complete(system, user):
        seen["context"] = json.loads(user)
        return reply

    agent = AIAgent(
        complete, windows=FakeWindows([NOTEPAD], NOTEPAD), tabs=FakeTabs({})
    )
    svc, _kb, opened, feedback, _ = _service(agent=agent, ai_fallback=True)
    assert svc.handle("computadora abre la calculadora", typing_active=False)
    assert opened == ["calc"]
    assert feedback[0] == ("Thinking…", "busy")
    assert feedback[-1] == ("Abriendo calculadora", "ok")
    assert seen["context"]["request"] == "abre la calculadora"
    assert seen["context"]["foreground_window"] == "notas.txt - Notepad"


def test_service_ai_blocked_plan_runs_nothing():
    agent = AIAgent(
        lambda s, u: json.dumps(
            {"actions": [{"type": "keys", "keys": "<ctrl>+<enter>"}]}
        ),
        windows=FakeWindows([NOTEPAD], NOTEPAD),
    )
    svc, kb, _o, feedback, _ = _service(agent=agent, ai_fallback=True)
    svc.handle("computadora envia el mensaje", typing_active=False)
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
            self.responses = ["Computadora, guarda.", "Hola mundo."]

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
        return text.startswith("Computadora")

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
    assert controller.typed == ["Hola mundo. "]
    assert handled[0] == ("Computadora, guarda.", True)
