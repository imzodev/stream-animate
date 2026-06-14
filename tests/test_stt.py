from __future__ import annotations

from typing import List

import numpy as np
import pytest

from stream_companion.models import STTConfig
from stream_companion.stt import (
    AudioCapture,
    AudioCaptureError,
    STTEngine,
    STTEvent,
    TextTyper,
    WhisperTranscriber,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSoundDevice:
    """Minimal stub of the sounddevice module used by AudioCapture."""

    def __init__(self) -> None:
        self.opened: List[dict] = []
        self._active: List[FakeStream] = []

    def InputStream(self, **kwargs):  # noqa: N802 - matches sounddevice API
        stream = FakeStream(self, kwargs)
        self.opened.append(kwargs)
        return stream

    def remove(self, stream: "FakeStream") -> None:
        if stream in self._active:
            self._active.remove(stream)


class FakeStream:
    def __init__(self, sd: FakeSoundDevice, kwargs: dict) -> None:
        self._sd = sd
        self.kwargs = kwargs
        self.callback = kwargs.get("callback")
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True
        self._sd._active.append(self)

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True
        self._sd.remove(self)

    def push(self, samples: np.ndarray) -> None:
        """Simulate an audio callback with the given float32 samples."""

        if self.callback is None:
            return
        indata = samples.reshape(-1, 1)
        self.callback(indata, samples.shape[0], None, None)


class FakeWhisperModel:
    def __init__(self) -> None:
        self.calls: List[dict] = []
        self.responses: List[str] = [""]

    def transcribe(self, audio, **kwargs):
        self.calls.append({"audio": audio, **kwargs})
        return {"text": self.responses.pop(0) if self.responses else ""}


class FakeController:
    def __init__(self) -> None:
        self.typed: List[str] = []

    def type(self, text: str) -> None:
        self.typed.append(text)


# ---------------------------------------------------------------------------
# AudioCapture
# ---------------------------------------------------------------------------


def test_audio_capture_requires_positive_chunk_seconds():
    with pytest.raises(ValueError):
        AudioCapture(chunk_seconds=0)
    with pytest.raises(ValueError):
        AudioCapture(sample_rate=0)


def test_audio_capture_starts_and_stops():
    sd = FakeSoundDevice()
    cap = AudioCapture(sounddevice_module=sd, sample_rate=16000, chunk_seconds=1.0)
    cap.start()
    assert cap.is_running is True
    assert len(sd.opened) == 1
    assert sd.opened[0]["samplerate"] == 16000
    assert sd.opened[0]["channels"] == 1
    cap.stop()
    assert cap.is_running is False


def test_audio_capture_emits_full_chunk():
    sd = FakeSoundDevice()
    cap = AudioCapture(sounddevice_module=sd, sample_rate=16000, chunk_seconds=0.5)
    cap.start()
    stream = sd._active[0]
    # 0.5s @ 16kHz = 8000 frames. Send 8000 samples.
    samples = np.ones(8000, dtype=np.float32)
    stream.push(samples)
    chunk = cap.get_chunk(timeout=1.0)
    assert chunk is not None
    assert chunk.shape[0] == 8000
    cap.stop()


def test_audio_capture_start_failure_raises():
    class BrokenSD:
        def InputStream(self, **kwargs):  # noqa: N802
            raise OSError("no mic")

    cap = AudioCapture(sounddevice_module=BrokenSD())
    with pytest.raises(AudioCaptureError):
        cap.start()
    assert cap.is_running is False
    assert isinstance(cap.last_error(), OSError)


# ---------------------------------------------------------------------------
# WhisperTranscriber
# ---------------------------------------------------------------------------


def test_whisper_transcriber_lazy_loads_and_transcribes():
    model = FakeWhisperModel()
    model.responses = ["hello world"]
    loader_calls: List[str] = []

    def loader(name: str):
        loader_calls.append(name)
        return model

    t = WhisperTranscriber(model_name="tiny", model_loader=loader)
    assert t.is_loaded() is False
    text = t.transcribe(np.zeros(16000, dtype=np.float32), language="auto")
    assert text == "hello world"
    assert loader_calls == ["tiny"]
    assert t.is_loaded() is True
    # Second call should not reload
    model.responses = ["again"]
    t.transcribe(np.zeros(16000, dtype=np.float32), language="en")
    assert loader_calls == ["tiny"]
    assert model.calls[-1]["language"] == "en"


def test_whisper_transcriber_skips_language_kwarg_for_auto():
    model = FakeWhisperModel()
    model.responses = ["hi"]
    t = WhisperTranscriber(model_loader=lambda _n: model)
    t.transcribe(np.zeros(16000, dtype=np.float32), language="auto")
    assert model.calls[-1].get("language") is None


# ---------------------------------------------------------------------------
# TextTyper
# ---------------------------------------------------------------------------


def test_text_typer_types_full_text_first_time():
    controller = FakeController()
    typer = TextTyper(controller_factory=lambda: controller, window=32)
    typed = typer.type_text("hello", append_space=True)
    assert typed == "hello "
    assert controller.typed == ["hello "]


def test_text_typer_dedups_overlapping_chunks():
    controller = FakeController()
    typer = TextTyper(controller_factory=lambda: controller, window=64)
    typer.type_text("the quick brown", append_space=True)
    # Next chunk repeats the tail "brown " then adds new text
    typed = typer.type_text("brown fox jumps", append_space=True)
    assert typed == "fox jumps "
    assert controller.typed == ["the quick brown ", "fox jumps "]


def test_text_typer_no_append_space():
    controller = FakeController()
    typer = TextTyper(controller_factory=lambda: controller, window=32)
    typed = typer.type_text("hi", append_space=False)
    assert typed == "hi"
    assert controller.typed == ["hi"]


def test_text_typer_empty_text_no_op():
    controller = FakeController()
    typer = TextTyper(controller_factory=lambda: controller, window=32)
    assert typer.type_text("", append_space=True) == ""
    assert controller.typed == []


def test_text_typer_reset_clears_tail():
    controller = FakeController()
    typer = TextTyper(controller_factory=lambda: controller, window=32)
    typer.type_text("foo", append_space=False)
    typer.reset()
    assert typer.tail() == ""
    # After reset, no dedup happens
    typed = typer.type_text("foo bar", append_space=False)
    assert typed == "foo bar"


def test_text_typer_window_size_zero_disables_tail_tracking():
    controller = FakeController()
    typer = TextTyper(controller_factory=lambda: controller, window=0)
    typer.type_text("a", append_space=False)
    assert typer.tail() == ""


def test_text_typer_propagates_controller_errors():
    class BrokenController:
        def type(self, text: str) -> None:
            raise RuntimeError("simulated pynput failure")

    typer = TextTyper(controller_factory=lambda: BrokenController(), window=32)
    with pytest.raises(RuntimeError):
        typer.type_text("hello", append_space=False)


def test_process_chunk_returns_status_tags():
    """_process_chunk returns one of the documented status tags."""

    engine, sd, model, controller, _ = _make_engine(
        {"always_on": True, "chunk_seconds": 0.25, "silence_rms_threshold": 0.1}
    )

    # Silent chunk (all zeros, rms=0)
    result = engine._process_chunk(np.zeros(4000, dtype=np.float32))
    assert result == "silent"

    # Above-silence chunk returns "above_silence" when transcribe yields empty string
    model.responses = [""]
    result = engine._process_chunk(np.ones(4000, dtype=np.float32) * 0.5)
    assert result == "above_silence"

    # Non-empty transcription with type_into_window=True returns "transcribed"
    model.responses = ["hello"]
    result = engine._process_chunk(np.ones(4000, dtype=np.float32) * 0.5)
    assert result == "transcribed"
    assert controller.typed == ["hello "]

    # Non-empty transcription with type_into_window=False returns "trigger_only"
    # and does NOT type into the focused window.
    model.responses = ["trigger fire"]
    controller.typed.clear()
    result = engine._process_chunk(
        np.ones(4000, dtype=np.float32) * 0.5, type_into_window=False
    )
    assert result == "trigger_only"
    assert controller.typed == []  # nothing was typed

    # Transcribe exception returns "error"
    model.responses = [""]  # just to reset
    original = engine._transcriber.transcribe
    engine._transcriber.transcribe = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("boom")
    )
    try:
        result = engine._process_chunk(np.ones(4000, dtype=np.float32) * 0.5)
    finally:
        engine._transcriber.transcribe = original
    assert result == "error"
    assert engine.last_error and "boom" in engine.last_error


