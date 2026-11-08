"""Tests for the floating tab picker.

Runs under ``QT_QPA_PLATFORM=offscreen`` so the widget can be
instantiated without a display.

What matters here:

* The list shows one numbered row per candidate.
* Clicking a row emits ``chosen`` with the 1-based index.
* The widget never takes the keyboard focus away from the app the user
  is working in (it is shown without activating).
* Tabs that share a name show their position, because the name alone
  cannot tell them apart.
* The voice thread can drive it: the calls are marshalled to the GUI
  thread.
"""

from __future__ import annotations

import os
import threading
import time

# Must be set BEFORE importing PySide6 to take effect on the QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from saidcue.voice_commands.tab_picker import TabPicker
from saidcue.voice_commands.tabs import TabCandidate


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app  # type: ignore[return-value]


@pytest.fixture
def picker(qapp) -> TabPicker:
    widget = TabPicker()
    yield widget
    widget.close()


def _candidate(name, *, hwnd=20, index=0, count=1, window="Chrome") -> TabCandidate:
    return TabCandidate(
        app="browser",
        hwnd=hwnd,
        window_title=window,
        tab_name=name,
        tab_index=index,
        tab_count=count,
        rank=0,
    )


def test_picker_lists_one_numbered_row_per_candidate(picker: TabPicker):
    picker.show_candidates(
        [_candidate("YouTube"), _candidate("YouTube - Lo-fi")], query="youtube"
    )
    assert picker.is_open
    assert picker._header.text() == "2 tabs match “youtube”"
    assert len(picker._rows) == 2


def test_clicking_a_row_emits_its_one_based_index(picker: TabPicker):
    picked: list[int] = []
    picker.chosen.connect(picked.append)
    picker.show_candidates([_candidate("YouTube"), _candidate("YouTube - Lo-fi")])

    picker._rows[1].clicked.emit(2)

    assert picked == [2]
    assert not picker.is_open  # the list closes after the pick


def test_dismiss_hides_the_list_and_notifies(picker: TabPicker):
    dismissed: list[bool] = []
    picker.dismissed.connect(lambda: dismissed.append(True))
    picker.show_candidates([_candidate("YouTube")])

    picker.dismiss()

    assert not picker.is_open
    assert dismissed == [True]


def test_tabs_with_the_same_name_show_their_position(picker: TabPicker):
    """Four "Windows PowerShell" tabs need the position to tell them apart."""

    picker.show_candidates(
        [
            _candidate("Windows PowerShell", index=i, count=4, window="Terminal")
            for i in range(4)
        ]
    )
    labels = [row._title_label.text() for row in picker._rows]
    assert labels[0] == "Windows PowerShell (tab 1/4)"
    assert labels[3] == "Windows PowerShell (tab 4/4)"


def test_the_window_title_only_shows_when_there_is_more_than_one(picker: TabPicker):
    picker.show_candidates([_candidate("YouTube", window="Gmail - Google Chrome")])
    assert picker._rows[0]._window_label.text() == ""

    picker.show_candidates(
        [
            _candidate("YouTube", hwnd=20, window="Gmail - Google Chrome"),
            _candidate("YouTube", hwnd=30, window="Docs - Google Chrome"),
        ]
    )
    assert picker._rows[0]._window_label.text() == "Gmail - Google Chrome"


def test_the_browser_suffix_is_stripped_from_the_title(picker: TabPicker):
    picker.show_candidates([_candidate("Some song - YouTube - Google Chrome", count=1)])
    assert picker._rows[0]._title_label.text() == "Some song - YouTube"


def test_the_picker_never_takes_the_keyboard_focus(picker: TabPicker):
    picker.show_candidates([_candidate("YouTube")])
    flags = picker.windowFlags()
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert picker.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_show_candidates_from_another_thread_marshals_to_the_gui_thread(
    picker: TabPicker, qapp: QApplication
):
    worker = threading.Thread(
        target=picker.show_candidates,
        args=([_candidate("YouTube")],),
        kwargs={"query": "youtube"},
    )
    worker.start()
    worker.join()

    deadline = time.time() + 2.0
    while not picker.is_open and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert picker.is_open
    assert picker._header.text() == "1 tab match “youtube”"
