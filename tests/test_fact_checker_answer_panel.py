"""Tests for the streaming answer panel.

Runs under ``QT_QPA_PLATFORM=offscreen`` so the widget can be
instantiated in CI without a display.
"""

from __future__ import annotations

import os

# Must be set BEFORE importing PySide6 to take effect on the QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

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
    p.clear()
    return p


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_panel_starts_empty(panel: AnswerPanel) -> None:
    assert panel.isVisible()
    assert panel._text.toPlainText() == ""


def test_panel_phase_label_initially_idle(panel: AnswerPanel) -> None:
    assert panel._phase_label.text() == "idle"


# ---------------------------------------------------------------------------
# Thread-marshalled API
# ---------------------------------------------------------------------------


def test_append_token_renders_text(panel: AnswerPanel) -> None:
    panel.append_token("Hello")
    panel.append_token(" world")
    QApplication.processEvents()
    assert panel._text.toPlainText() == "Hello world"


def test_append_token_ignores_empty(panel: AnswerPanel) -> None:
    panel.append_token("")
    QApplication.processEvents()
    assert panel._text.toPlainText() == ""


def test_clear_empties_text(panel: AnswerPanel) -> None:
    panel.append_token("something")
    QApplication.processEvents()
    assert panel._text.toPlainText() == "something"
    panel.clear()
    QApplication.processEvents()
    assert panel._text.toPlainText() == ""


def test_set_phase_updates_label(panel: AnswerPanel) -> None:
    panel.set_phase("streaming")
    QApplication.processEvents()
    assert panel._phase_label.text() == "streaming"


def test_set_persona_label_updates_label(panel: AnswerPanel) -> None:
    panel.set_persona_label("ELI5")
    QApplication.processEvents()
    assert panel._persona_label.text() == "ELI5"


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
    # The bridge uses QueuedConnection, so the slot runs on the next
    # event-loop tick. processEvents drains that.
    QApplication.processEvents()
    assert panel._text.toPlainText() == "from-thread"