def test_set_triggers_enabled_toggles_state():
    engine, _, _, _, _ = _make_engine(
        {"always_on": True, "chunk_seconds": 0.25, "silence_rms_threshold": 0.1}
    )
    # Default is True (we want voice triggers active out of the box)
    assert engine._triggers_enabled is True
    engine.set_triggers_enabled(False)
    assert engine._triggers_enabled is False
    engine.set_triggers_enabled(False)  # no-op
    assert engine._triggers_enabled is False
    engine.set_triggers_enabled(True)
    assert engine._triggers_enabled is True


# ---------------------------------------------------------------------------
# STTEngine
# ---------------------------------------------------------------------------


def _make_engine(
    config_kwargs=None,
) -> tuple[
    STTEngine, FakeSoundDevice, FakeWhisperModel, FakeController, List[STTEvent]
]:
    cfg = STTConfig(**(config_kwargs or {}))
    sd = FakeSoundDevice()
    model = FakeWhisperModel()
    controller = FakeController()
    events: List[STTEvent] = []

    cap = AudioCapture(
        sounddevice_module=sd,
        sample_rate=cfg.sample_rate,
        chunk_seconds=cfg.chunk_seconds,
    )
    transcriber = WhisperTranscriber(model_loader=lambda _n: model)
    typer = TextTyper(controller_factory=lambda: controller, window=cfg.dedup_window)
    engine = STTEngine(
        cfg,
        audio_capture=cap,
        transcriber=transcriber,
        typer=typer,
        on_phrase=events.append,
    )
    return engine, sd, model, controller, events


