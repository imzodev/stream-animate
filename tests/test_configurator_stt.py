from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from saidcue.configurator.sections import STTSection  # noqa: E402
from saidcue.models import STTConfig  # noqa: E402


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


def test_voice_section_wake_word_and_command_toggles(qapp):
    from PySide6.QtCore import Qt

    from saidcue.configurator.voice_section import VoiceCommandsSection
    from saidcue.voice_commands import (
        COMMANDS,
        WAKE_WORD_PRESETS,
        VoiceCommandsConfig,
    )

    cfg = VoiceCommandsConfig.from_dict(
        {
            "enabled": True,
            "wake_word": "assistant",
            "disabled_commands": ["open_twitch", "removed_in_future"],
        }
    )
    section = VoiceCommandsSection()
    section.populate(cfg)
    assert section.wake_word() == "assistant"
    assert [section._wake_word.itemText(i) for i in range(section._wake_word.count())][
        : len(WAKE_WORD_PRESETS)
    ] == list(WAKE_WORD_PRESETS)
    items = {i.data(0, Qt.ItemDataRole.UserRole): i for i in section._command_items()}
    assert set(items) == {c.id for c in COMMANDS}
    assert items["open_twitch"].checkState(0) == Qt.CheckState.Unchecked
    assert items["save"].checkState(0) == Qt.CheckState.Checked

    # Custom wake word + turn "save" off, Twitch back on.
    section._wake_word.setCurrentText("  okay   stream ")
    items["save"].setCheckState(0, Qt.CheckState.Unchecked)
    items["open_twitch"].setCheckState(0, Qt.CheckState.Checked)
    section._ai_fallback.setChecked(True)
    out = section.read()
    assert out["wake_word"] == "okay stream"
    assert out["ai_fallback"] is True
    assert out["disabled_commands"] == ["removed_in_future", "save"]

    section._wake_word.setCurrentText("  ")
    assert section.validate()  # enabled with no wake word is an error


def test_voice_section_guide_button_opens_dialog(qapp, monkeypatch):
    from saidcue.configurator.voice_guide import VoiceGuideDialog
    from saidcue.configurator.voice_section import VoiceCommandsSection

    opened = []
    monkeypatch.setattr(VoiceGuideDialog, "exec", lambda self: opened.append(self) or 0)
    section = VoiceCommandsSection()
    section._open_guide()
    assert len(opened) == 1 and isinstance(opened[0], VoiceGuideDialog)


def test_voice_guide_dialog_search_updates_browser(qapp):
    from saidcue.configurator.voice_guide import VoiceGuideDialog

    dialog = VoiceGuideDialog()
    full_text = dialog._browser.toPlainText()
    assert "Windows & tabs" in full_text and "How a command fires" in full_text

    dialog._search.setText("volume")
    filtered = dialog._browser.toPlainText()
    assert "set volume to" in filtered
    assert "How a command fires" not in filtered
