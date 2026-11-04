"""Group raw microphone audio into whole utterances.

Whisper transcribes each buffer independently, with no memory of what came
before. Feeding it fixed-length slices (e.g. every 1 second) cuts words in
half and leaves fragments — the tail of a word, a breath — that the model
"completes" with invented text. Instead, this segmenter accumulates audio
while the speaker is talking and emits the whole phrase once a pause is
detected, so Whisper always sees complete words with surrounding context.

Speech/silence is decided per short analysis frame by RMS energy against the
configured threshold. faster-whisper's own VAD still runs on each utterance
to trim residual silence before inference.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, List

import numpy as np

_FRAME_MS = 30


class UtteranceSegmenter:
    """Accumulates audio blocks and returns completed utterances.

    Args:
        sample_rate: Audio sample rate in Hz.
        silence_threshold: Frame RMS below this counts as silence.
        end_silence_ms: Pause length that ends an utterance.
        max_utterance_seconds: Force a cut when talking this long without a
            pause. The cut is placed at the quietest frame of the last second
            so it falls between words where possible.
        min_speech_ms: Utterances with less voiced audio than this are
            discarded (clicks, breaths, keyboard taps).
        pre_roll_ms: Audio kept from before speech onset so the first
            syllable isn't clipped.
        tail_ms: Trailing silence kept at the end of an utterance.
    """

    def __init__(
        self,
        sample_rate: int,
        *,
        silence_threshold: float,
        end_silence_ms: int = 500,
        max_utterance_seconds: float = 12.0,
        min_speech_ms: int = 200,
        pre_roll_ms: int = 300,
        tail_ms: int = 150,
    ) -> None:
        self._frame_len = max(1, int(sample_rate * _FRAME_MS / 1000))
        self._threshold = float(silence_threshold)
        self._end_silence_frames = max(1, end_silence_ms // _FRAME_MS)
        self._max_frames = max(
            self._end_silence_frames + 1, int(max_utterance_seconds * 1000 / _FRAME_MS)
        )
        self._min_speech_frames = max(1, min_speech_ms // _FRAME_MS)
        self._tail_frames = max(0, tail_ms // _FRAME_MS)
        self._lookback_frames = max(1, 1000 // _FRAME_MS)

        self._leftover = np.zeros(0, dtype=np.float32)
        self._pre_roll: Deque[np.ndarray] = deque(
            maxlen=max(0, pre_roll_ms // _FRAME_MS)
        )
        self._frames: List[np.ndarray] = []
        self._energies: List[float] = []
        self._in_speech = False
        self._speech_frames = 0
        self._trailing_silence = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        """Drop everything buffered (e.g. when listening is paused)."""

        self._leftover = np.zeros(0, dtype=np.float32)
        self._pre_roll.clear()
        self._clear_utterance()

    def feed(self, block: np.ndarray) -> List[np.ndarray]:
        """Add an audio block; return any utterances completed by it."""

        audio = np.asarray(block, dtype=np.float32).reshape(-1)
        if self._leftover.size:
            audio = np.concatenate([self._leftover, audio])
        n_frames = audio.size // self._frame_len
        self._leftover = audio[n_frames * self._frame_len :].copy()

        out: List[np.ndarray] = []
        for i in range(n_frames):
            frame = audio[i * self._frame_len : (i + 1) * self._frame_len]
            utterance = self._push_frame(frame)
            if utterance is not None:
                out.append(utterance)
        return out

    def flush(self) -> List[np.ndarray]:
        """Emit the in-progress utterance, if it has enough speech."""

        out: List[np.ndarray] = []
        if self._in_speech:
            utterance = self._finish(len(self._frames))
            if utterance is not None:
                out.append(utterance)
        self.reset()
        return out

    # -- internals ----------------------------------------------------------

    def _push_frame(self, frame: np.ndarray):
        energy = float(np.sqrt(np.mean(np.square(frame))))
        voiced = energy >= self._threshold

        if not self._in_speech:
            if not voiced:
                self._pre_roll.append(frame)
                return None
            # Speech onset: start the utterance with the pre-roll.
            self._in_speech = True
            self._frames = list(self._pre_roll)
            self._energies = [0.0] * len(self._frames)
            self._pre_roll.clear()

        self._frames.append(frame)
        self._energies.append(energy)
        if voiced:
            self._speech_frames += 1
            self._trailing_silence = 0
        else:
            self._trailing_silence += 1

        if self._trailing_silence >= self._end_silence_frames:
            keep = len(self._frames) - self._trailing_silence + self._tail_frames
            return self._finish(keep)
        if len(self._frames) >= self._max_frames:
            return self._force_cut()
        return None

    def _finish(self, keep: int):
        """Close the utterance, keeping its first ``keep`` frames."""

        frames = self._frames[:keep]
        enough = self._speech_frames >= self._min_speech_frames
        self._clear_utterance()
        if not enough or not frames:
            return None
        return np.concatenate(frames)

    def _force_cut(self):
        """Split an over-long utterance at the quietest recent frame."""

        start = max(1, len(self._frames) - self._lookback_frames)
        window = self._energies[start:]
        cut = start + int(np.argmin(window)) + 1
        head, tail = self._frames[:cut], self._frames[cut:]
        tail_energies = self._energies[cut:]
        enough = self._speech_frames >= self._min_speech_frames
        # Carry the remainder over as the start of the next utterance.
        self._frames = list(tail)
        self._energies = list(tail_energies)
        self._speech_frames = sum(1 for e in tail_energies if e >= self._threshold)
        self._trailing_silence = 0
        if not enough:
            return None
        return np.concatenate(head)

    def _clear_utterance(self) -> None:
        self._frames = []
        self._energies = []
        self._in_speech = False
        self._speech_frames = 0
        self._trailing_silence = 0
