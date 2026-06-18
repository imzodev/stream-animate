"""Tests for the fact-checker engine and observer lifecycle."""

from __future__ import annotations

import time
from typing import List

import numpy as np
import pytest

from stream_companion.fact_checker.engine import (
    FactCheckerEngine,
    FactCheckerEvent,
)
from stream_companion.llm.client import LLMError
from stream_companion.llm.config import LLMConfig
from stream_companion.stt.audio import AudioCaptureError

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeAudioCapture:
    """Replaces :class:`AudioCapture` in engine tests."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.start_error: Exception | None = None
        self._chunks: List[np.ndarray] = []

    def feed(
        self,
        text: str,
        *,
        silent_chunks: int = 4,
    ) -> None:
        """Push audio frames that the fake transcriber will turn into
        ``text`` (non-silent) followed by ``silent_chunks`` silent
        frames (RMS ≈ 0) to end the question.
        """

        # 0.5s of 16kHz mono float32 = 8000 samples
        loud = np.full(8000, 0.1, dtype=np.float32)
        silent = np.zeros(8000, dtype=np.float32)
        self._chunks.append(loud)
        self._silent_for = text
        for _ in range(silent_chunks):
            self._chunks.append(silent)
        self._exhausted = False

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def get_chunk(self, timeout: float = 0.5):  # noqa: ARG002 - matches API
        if not self._chunks:
            # Block briefly to mimic real behavior.
            time.sleep(0.01)
            return None
        return self._chunks.pop(0)


class FakeTranscriber:
    """Returns a configurable text per call."""

    def __init__(self) -> None:
        self.calls: int = 0
        self.scripted: List[str] = []

    def transcribe(self, audio, language: str = "auto") -> str:  # noqa: ARG002
        self.calls += 1
        if self.scripted:
            return self.scripted.pop(0)
        return ""

    def is_loaded(self) -> bool:
        return True


class FakeClient:
    """Replaces :class:`FactCheckerClient` in engine tests."""

    def __init__(
        self,
        tokens: List[str] | None = None,
        *,
        raise_after: Exception | None = None,
    ) -> None:
        self.tokens = tokens or []
        self.raise_after = raise_after
        self.streamed: List[str] = []
        self.closed = False

    def stream(self, user_text: str):  # noqa: ARG002
        from stream_companion.llm.providers import StreamChunk

        self.streamed.append(user_text)
        if self.raise_after is not None:
            raise self.raise_after
        for t in self.tokens:
            yield StreamChunk(content=t)

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_engine_constructs_with_default_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    cfg = LLMConfig()
    engine = FactCheckerEngine(cfg)
    assert engine.is_listening is False
    assert engine.is_running is False
    assert engine.phase == "idle"
    engine.close()


def test_engine_status_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    engine = FactCheckerEngine(
        LLMConfig(model="gpt-4o-mini", persona="eli5"),
        audio_capture=FakeAudioCapture(),
        transcriber=FakeTranscriber(),
        client=FakeClient(),
    )
    status = engine.status()
    assert status["running"] is False
    assert status["listening"] is False
    assert status["phase"] == "idle"
    assert status["model"] == "gpt-4o-mini"
    assert status["persona"] == "eli5"
    assert status["last_question"] == ""
    engine.close()


# ---------------------------------------------------------------------------
# Toggle on → question → answer
# ---------------------------------------------------------------------------


def test_toggle_starts_listening_then_streams_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    audio = FakeAudioCapture()
    audio.feed("Why is the sky blue?")
    transcriber = FakeTranscriber()
    transcriber.scripted = ["Why is the sky blue?"]
    client = FakeClient(tokens=["It", " is", " blue", "."])
    events: List[FactCheckerEvent] = []
    engine = FactCheckerEngine(
        LLMConfig(),
        audio_capture=audio,
        transcriber=transcriber,
        client=client,
    )
    engine.add_observer(events.append)

    engine.toggle()
    _wait_for(lambda: not engine.is_running, timeout=2.0)

    phases = [e.phase for e in events]
    assert "listening" in phases
    assert "thinking" in phases
    assert "streaming" in phases
    assert phases[-1] == "done"
    # The full streamed text was reassembled correctly.
    streaming_texts = [e.text for e in events if e.phase == "streaming"]
    assert streaming_texts[-1] == "It is blue."
    # Deltas were emitted in order.
    deltas = [e.delta for e in events if e.phase == "streaming"]
    assert deltas == ["It", " is", " blue", "."]
    engine.close()


def test_toggle_off_mid_silence_ends_quickly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    audio = FakeAudioCapture()
    audio.feed("Hi", silent_chunks=10)
    transcriber = FakeTranscriber()
    transcriber.scripted = ["Hi"]
    client = FakeClient(tokens=["hello"])
    engine = FactCheckerEngine(
        LLMConfig(),
        audio_capture=audio,
        transcriber=transcriber,
        client=client,
    )
    engine.toggle()
    # Toggle again after a tiny delay to mimic a real "press twice" gesture.
    time.sleep(0.05)
    engine.toggle()
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    assert engine.phase in ("done", "idle", "error")
    engine.close()


def test_toggle_twice_while_idle_is_a_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    audio = FakeAudioCapture()
    transcriber = FakeTranscriber()
    client = FakeClient()
    engine = FactCheckerEngine(
        LLMConfig(),
        audio_capture=audio,
        transcriber=transcriber,
        client=client,
    )
    engine.toggle()
    engine.toggle()  # this should be a no-op until the thread is up
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    engine.close()


# ---------------------------------------------------------------------------
# Mic busy / LLM error / empty question
# ---------------------------------------------------------------------------


def test_mic_busy_surfaces_error_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    audio = FakeAudioCapture()
    audio.start_error = AudioCaptureError("device busy")
    events: List[FactCheckerEvent] = []
    engine = FactCheckerEngine(
        LLMConfig(),
        audio_capture=audio,
        transcriber=FakeTranscriber(),
        client=FakeClient(),
    )
    engine.add_observer(events.append)
    engine.toggle()
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    assert any(e.phase == "error" for e in events)
    assert engine.phase == "error"
    assert "device busy" in (engine.last_error or "")
    engine.close()


def test_llm_error_surfaces_error_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    audio = FakeAudioCapture()
    audio.feed("hello")
    transcriber = FakeTranscriber()
    transcriber.scripted = ["hello"]
    client = FakeClient(raise_after=LLMError("http 500", status=500, body="oops"))
    events: List[FactCheckerEvent] = []
    engine = FactCheckerEngine(
        LLMConfig(),
        audio_capture=audio,
        transcriber=transcriber,
        client=client,
    )
    engine.add_observer(events.append)
    engine.toggle()
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    assert engine.phase == "error"
    assert engine.last_error and "500" in engine.last_error
    error_event = next(e for e in events if e.phase == "error")
    # The user-facing message should be friendly, NOT the raw body
    # "oops" (which could be HTML or multi-line JSON in real life).
    assert "500" in error_event.text
    assert "oops" not in error_event.text
    assert "service error" in error_event.text.lower()
    engine.close()


@pytest.mark.parametrize(
    "status,expected_keyword",
    [
        (401, "auth failed"),
        (403, "auth failed"),
        (404, "not found"),
        (429, "rate limited"),
        (500, "service error"),
        (502, "service error"),
        (503, "service error"),
    ],
)
def test_llm_error_summarises_status_for_panel(
    monkeypatch: pytest.MonkeyPatch, status: int, expected_keyword: str
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    audio = FakeAudioCapture()
    audio.feed("hi")
    transcriber = FakeTranscriber()
    transcriber.scripted = ["hi"]
    client = FakeClient(
        raise_after=LLMError(
            f"http {status}", status=status, body="<html>big response</html>"
        )
    )
    events: List[FactCheckerEvent] = []
    engine = FactCheckerEngine(
        LLMConfig(),
        audio_capture=audio,
        transcriber=transcriber,
        client=client,
    )
    engine.add_observer(events.append)
    engine.toggle()
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    assert engine.phase == "error"
    error_event = next(e for e in events if e.phase == "error")
    # The raw HTML body must NOT be in the panel message.
    assert "<html>" not in error_event.text
    assert "big response" not in error_event.text
    # The friendly message must mention the status in a useful way.
    assert expected_keyword in error_event.text.lower()
    engine.close()


def test_llm_error_opencode_model_not_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401 with a JSON ``ModelError`` body (opencode proxy style)
    must NOT be reported as an auth failure — the key is fine, the
    model just isn't provisioned on that gateway. The panel must
    surface the model name so the user knows what to change.
    """
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    audio = FakeAudioCapture()
    audio.feed("hi")
    transcriber = FakeTranscriber()
    transcriber.scripted = ["hi"]
    body = (
        '{"type":"error","error":{"type":"ModelError",'
        '"message":"Model opencode-go/deepseek-v4-flash is not supported"}}'
    )
    client = FakeClient(raise_after=LLMError("http 401", status=401, body=body))
    events: List[FactCheckerEvent] = []
    engine = FactCheckerEngine(
        LLMConfig(model="deepseek-v4-flash"),
        audio_capture=audio,
        transcriber=transcriber,
        client=client,
    )
    engine.add_observer(events.append)
    engine.toggle()
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    error_event = next(e for e in events if e.phase == "error")
    # The message must identify the model and the cause, not the
    # generic "auth failed" hint.
    assert "deepseek-v4-flash" in error_event.text
    assert "not available" in error_event.text.lower()
    assert "auth" not in error_event.text.lower()
    # Raw JSON must not leak.
    assert "ModelError" not in error_event.text
    engine.close()


