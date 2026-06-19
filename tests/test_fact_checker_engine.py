"""Tests for the fact-checker engine and observer lifecycle.

The engine no longer runs its own microphone or Whisper pass; it
subscribes to the existing STT engine's phrase stream. These tests
use a ``FakeSTTEngine`` that records phrase observers and lets the
test push phrases via ``stt.emit_phrase("…")``.
"""

from __future__ import annotations

import time
from typing import Callable, List

import pytest

from stream_companion.fact_checker.engine import (
    FactCheckerEngine,
    FactCheckerEvent,
)
from stream_companion.llm.client import LLMError
from stream_companion.llm.config import LLMConfig
from stream_companion.stt.engine import STTEvent

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSTTEngine:
    """Stand-in for :class:`stream_companion.stt.STTEngine`.

    Exposes the phrase-observer API the fact-checker uses, and lets
    the test push phrases synchronously via ``emit_phrase``.

    Also serves as a stand-in for the lifecycle of the real engine
    (start/stop are no-ops) so we can construct
    :class:`FactCheckerEngine` without needing a real mic.
    """

    def __init__(self) -> None:
        self._phrase_observers: List[Callable[[STTEvent], None]] = []
        # Optional block callback: when set, emit_phrase waits on it
        # before returning. Used to simulate a slow STT pipeline in
        # timing-sensitive tests.
        self._phrase_block: object = None

    def add_phrase_observer(self, callback: Callable[[STTEvent], None]) -> None:
        self._phrase_observers.append(callback)

    def remove_phrase_observer(self, callback: Callable[[STTEvent], None]) -> None:
        try:
            self._phrase_observers.remove(callback)
        except ValueError:
            pass

    def emit_phrase(self, text: str, *, language: str = "es") -> None:
        if not text:
            return
        # Mirror the real STT engine: ``text`` is the *typed* output
        # (empty when typing is paused / trigger-only mode);
        # ``raw_text`` is the actual Whisper transcription. The
        # fact-checker reads ``raw_text`` because it must work even
        # when the user has typing paused.
        event = STTEvent(
            text="",
            raw_text=text,
            rms=0.05,
            language=language,
        )
        self._fire(event)

    def emit_phrase_raw(self, event: STTEvent) -> None:
        """Push a fully-formed STTEvent (used by the trigger-only
        regression test to assert that ``text=""`` + ``raw_text="…"``
        is handled correctly by the fact-checker)."""
        self._fire(event)

    def _fire(self, event: STTEvent) -> None:
        for cb in list(self._phrase_observers):
            try:
                cb(event)
            except Exception:  # pragma: no cover - defensive
                pass

    def emit_phrases_after_delay(
        self, phrases: List[str], *, delay: float = 0.1
    ) -> None:
        """Helper: push each phrase in a background thread with a
        short delay between them. Mirrors the cadence of a real
        STT engine emitting ~1 chunk per 4s.
        """
        import threading

        def push():
            for p in phrases:
                time.sleep(delay)
                self.emit_phrase(p)

        threading.Thread(target=push, daemon=True).start()


def _short_silence(monkeypatch) -> None:
    """Shorten the silence-timeout fallback so the few tests that
    build the engine WITHOUT passing ``silence_timeout`` (or a
    config with one) still run fast.

    Most tests should pass ``_fast_config()`` explicitly. This
    monkeypatch is only a belt-and-suspenders fallback for tests
    that hit the module-level default path.
    """
    monkeypatch.setattr(
        "stream_companion.fact_checker.engine._SILENT_PHRASE_TIMEOUT", 0.05
    )


def _fast_config(**overrides) -> LLMConfig:
    """Build an ``LLMConfig`` with the silence window shortened to
    0.05s so the engine's silence detector fires quickly. Use this
    in place of ``LLMConfig()`` for any test that calls
    ``engine.toggle()``."""
    return LLMConfig(silence_timeout=0.05, **overrides)


