from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from stream_companion.configurator.sections import STTSection  # noqa: E402
from stream_companion.models import STTConfig  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _cfg(**kw) -> STTConfig:
    base = dict(enabled=True, always_on=True)
    base.update(kw)
    return STTConfig(**base)


def test_new_settings_round_trip(qapp):
    section = STTSection()
    section.populate(
        _cfg(
            end_silence_ms=750,
            max_utterance_seconds=8.0,
            typing_method="keys",
            key_delay_ms=25,
        )
    )
    out = section.read()
    assert out.end_silence_ms == 750
    assert out.max_utterance_seconds == 8.0
    assert out.typing_method == "keys"
    assert out.key_delay_ms == 25


def test_fields_without_widgets_survive_a_save(qapp):
    section = STTSection()
    section.populate(_cfg(trigger_cooldown_ms=4000, chunk_seconds=1.0))
    out = section.read()
    assert out.trigger_cooldown_ms == 4000
    assert out.chunk_seconds == 1.0


def test_key_delay_only_editable_for_keystrokes(qapp):
    section = STTSection()
    section.populate(_cfg(typing_method="paste"))
    assert not section._key_delay_input.isEnabled()
    section._typing_method_combo.setCurrentIndex(
        section._typing_method_combo.findData("keys")
    )
    assert section._key_delay_input.isEnabled()
    # Disabling STT disables everything, including the delay.
    section._enabled_checkbox.setChecked(False)
    assert not section._key_delay_input.isEnabled()


def test_defaults_are_paste_and_500ms(qapp):
    section = STTSection()
    section.populate(None)
    section._enabled_checkbox.setChecked(True)
    out = section.read()
    assert out.typing_method == "paste"
    assert out.end_silence_ms == 500
    assert out.max_utterance_seconds == 12.0


def test_validate_rejects_out_of_range_phrase_settings(qapp):
    section = STTSection()
    errors = section.validate(_cfg(end_silence_ms=50, max_utterance_seconds=60.0))
    assert any("pause" in e for e in errors)
    assert any("phrase length" in e for e in errors)
    assert section.validate(_cfg()) == []


def test_indicator_settings_round_trip(qapp):
    section = STTSection()
    section.populate(_cfg(show_indicator=False, indicator_position="bottom-left"))
    assert not section._indicator_position_combo.isEnabled()
    out = section.read()
    assert out.show_indicator is False
    assert out.indicator_position == "bottom-left"

    section._show_indicator.setChecked(True)
    assert section._indicator_position_combo.isEnabled()
    assert section.read().show_indicator is True