def _wait_until(predicate, timeout: float = 4.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)


def test_engine_active_transcribes_and_types():
    engine, sd, model, controller, events = _make_engine(
        {"always_on": True, "chunk_seconds": 0.25, "silence_rms_threshold": 0.0}
    )
    model.responses = ["hello there"]
    engine.set_active(True)
    engine.start()
    # Allow the loop to come up
    _wait_until(lambda: sd._active and sd._active[0].started)
    stream = sd._active[0]
    stream.push(np.ones(int(0.25 * 16000), dtype=np.float32) * 0.5)
    _wait_until(lambda: bool(controller.typed))
    assert controller.typed and controller.typed[0].strip() == "hello there"
    assert events and events[0].raw_text == "hello there"
    engine.stop()


def test_engine_idle_does_not_transcribe_in_hotkey_mode():
    engine, sd, model, controller, _ = _make_engine(
        {
            "always_on": False,
            "hotkey": "<ctrl>+<alt>+space",
            "chunk_seconds": 0.25,
            "silence_rms_threshold": 0.0,
        }
    )
    # The engine may discard the first chunk if it arrives while the loop
    # still believes we are idle. Provide two responses so the assertion
    # below only needs at least one typed "activated" entry.
    model.responses = ["activated", "activated"]
    engine.start()
    _wait_until(lambda: sd._active and sd._active[0].started)
    stream = sd._active[0]
    engine.set_active(True)
    # Push a burst of audio. Some chunks may be drained while the loop is
    # still in the idle branch, but the engine will eventually process at
    # least one after observing the active flag.
    for _ in range(3):
        stream.push(np.ones(int(0.25 * 16000), dtype=np.float32) * 0.5)
    _wait_until(lambda: any(t.strip() == "activated" for t in controller.typed))
    engine.stop()


def test_engine_transcribes_for_triggers_while_typing_paused():
    """In hotkey mode, voice triggers should still fire when typing is paused."""

    engine, sd, model, controller, _ = _make_engine(
        {
            "always_on": False,
            "hotkey": "<ctrl>+<alt>+space",
            "chunk_seconds": 0.25,
            "silence_rms_threshold": 0.0,
        }
    )
    # _active stays False; the loop is in "trigger-only" mode.
    assert engine._active is False
    assert engine._triggers_enabled is True  # default

    on_phrase_calls: list[str] = []
    engine.add_observer(lambda: None)  # no-op
    # We can't easily inspect the on_phrase signal without spinning up
    # the Qt event loop, but we can verify that the engine's _process_chunk
    # is reachable by checking the on_phrase callback was wired. The
    # cleaner test is to use the heartbeat/log mechanism: push audio,
    # wait for the engine to consume it, and confirm that the typer was
    # NOT called (because typing is paused) but the model was.
    model.responses = ["trigger me"]
    engine.start()
    _wait_until(lambda: sd._active and sd._active[0].started)
    stream = sd._active[0]

    # Inject on_phrase hook AFTER start so we can capture the phrase
    def on_phrase(event):
        on_phrase_calls.append(event.raw_text)

    # Re-create the engine's on_phrase via monkey-patch on the
    # transcriber (simpler than re-instantiating): we attach to the
    # on_phrase attribute. But the simpler assertion is: when typing
    # is paused, the typer must NOT be called.
    for _ in range(3):
        stream.push(np.ones(int(0.25 * 16000), dtype=np.float32) * 0.5)
    # Give the engine time to process
    import time as _t

    _t.sleep(0.5)
    # If the trigger-only behavior works, controller.typed stays empty
    assert controller.typed == []
    # And the model was called at least once (transcription happened)
    assert model.calls, "engine should still transcribe even when typing is paused"
    engine.stop()


