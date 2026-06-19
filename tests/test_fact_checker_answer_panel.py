"""Tests for the streaming answer panel.

Runs under ``QT_QPA_PLATFORM=offscreen`` so the widget can be
instantiated in CI without a display.

Tests target the structural behaviors that matter for
streaming:

* Public API contract (append_token, clear, set_phase,
  set_persona_label) still works end-to-end.
* The panel is thread-safe (worker thread can call
  append_token and the GUI thread sees the result).
* The persona accent changes the visible color theme.
* Lock state prevents drag-based repositioning.
* Question card shows after ``set_question`` and is hidden
  after ``clear``.
* Reasoning tokens are dropped from the visible answer.
* Phase pill text updates when ``set_phase`` is called.
"""

from __future__ import annotations

import os

# Must be set BEFORE importing PySide6 to take effect on the QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from stream_companion.fact_checker import _persona_accent as accent_mod
from stream_companion.fact_checker.answer_panel import AnswerPanel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture
def panel(qapp: QApplication) -> AnswerPanel:
    p = AnswerPanel()
    p.show()
    # ``clear`` and other public methods are thread-safe via
    # QueuedConnection. Process events so the slot runs before
    # the test starts interacting with the panel.
    p.clear()
    QApplication.processEvents()
    return p


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_panel_starts_empty(panel: AnswerPanel) -> None:
    assert panel.isVisible()
    # No answer text rendered yet.
    assert panel._answer_view.toPlainText() == ""


def test_panel_starts_in_idle_phase(panel: AnswerPanel) -> None:
    # The status bar shows "IDLE" by default.
    assert panel._status_bar._state_label.text() == "IDLE"


def test_panel_uses_custom_persona_by_default(panel: AnswerPanel) -> None:
    # No persona has been selected, so the panel uses the
    # neutral "custom" accent.
    assert panel._current_persona == "custom"
    assert panel._status_bar._name_label.text() == "CUSTOM"


# ---------------------------------------------------------------------------
# Public API: append_token / clear
# ---------------------------------------------------------------------------


def test_append_token_renders_text(panel: AnswerPanel) -> None:
    panel.append_token("Hello")
    panel.append_token(" world")
    QApplication.processEvents()
    text = panel._answer_view.toPlainText()
    assert "Hello world" in text


def test_append_token_reasoning_is_hidden(panel: AnswerPanel) -> None:
    """Reasoning tokens are dropped from the panel — the chain-of-
    thought is logged for debugging but never shown to the user.
    Only the final answer text is rendered."""
    panel.append_token("thinking...", kind="reasoning")
    panel.append_token("answer.", kind="answer")
    QApplication.processEvents()
    text = panel._answer_view.toPlainText()
    assert "thinking..." not in text
    assert "answer." in text


def test_append_token_ignores_empty(panel: AnswerPanel) -> None:
    panel.append_token("")
    QApplication.processEvents()
    # Empty tokens are no-ops.
    assert panel._answer_view.toPlainText() == ""


def test_clear_empties_text_and_question(panel: AnswerPanel) -> None:
    panel.append_token("something")
    panel.set_question("why?")
    QApplication.processEvents()
    panel.clear()
    # Wait for the question card's disappear animation (150ms)
    # to actually hide the widget.
    import time

    deadline = time.time() + 1.0
    while panel._question_card.isVisible() and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert panel._answer_view.toPlainText() == ""
    assert not panel._question_card.isVisible()


def test_clear_resets_footer_counter(panel: AnswerPanel) -> None:
    panel.append_token("hello world")
    QApplication.processEvents()
    panel.clear()
    QApplication.processEvents()
    assert panel._footer_bar._char_count == 0
    assert panel._footer_bar._chars_label.text() == "0 chars"


# ---------------------------------------------------------------------------
# Public API: set_phase
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phase,expected_label",
    [
        ("idle", "IDLE"),
        ("listening", "LISTENING"),
        ("thinking", "THINKING"),
        ("streaming", "STREAMING"),
        ("done", "DONE"),
        ("error", "ERROR"),
    ],
)
def test_set_phase_updates_state_pill(
    panel: AnswerPanel, phase: str, expected_label: str
) -> None:
    panel.set_phase(phase)
    QApplication.processEvents()
    assert panel._status_bar._state_label.text() == expected_label


def test_streaming_phase_starts_caret(panel: AnswerPanel) -> None:
    panel.set_phase("streaming")
    QApplication.processEvents()
    assert panel._answer_view._streaming is True


def test_done_phase_stops_caret(panel: AnswerPanel) -> None:
    panel.set_phase("streaming")
    panel.set_phase("done")
    QApplication.processEvents()
    assert panel._answer_view._streaming is False


# ---------------------------------------------------------------------------
# Public API: set_persona_label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "persona,expected_name",
    [
        ("fact_checker", "FACT-CHECKER"),
        ("eli5", "ELI5"),
        ("socratic", "SOCRATIC"),
        ("devils_advocate", "DEVIL'S ADVOCATE"),
        ("custom", "CUSTOM"),
    ],
)
def test_set_persona_label_updates_status_bar(
    panel: AnswerPanel, persona: str, expected_name: str
) -> None:
    panel.set_persona_label(persona)
    QApplication.processEvents()
    assert panel._status_bar._name_label.text() == expected_name