class FakeClient:
    """Replaces :class:`FactCheckerClient` in engine tests."""

    def __init__(
        self,
        tokens: List[str] | None = None,
        *,
        raise_after: Exception | None = None,
        token_delay: float = 0.0,
    ) -> None:
        self.tokens = tokens or []
        self.raise_after = raise_after
        self.streamed: List[str] = []
        self.closed = False
        # Per-token sleep so tests can observe mid-stream cancel.
        self._token_delay = token_delay

    def stream(self, user_text: str):  # noqa: ARG002
        from stream_companion.llm.providers import StreamChunk

        self.streamed.append(user_text)
        if self.raise_after is not None:
            raise self.raise_after
        for t in self.tokens:
            if self._token_delay > 0:
                time.sleep(self._token_delay)
            yield StreamChunk(content=t)

    def close(self) -> None:
        self.closed = True


def _wait_for(predicate, *, timeout: float) -> None:
    """Poll ``predicate`` every 10ms until it returns truthy or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timeout waiting for predicate")


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
    # Without an STT engine, the toggle is a no-op and
    # ``using_stt_stream`` is False.
    assert engine.using_stt_stream is False
    engine.close()


def test_engine_status_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    engine = FactCheckerEngine(
        LLMConfig(model="gpt-4o-mini", persona="eli5"),
        stt_engine=FakeSTTEngine(),
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
    _short_silence(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    stt = FakeSTTEngine()
    client = FakeClient(tokens=["It", " is", " blue", "."])
    events: List[FactCheckerEvent] = []
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    engine.add_observer(events.append)

    engine.toggle()
    # Push the question phrase; silence detection will end the
    # question after _SILENT_PHRASE_TIMEOUT seconds.
    stt.emit_phrase("Why is the sky blue?")
    _wait_for(lambda: not engine.is_running, timeout=3.0)

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
    assert client.streamed == ["Why is the sky blue?"]
    engine.close()


def test_toggle_off_mid_question_sends_what_was_collected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing the toggle a second time ends the question
    immediately and sends the phrases collected so far."""
    _short_silence(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    stt = FakeSTTEngine()
    client = FakeClient(tokens=["hello"])
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    engine.toggle()
    stt.emit_phrase("Hi")
    # Emit a second phrase INSIDE the silence window so the engine
    # does NOT end the question on its own — we want the second
    # toggle press to be the thing that ends it.
    time.sleep(0.01)
    stt.emit_phrase("there")
    time.sleep(0.01)
    engine.toggle()  # user re-presses
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    assert engine.phase in ("done", "idle", "error")
    assert client.streamed == ["Hi there"]
    engine.close()


def test_toggle_to_send_does_not_cancel_llm_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: pressing the toggle to "send the question" must
    NOT abort the LLM stream. The user's second press means
    "finalise and send", not "cancel the LLM call".

    Cancellation is handled by a SEPARATE flag (``_cancel_event``)
    bound to the dedicated cancel hotkey (ESC by default). The
    toggle hotkey never touches it — so pressing the toggle twice
    can never accidentally abort the answer the user just asked
    for.
    """
    _short_silence(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    stt = FakeSTTEngine()
    # Multi-token response so we can observe the full stream.
    client = FakeClient(tokens=["The", " answer", " is", " 42", "."])
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    events: List[FactCheckerEvent] = []
    engine.add_observer(events.append)
    engine.toggle()
    stt.emit_phrase("what is the answer")
    time.sleep(0.01)
    stt.emit_phrase("to life")
    time.sleep(0.01)
    engine.toggle()  # user re-presses to SEND
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    assert engine.phase == "done"
    # All streaming tokens were delivered — no premature cancel.
    deltas = [e.delta for e in events if e.phase == "streaming"]
    assert deltas == ["The", " answer", " is", " 42", "."]
    # The "user cancelled mid-stream" path was NOT taken.
    assert engine.phase != "error"


def test_cancel_during_stream_aborts_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling ``cancel()`` while the LLM is streaming aborts the
    stream mid-flight. This is the path bound to the ESC hotkey.
    """
    _short_silence(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    stt = FakeSTTEngine()
    # Multi-token response with a per-token delay so the test has
    # time to observe the cancel point.
    client = FakeClient(tokens=["A", " B", " C", " D", " E"], token_delay=0.05)
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    events: List[FactCheckerEvent] = []
    engine.add_observer(events.append)
    engine.toggle()
    stt.emit_phrase("hi")
    time.sleep(0.01)
    stt.emit_phrase("there")
    time.sleep(0.01)
    engine.toggle()  # user re-presses to SEND
    # As soon as streaming starts, cancel via the dedicated path.
    _wait_for(lambda: engine.phase == "streaming", timeout=2.0)
    # Let at least one token land so we can prove the cancel
    # actually cut things short (not just a no-op race).
    time.sleep(0.08)
    engine.cancel()
    _wait_for(lambda: not engine.is_running, timeout=2.0)
    # Phase is "done" (not "error") — cancel is not an error, just
    # an intentional user action.
    assert engine.phase == "done"
    # The stream was cut short — fewer tokens than the full 5.
    deltas = [e.delta for e in events if e.phase == "streaming"]
    assert 1 <= len(deltas) < 5
    engine.close()


def test_toggle_while_idle_when_no_stt_engine_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an STT engine, the fact-checker cannot capture audio.
    ``toggle`` logs a warning and returns without starting a thread."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    engine = FactCheckerEngine(_fast_config())  # no stt_engine
    engine.toggle()
    assert engine.is_running is False
    assert engine.phase == "idle"
    engine.close()


# ---------------------------------------------------------------------------
# Empty / error paths
# ---------------------------------------------------------------------------


def test_no_phrases_emitted_returns_to_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the user starts the toggle but never speaks, the engine
    stays listening until the user re-presses the toggle (or hits
    the hard cap). When the user re-presses, no question has been
    collected, no LLM call is made, and the engine returns to idle.
    """
    _short_silence(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    stt = FakeSTTEngine()
    client = FakeClient()
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    events: List[FactCheckerEvent] = []
    engine.add_observer(events.append)
    engine.toggle()
    # No phrase arrives. The engine keeps waiting (it does NOT time
    # out the first-phrase wait — that's the whole point of the
    # "wait for STT to catch up" behaviour). Simulate the user
    # giving up by re-pressing the toggle.
    time.sleep(0.05)
    engine.toggle()
    _wait_for(lambda: not engine.is_running, timeout=3.0)
    assert engine.phase == "idle"
    assert client.streamed == []
    assert all(e.phase != "thinking" for e in events)
    engine.close()


def test_llm_error_surfaces_error_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    _short_silence(monkeypatch)
    stt = FakeSTTEngine()
    client = FakeClient(raise_after=LLMError("http 500", status=500, body="oops"))
    events: List[FactCheckerEvent] = []
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    engine.add_observer(events.append)
    engine.toggle()
    stt.emit_phrase("hello")
    _wait_for(lambda: not engine.is_running, timeout=3.0)
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
    _short_silence(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    stt = FakeSTTEngine()
    client = FakeClient(
        raise_after=LLMError(
            f"http {status}", status=status, body="<html>big response</html>"
        )
    )
    events: List[FactCheckerEvent] = []
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    engine.add_observer(events.append)
    engine.toggle()
    stt.emit_phrase("hi")
    _wait_for(lambda: not engine.is_running, timeout=3.0)
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
    _short_silence(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    stt = FakeSTTEngine()
    body = (
        '{"type":"error","error":{"type":"ModelError",'
        '"message":"Model opencode-go/deepseek-v4-flash is not supported"}}'
    )
    client = FakeClient(raise_after=LLMError("http 401", status=401, body=body))
    events: List[FactCheckerEvent] = []
    engine = FactCheckerEngine(
        _fast_config(model="deepseek-v4-flash"),
        stt_engine=stt,
        client=client,
    )
    engine.add_observer(events.append)
    engine.toggle()
    stt.emit_phrase("hi")
    _wait_for(lambda: not engine.is_running, timeout=3.0)
    error_event = next(e for e in events if e.phase == "error")
    # The message must identify the model and the cause, not the
    # generic "auth failed" hint.
    assert "deepseek-v4-flash" in error_event.text
    assert "not available" in error_event.text.lower()
    assert "auth" not in error_event.text.lower()
    # Raw JSON must not leak.
    assert "ModelError" not in error_event.text
    engine.close()


# ---------------------------------------------------------------------------
# Observer lifecycle
# ---------------------------------------------------------------------------


def test_observers_fire_on_remove_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    _short_silence(monkeypatch)
    stt = FakeSTTEngine()
    client = FakeClient(tokens=["ok"])
    events_a: List[FactCheckerEvent] = []
    events_b: List[FactCheckerEvent] = []
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    engine.add_observer(events_a.append)
    engine.add_observer(events_b.append)
    engine.remove_observer(events_b.append)
    engine.toggle()
    stt.emit_phrase("hello")
    _wait_for(lambda: not engine.is_running, timeout=3.0)
    assert events_a
    assert events_b == []
    engine.close()


def test_observer_exception_does_not_break_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    _short_silence(monkeypatch)
    stt = FakeSTTEngine()
    client = FakeClient(tokens=["a"])
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)

    def bad(_event):
        raise RuntimeError("observer boom")

    engine.add_observer(bad)
    engine.add_observer(lambda _e: None)
    engine.toggle()
    stt.emit_phrase("hi")
    _wait_for(lambda: not engine.is_running, timeout=3.0)
    assert engine.phase == "done"
    engine.close()


def test_phrase_observer_unsubscribed_after_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fact-checker must unsubscribe from the STT phrase stream
    when the question ends, so subsequent STT phrases do not
    leak into a fresh question."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    _short_silence(monkeypatch)
    stt = FakeSTTEngine()
    client = FakeClient()
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    assert stt._phrase_observers == []
    engine.toggle()
    assert len(stt._phrase_observers) == 1
    stt.emit_phrase("first question")
    _wait_for(lambda: not engine.is_running, timeout=3.0)
    # After the question completes, the observer is removed so the
    # next question starts with a clean buffer.
    assert stt._phrase_observers == []
    engine.close()


# ---------------------------------------------------------------------------
# STT phrase accumulation
# ---------------------------------------------------------------------------


def test_multiple_phrases_are_concatenated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple STT phrases within the same question must be joined
    with spaces and sent to the LLM as a single prompt."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    _short_silence(monkeypatch)
    stt = FakeSTTEngine()
    client = FakeClient(tokens=["answer"])
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    engine.toggle()
    stt.emit_phrase("¿Qué")
    time.sleep(0.02)
    stt.emit_phrase("es un")
    time.sleep(0.02)
    stt.emit_phrase("LLM?")
    # No more phrases — wait for silence to expire and the engine
    # to send the buffered question to the LLM.
    _wait_for(lambda: not engine.is_running, timeout=3.0)
    assert client.streamed == ["¿Qué es un LLM?"]
    engine.close()


def test_empty_phrases_are_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only or empty phrases are ignored so the question
    is not polluted with stray spaces."""
    _short_silence(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    stt = FakeSTTEngine()
    client = FakeClient()
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    engine.toggle()
    stt.emit_phrase("hi")
    stt.emit_phrase("")  # empty
    stt.emit_phrase("   ")  # whitespace only
    stt.emit_phrase("there")
    _wait_for(lambda: not engine.is_running, timeout=3.0)
    assert client.streamed == ["hi there"]
    engine.close()


def test_phrases_buffered_even_when_stt_text_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: when the STT engine is in trigger-only mode (typing
    paused) the emitted ``STTEvent`` has ``text=""`` and the actual
    transcription lives in ``raw_text``. The fact-checker must read
    ``raw_text`` — otherwise it would silently drop every phrase and
    the LLM would never be called.
    """
    _short_silence(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    stt = FakeSTTEngine()
    client = FakeClient(tokens=["answer"])
    engine = FactCheckerEngine(_fast_config(), stt_engine=stt, client=client)
    engine.toggle()
    # Push a phrase the trigger-only way: emit a raw STTEvent with
    # ``text=""`` (as the real engine does when typing is paused).
    stt.emit_phrase_raw(
        STTEvent(
            text="",
            raw_text="explica la diferencia",
            rms=0.05,
            language="es",
        )
    )
    _wait_for(lambda: not engine.is_running, timeout=3.0)
    assert client.streamed == ["explica la diferencia"]
    engine.close()


# ---------------------------------------------------------------------------
# Language hint
# ---------------------------------------------------------------------------


def test_fact_checker_uses_default_language_auto(monkeypatch):
    """Without an explicit language, the engine defaults to 'auto'."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    engine = FactCheckerEngine(_fast_config(), stt_engine=FakeSTTEngine())
    assert engine.language == "auto"


def test_fact_checker_falsy_language_falls_back_to_auto(monkeypatch):
    """An empty string is treated as 'auto' (defensive)."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    engine = FactCheckerEngine(_fast_config(), stt_engine=FakeSTTEngine(), language="")
    assert engine.language == "auto"
