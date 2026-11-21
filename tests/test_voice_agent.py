"""The multi-step AI agent: clicks, rounds, confirmation and stopping."""

from __future__ import annotations

import json
import threading
import time
from typing import List

import pytest

from saidcue.voice_commands.actions import ActionRunner
from saidcue.voice_commands.agent_run import AgentMode
from saidcue.voice_commands.ai_agent import (
    AIAgent,
    PlanRejected,
    check_plan,
    is_risky,
    system_prompt,
)
from saidcue.voice_commands.config import VoiceCommandsConfig
from saidcue.voice_commands.elements import Target
from saidcue.voice_commands.service import VoiceCommandService

from test_computer_control import NOTEPAD, FakeKeyboard, FakeMouse, FakeWindows

TARGETS = [
    Target("Format", "button", (0, 0, 10, 10), (1,)),
    Target("Word Wrap", "button", (0, 20, 10, 10), (2,)),
    Target("Delete", "button", (0, 40, 10, 10), (3,)),
    Target("", "button", (0, 60, 10, 10), (4,)),
    Target("Search", "input", (0, 80, 10, 10), (5,)),
]


class Elements:
    def __init__(self):
        self.activated: List[tuple] = []

    def list_targets(self, hwnd):
        return list(TARGETS)

    def activate_target(self, hwnd, t, button="left"):
        self.activated.append((t.name, button))
        return "invoke"


def make(replies, *, background=False, **cfg):
    """A service whose AI answers with ``replies`` (JSON dicts) in order."""

    elements = Elements()
    kb = FakeKeyboard()
    windows = FakeWindows([NOTEPAD], foreground=NOTEPAD)
    runner = ActionRunner(
        keyboard_factory=lambda: kb,
        windows=windows,
        sleep=lambda _s: None,
        elements_factory=lambda: elements,
        mouse_factory=FakeMouse,
    )
    contexts: List[dict] = []
    replies = list(replies)

    def complete(system, user):
        contexts.append(json.loads(user))
        reply = replies.pop(0)
        return reply() if callable(reply) else json.dumps(reply)

    config = VoiceCommandsConfig.from_dict(
        {"enabled": True, "ai_fallback": True, **cfg}
    )
    agent = AIAgent(
        complete,
        windows=windows,
        elements=runner.label_targets,
        allow_clicks=config.ai_clicks,
        max_rounds=config.max_agent_steps,
    )
    feedback: List[tuple] = []
    svc = VoiceCommandService(
        config,
        runner=runner,
        engine_getter=lambda: None,
        agent=agent,
        on_feedback=lambda t, k: feedback.append((t, k)),
        background=background,
        sleep=lambda _s: None,
    )
    return svc, elements, kb, contexts, feedback