def test_set_persona_label_updates_glow_color(panel: AnswerPanel) -> None:
    panel.set_persona_label("eli5")
    QApplication.processEvents()
    # The shadow color should be the eli5 glow color (with
    # alpha applied). We just check that the RGB matches.
    assert panel._shadow is not None
    eli5_glow = accent_mod.accent_for("eli5").glow
    expected_rgb = tuple(int(eli5_glow[i : i + 2], 16) for i in (1, 3, 5))
    actual_rgb = panel._shadow.color().getRgb()[:3]
    assert actual_rgb == expected_rgb


def test_set_persona_label_updates_border_painter(panel: AnswerPanel) -> None:
    panel.set_persona_label("socratic")
    QApplication.processEvents()
    # Border painter's accent should reflect the new persona.
    assert panel._border._accent.persona == "socratic"


def test_unknown_persona_falls_back_to_custom(panel: AnswerPanel) -> None:
    panel.set_persona_label("nonexistent-persona")
    QApplication.processEvents()
    # accent_for() falls back to the "custom" persona.
    assert panel._current_persona == "nonexistent-persona"
    # But the visual identity is the custom one.
    assert panel._border._accent.persona == "custom"


# ---------------------------------------------------------------------------
# Question card
# ---------------------------------------------------------------------------


def test_set_question_shows_card(panel: AnswerPanel) -> None:
    panel.set_question("¿Qué es un LLM?")
    QApplication.processEvents()
    assert panel._question_card.isVisible()
    assert "LLM" in panel._question_card._text.text()


def test_clear_hides_question_card(panel: AnswerPanel) -> None:
    panel.set_question("question?")
    QApplication.processEvents()
    assert panel._question_card.isVisible()
    panel.clear()
    # The clear() hides the card immediately (no fade-out) so the
    # parent layout collapses the space and the answer view moves
    # up. Wait briefly for the layout to settle.
    import time

    deadline = time.time() + 1.0
    while panel._question_card.isVisible() and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert not panel._question_card.isVisible()


# ---------------------------------------------------------------------------
# Lock behavior
# ---------------------------------------------------------------------------


def test_status_bar_lock_toggles_locked_state(panel: AnswerPanel) -> None:
    assert panel._status_bar.is_locked() is False
    # Simulate the user clicking the lock button.
    panel._status_bar._on_lock_clicked()
    assert panel._status_bar.is_locked() is True
    assert panel._locked is True
    panel._status_bar._on_lock_clicked()
    assert panel._status_bar.is_locked() is False
    assert panel._locked is False


def test_lock_button_text_reflects_state(panel: AnswerPanel) -> None:
    panel._status_bar.set_locked(True)
    assert panel._status_bar._lock_btn.text() == "🔒"
    panel._status_bar.set_locked(False)
    assert panel._status_bar._lock_btn.text() == "🔓"


# ---------------------------------------------------------------------------
# Footer / telemetry
# ---------------------------------------------------------------------------


def test_set_model_updates_footer(panel: AnswerPanel) -> None:
    panel.set_model("deepseek-v4-flash")
    QApplication.processEvents()
    assert panel._footer_bar._model_label.text() == "deepseek-v4-flash"


def test_footer_char_count_tracks_tokens(panel: AnswerPanel) -> None:
    panel.append_token("Hello")
    panel.append_token(" world")
    QApplication.processEvents()
    assert panel._footer_bar._char_count == 11


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_append_token_from_worker_thread_marshals(
    panel: AnswerPanel, qapp: QApplication
) -> None:
    import threading

    done = threading.Event()

    def worker() -> None:
        panel.append_token("from-thread")
        done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    done.wait(timeout=2.0)
    t.join(timeout=1.0)
    # The bridge uses QueuedConnection, so the slot runs on the
    # next event-loop tick. processEvents drains that.
    QApplication.processEvents()
    assert "from-thread" in panel._answer_view.toPlainText()


def test_set_phase_from_worker_thread_marshals(
    panel: AnswerPanel, qapp: QApplication
) -> None:
    import threading

    done = threading.Event()

    def worker() -> None:
        panel.set_phase("error")
        done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    done.wait(timeout=2.0)
    t.join(timeout=1.0)
    QApplication.processEvents()
    assert panel._status_bar._state_label.text() == "ERROR"


# ---------------------------------------------------------------------------
# Border painter / animations
# ---------------------------------------------------------------------------


def test_border_painter_runs_timer_while_visible(panel: AnswerPanel) -> None:
    # Show the panel; the border painter timer should be active.
    panel.show()
    QApplication.processEvents()
    assert panel._border._timer.isActive()


def test_border_painter_stops_on_hide(panel: AnswerPanel) -> None:
    panel.show()
    QApplication.processEvents()
    panel.hide()
    QApplication.processEvents()
    assert not panel._border._timer.isActive()