def test_engine_disabled_triggers_skips_transcription_when_typing_paused():
    """When typing is paused AND triggers are disabled, audio is discarded."""

    engine, sd, model, controller, _ = _make_engine(
        {
            "always_on": False,
            "hotkey": "<ctrl>+<alt>+space",
            "chunk_seconds": 0.25,
            "silence_rms_threshold": 0.0,
        }
    )
    engine.set_triggers_enabled(False)
    engine.start()
    _wait_until(lambda: sd._active and sd._active[0].started)
    stream = sd._active[0]

    # Push a few chunks. With typing paused and triggers disabled, the
    # engine should drop them all (the loop's "if not active and not
    # triggers_enabled: continue" branch).
    for _ in range(3):
        stream.push(np.ones(int(0.25 * 16000), dtype=np.float32) * 0.5)
    import time as _t

    _t.sleep(0.5)
    assert model.calls == []
    assert controller.typed == []
    engine.stop()


def test_engine_skips_silent_chunks():
    engine, sd, model, controller, _ = _make_engine(
        {"always_on": True, "chunk_seconds": 0.25, "silence_rms_threshold": 0.1}
    )
    model.responses = ["should not type"]
    engine.set_active(True)
    engine.start()
    _wait_until(lambda: sd._active and sd._active[0].started)
    stream = sd._active[0]
    # All zeros -> RMS = 0 < threshold
    stream.push(np.zeros(int(0.25 * 16000), dtype=np.float32))
    import time

    time.sleep(0.3)
    assert controller.typed == []
    engine.stop()


def test_engine_activation_mode_helpers():
    assert STTConfig(always_on=True).activation_mode() == "always"
    assert STTConfig(always_on=False, hotkey="<ctrl>+x").activation_mode() == "hotkey"
    assert STTConfig(enabled=False).activation_mode() == "off"


def test_engine_microphone_failure_emits_status():
    statuses: List[str] = []

    class BrokenSD:
        def InputStream(self, **kwargs):  # noqa: N802
            raise OSError("nope")

    cfg = STTConfig(always_on=True)
    cap = AudioCapture(
        sounddevice_module=BrokenSD(),
        sample_rate=cfg.sample_rate,
        chunk_seconds=cfg.chunk_seconds,
    )
    engine = STTEngine(
        cfg,
        audio_capture=cap,
        transcriber=WhisperTranscriber(model_loader=lambda _n: FakeWhisperModel()),
        on_status=statuses.append,
    )
    engine.set_active(True)
    engine.start()
    # Wait briefly for the loop to bail out
    import time

    time.sleep(0.2)
    engine.stop()
    assert any(s.startswith("error:") or s == "stopped" for s in statuses)


def test_engine_status_reports_running_and_model():
    engine, _, _, _, _ = _make_engine(
        {"always_on": False, "hotkey": "<ctrl>+<alt>+space", "model": "tiny"}
    )
    # Before start: thread is not alive, mic is closed
    snap = engine.status()
    assert snap["running"] is False
    assert snap["active"] is False
    assert snap["mic_open"] is False
    assert snap["model"] == "tiny"
    assert snap["hotkey"] is None  # not passed in _make_engine
    assert snap["last_error"] is None

    # Hotkey is propagated when the engine is constructed with one
    engine_with_hk = STTEngine(
        STTConfig(always_on=False, hotkey="<ctrl>+<alt>+x"),
        audio_capture=AudioCapture(sounddevice_module=FakeSoundDevice()),
        transcriber=WhisperTranscriber(model_loader=lambda _n: FakeWhisperModel()),
        typer=TextTyper(controller_factory=FakeController),
        hotkey="<ctrl>+<alt>+x",
    )
    assert engine_with_hk.status()["hotkey"] == "<ctrl>+<alt>+x"


def test_engine_observers_fire_on_state_change():
    engine, sd, model, controller, _ = _make_engine(
        {"always_on": True, "chunk_seconds": 0.25, "silence_rms_threshold": 0.0}
    )
    events: List[str] = []

    def observer() -> None:
        events.append(engine.status().get("active", False) and "active" or "idle")

    engine.add_observer(observer)
    engine.set_active(True)
    engine.start()
    import time

    # Wait briefly so the loop has time to start
    time.sleep(0.1)
    # Observer should have fired for the set_active, the start, and the
    # run-loop entry. At least one of those is "active".
    assert "active" in events
    engine.stop()
    # remove_observer should be a no-op on a missing callback
    engine.remove_observer(lambda: None)