def wait_until(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------


def test_risky_words():
    assert is_risky("Delete")
    assert is_risky("Buy now")
    assert is_risky("Send message")
    assert is_risky("Eliminar archivo")
    assert not is_risky("Word Wrap")
    assert not is_risky("Format")  # the menu, not the disk
    assert is_risky("Format drive")
    assert not is_risky("Sender name")  # whole words only


def test_click_element_needs_permission_and_a_real_id():
    raw = {"actions": [{"type": "click_element", "id": 2}]}
    with pytest.raises(PlanRejected, match="click"):
        check_plan(raw, foreground=NOTEPAD, windows=[], targets=TARGETS)
    plan = check_plan(
        raw, foreground=NOTEPAD, windows=[], allow_clicks=True, targets=TARGETS
    )
    assert plan.risky == []
    with pytest.raises(PlanRejected, match="no element 9"):
        check_plan(
            {"actions": [{"type": "click_element", "id": 9}]},
            foreground=NOTEPAD,
            windows=[],
            allow_clicks=True,
            targets=TARGETS,
        )


def test_risky_steps_are_flagged_for_confirmation():
    plan = check_plan(
        {
            "actions": [
                {"type": "click_element", "id": 3},
                {"type": "click", "target": "Buy now"},
                {"type": "close_app", "name": "spotify"},
            ]
        },
        foreground=NOTEPAD,
        windows=[],
        allow_clicks=True,
        targets=TARGETS,
    )
    assert plan.risky == ["click “Delete”", "click “Buy now”", "close spotify"]


@pytest.mark.parametrize(
    "action",
    [
        {"type": "labels"},
        {"type": "grid"},
        {"type": "list_windows"},
        {"type": "mouse", "op": "click"},
        {"type": "keys", "keys": "<enter>"},
        {"type": "click_point", "x": 1, "y": 1, "target": "OK"},
    ],
)
def test_the_ai_still_cannot(action):
    with pytest.raises(PlanRejected):
        check_plan(
            {"actions": [action]},
            foreground=NOTEPAD,
            windows=[],
            allow_clicks=True,
            targets=TARGETS,
        )


def test_prompt_mentions_clicks_only_when_allowed():
    assert "click_element" in system_prompt(True)
    assert "click_element" not in system_prompt(False)


# ---------------------------------------------------------------------------
# Rounds
# ---------------------------------------------------------------------------


def test_a_request_in_two_rounds_with_element_ids():
    svc, elements, kb, contexts, feedback = make(
        [
            {"actions": [{"type": "click_element", "id": 1}], "done": False},
            {"actions": [{"type": "click_element", "id": 2}], "say": "Word wrap is on"},
        ]
    )
    assert svc.handle("computer, turn on word wrap", False)
    assert elements.activated == [("Format", "left"), ("Word Wrap", "left")]
    assert feedback[-1] == ("Word wrap is on", "ok")
    # The model sees named elements with ids; unnamed ones are left out.
    assert contexts[0]["elements"][:2] == [
        {"id": 1, "name": "Format", "type": "button"},
        {"id": 2, "name": "Word Wrap", "type": "button"},
    ]
    assert all(e["name"] for e in contexts[0]["elements"])
    assert contexts[0]["elements"][-1] == {
        "id": 5,
        "name": "Search",
        "type": "text box",
    }
    # Round 2 gets what happened in round 1.
    assert contexts[1]["round"] == 2
    assert contexts[1]["history"][0]["ok"] is True
    assert svc.mode is None


def test_a_single_round_plan_is_done_by_default():
    svc, _elements, kb, contexts, feedback = make(
        [{"actions": [{"type": "window", "op": "maximize"}], "say": "Maximized"}]
    )
    svc.handle("computer, make this bigger", False)
    assert len(contexts) == 1 and feedback[-1] == ("Maximized", "ok")


def test_the_agent_gives_up_after_the_round_limit():
    keep_going = {"actions": [{"type": "scroll", "direction": "down"}], "done": False}
    svc, _e, _kb, contexts, feedback = make([keep_going] * 3, max_agent_steps=3)
    svc.handle("computer, find the settings", False)
    assert len(contexts) == 3
    assert feedback[-1][1] == "error" and "after 3 steps" in feedback[-1][0]


def test_clicks_can_be_turned_off():
    svc, elements, _kb, contexts, feedback = make(
        [{"actions": [{"type": "click_element", "id": 1}]}], ai_clicks=False
    )
    svc.handle("computer, open the format menu", False)
    assert elements.activated == []
    assert "elements" not in contexts[0]
    assert feedback[-1][1] == "error" and "Blocked" in feedback[-1][0]


# ---------------------------------------------------------------------------
# Confirmation and stopping (worker thread)
# ---------------------------------------------------------------------------


def test_a_risky_click_waits_for_yes():
    svc, elements, _kb, _ctx, feedback = make(
        [{"actions": [{"type": "click_element", "id": 3}], "say": "Deleted"}],
        background=True,
    )
    svc.handle("computer, delete it", False)
    assert wait_until(lambda: any("yes or no" in t for t, _k in feedback))
    assert elements.activated == []
    assert isinstance(svc.mode, AgentMode)
    assert svc.handle("yes", False)  # no wake word needed
    assert wait_until(lambda: svc.mode is None)
    assert elements.activated == [("Delete", "left")]
    assert feedback[-1] == ("Deleted", "ok")


def test_no_means_nothing_is_clicked():
    svc, elements, _kb, _ctx, feedback = make(
        [{"actions": [{"type": "click_element", "id": 3}]}], background=True
    )
    svc.handle("computer, delete it", False)
    assert wait_until(lambda: any("yes or no" in t for t, _k in feedback))
    svc.handle("no", False)
    assert wait_until(lambda: svc.mode is None)
    assert elements.activated == []
    assert "nothing was clicked" in feedback[-1][0]


def test_stop_interrupts_between_rounds():
    gate = threading.Event()

    def slow():
        gate.wait(2)
        return json.dumps(
            {"actions": [{"type": "click_element", "id": 1}], "done": False}
        )

    svc, elements, _kb, _ctx, feedback = make([slow, slow], background=True)
    svc.handle("computer, do a long thing", False)
    assert wait_until(lambda: isinstance(svc.mode, AgentMode))
    assert svc.handle("stop", False)
    gate.set()
    assert wait_until(lambda: any(t == "Stopped" for t, _k in feedback))
    assert elements.activated == []


def test_the_stop_hotkey_and_a_new_command_also_stop_it():
    gate = threading.Event()

    def slow():
        gate.wait(2)
        return json.dumps(
            {"actions": [{"type": "click_element", "id": 1}], "done": False}
        )

    svc, elements, _kb, _ctx, feedback = make([slow, slow], background=True)
    svc.handle("computer, do a long thing", False)
    assert wait_until(lambda: isinstance(svc.mode, AgentMode))
    svc.stop_agent()
    gate.set()
    assert wait_until(lambda: any(t == "Stopped" for t, _k in feedback))
    assert elements.activated == []


def test_ordinary_speech_does_not_stop_the_agent():
    mode = AgentMode()
    assert mode.parse("I will stop by later") is None
    assert mode.parse("yes") is None  # nothing was asked
    assert mode.parse("stop") == "stop"