def test_empty_question_returns_to_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    audio = FakeAudioCapture()
    # No loud chunk → no transcript. Push enough silent chunks for the
    # engine to give up.
    audio._chunks = [np.zeros(8000, dtype=np.float32) for _ in range(8)]
    client = FakeClient()
    engine = FactCheckerEngine(
        LLMConfig(),
        audio_capture=audio,
        transcriber=FakeTranscriber(),
        client=client,
    )
    engine.toggle()
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    assert engine.phase == "idle"
    assert client.streamed == []
    engine.close()


# ---------------------------------------------------------------------------
# Observer lifecycle
# ---------------------------------------------------------------------------


def test_observers_fire_on_remove_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    audio = FakeAudioCapture()
    audio.feed("hello")
    transcriber = FakeTranscriber()
    transcriber.scripted = ["hello"]
    client = FakeClient(tokens=["ok"])
    events_a: List[FactCheckerEvent] = []
    events_b: List[FactCheckerEvent] = []
    engine = FactCheckerEngine(
        LLMConfig(),
        audio_capture=audio,
        transcriber=transcriber,
        client=client,
    )
    engine.add_observer(events_a.append)
    engine.add_observer(events_b.append)
    engine.remove_observer(events_b.append)
    engine.toggle()
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    assert events_a  # A fired
    assert events_b == []  # B was removed before toggle
    engine.close()


