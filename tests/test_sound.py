from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

import pytest

from stream_companion.sound import SoundPlayer


class DummySound:
    def __init__(self) -> None:
        self.play_calls: list[Dict[str, int]] = []

    def play(self, *, loops: int = 0) -> None:
        self.play_calls.append({"loops": loops})


class DummyMixer:
    def __init__(self) -> None:
        self.init_calls: list[Dict[str, int]] = []
        self.quit_calls = 0
        self.stop_calls = 0
        self.sounds: Dict[Path, DummySound] = {}

    def init(self, *, frequency: int, size: int, channels: int, buffer: int) -> None:
        self.init_calls.append(
            {
                "frequency": frequency,
                "size": size,
                "channels": channels,
                "buffer": buffer,
            }
        )

    def quit(self) -> None:
        self.quit_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def Sound(self, path: str) -> DummySound:  # noqa: N802 - mimicking pygame API
        dummy = DummySound()
        self.sounds[Path(path)] = dummy
        return dummy


@pytest.fixture()
def temp_sound(tmp_path: Path) -> Path:
    path = tmp_path / "sound.wav"
    path.write_bytes(b"fake sound data")
    return path


@pytest.fixture()
def player(temp_sound: Path) -> tuple[SoundPlayer, DummyMixer]:
    mixer = DummyMixer()
    instance = SoundPlayer(mixer=mixer, logger=logging.getLogger("test.sound"))
    return instance, mixer


def test_initialize_is_idempotent(player: tuple[SoundPlayer, DummyMixer]) -> None:
    sound_player, mixer = player
    sound_player.initialize()
    sound_player.initialize()

    assert len(mixer.init_calls) == 1


def test_load_invalid_path_logs_warning(player: tuple[SoundPlayer, DummyMixer]) -> None:
    sound_player, _ = player
    assert sound_player.load("sfx", "missing.wav") is False


def test_load_valid_path_registers_sound(
    player: tuple[SoundPlayer, DummyMixer], temp_sound: Path
) -> None:
    sound_player, mixer = player
    assert sound_player.load("sfx", temp_sound.as_posix()) is True
    assert "sfx" in sound_player.loaded_sounds()
    assert temp_sound in mixer.sounds


def test_play_loaded_sound(
    player: tuple[SoundPlayer, DummyMixer], temp_sound: Path
) -> None:
    sound_player, _ = player
    sound_player.load("sfx", temp_sound.as_posix())
    assert sound_player.play("sfx", loops=2) is True


def test_play_missing_sound_returns_false(
    player: tuple[SoundPlayer, DummyMixer],
) -> None:
    sound_player, _ = player
    assert sound_player.play("missing") is False


def test_unload_sound(player: tuple[SoundPlayer, DummyMixer], temp_sound: Path) -> None:
    sound_player, _ = player
    sound_player.load("sfx", temp_sound.as_posix())
    assert sound_player.unload("sfx") is True
    assert sound_player.unload("sfx") is False


def test_shutdown_quits_mixer(
    player: tuple[SoundPlayer, DummyMixer], temp_sound: Path
) -> None:
    sound_player, mixer = player
    sound_player.load("sfx", temp_sound.as_posix())
    sound_player.shutdown()

    assert mixer.quit_calls == 1
    assert mixer.stop_calls == 1
    assert sound_player.loaded_sounds() == {}