def test_observer_exception_does_not_break_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    audio = FakeAudioCapture()
    audio.feed("hi")
    transcriber = FakeTranscriber()
    transcriber.scripted = ["hi"]
    client = FakeClient(tokens=["a"])
    engine = FactCheckerEngine(
        LLMConfig(),
        audio_capture=audio,
        transcriber=transcriber,
        client=client,
    )

    def bad(_event):
        raise RuntimeError("observer boom")

    engine.add_observer(bad)
    engine.add_observer(lambda _e: None)
    engine.toggle()
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    assert engine.phase == "done"
    engine.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for(predicate, *, timeout: float) -> None:
    """Poll ``predicate`` every 10ms until it returns truthy or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timeout waiting for predicate")


# ---------------------------------------------------------------------------
# Language hint wiring
# ---------------------------------------------------------------------------


def test_fact_checker_uses_default_language_auto(monkeypatch):
    """Without an explicit language, the engine passes 'auto' to Whisper."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    engine = FactCheckerEngine(LLMConfig())
    assert engine._language == "auto"  # noqa: SLF001


def test_fact_checker_passes_explicit_language_to_transcriber(
    monkeypatch,
):
    """The language hint is forwarded to every transcriber.transcribe call."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    audio = FakeAudioCapture()
    audio.feed("hola")
    languages_used: List[str] = []

    class _RecordingTranscriber(FakeTranscriber):
        def transcribe(self, audio, language: str = "auto") -> str:  # noqa: ARG002
            languages_used.append(language)
            return super().transcribe(audio, language)

    transcriber = _RecordingTranscriber()
    transcriber.scripted = ["hola"]
    engine = FactCheckerEngine(
        LLMConfig(),
        audio_capture=audio,
        transcriber=transcriber,
        language="es",
    )
    engine.toggle()
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    assert languages_used, "transcriber was never called"
    assert all(lang == "es" for lang in languages_used)
    engine.close()


def test_fact_checker_falsy_language_falls_back_to_auto(monkeypatch):
    """An empty string is treated as 'auto' (defensive)."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    engine = FactCheckerEngine(LLMConfig(), language="")
    assert engine._language == "auto"  # noqa: SLF001
