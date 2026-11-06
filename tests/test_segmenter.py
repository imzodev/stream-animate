from __future__ import annotations

import numpy as np

from saidcue.stt.segmenter import UtteranceSegmenter

SR = 16000


def _speech(seconds: float, level: float = 0.3) -> np.ndarray:
    return np.full(int(seconds * SR), level, dtype=np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def _seg(**kw) -> UtteranceSegmenter:
    kw.setdefault("silence_threshold", 0.05)
    kw.setdefault("end_silence_ms", 500)
    kw.setdefault("max_utterance_seconds", 12.0)
    return UtteranceSegmenter(SR, **kw)


def _feed_blocks(seg, audio, block_seconds=0.1):
    out = []
    step = int(block_seconds * SR)
    for i in range(0, audio.size, step):
        out.extend(seg.feed(audio[i : i + step]))
    return out


def test_speech_is_held_until_a_pause():
    seg = _seg()
    # 1.5 s of continuous speech: nothing emitted yet (no word cut at 1 s).
    assert _feed_blocks(seg, _speech(1.5)) == []
    assert seg.in_speech
    # A pause ends the utterance and emits all of it at once.
    out = _feed_blocks(seg, _silence(0.6))
    assert len(out) == 1
    assert out[0].size / SR >= 1.5


def test_short_gaps_inside_a_phrase_do_not_split_it():
    seg = _seg()
    audio = np.concatenate(
        [_speech(0.6), _silence(0.2), _speech(0.6), _silence(0.2), _speech(0.6)]
    )
    assert _feed_blocks(seg, audio) == []
    out = _feed_blocks(seg, _silence(0.6))
    assert len(out) == 1
    assert out[0].size / SR >= 2.2


def test_two_phrases_separated_by_a_pause_are_two_utterances():
    seg = _seg()
    audio = np.concatenate([_speech(0.8), _silence(0.7), _speech(0.8), _silence(0.7)])
    assert len(_feed_blocks(seg, audio)) == 2


def test_pre_roll_and_tail_are_kept_but_long_silence_trimmed():
    seg = _seg(pre_roll_ms=300, tail_ms=150)
    audio = np.concatenate([_silence(1.0), _speech(1.0), _silence(1.0)])
    (utt,) = _feed_blocks(seg, audio)
    # ~0.3 s pre-roll + 1.0 s speech + ~0.15 s tail, not the full 3 s.
    assert 1.3 <= utt.size / SR <= 1.6


def test_brief_blips_are_discarded():
    seg = _seg(min_speech_ms=200)
    # A 60 ms click followed by silence never reaches Whisper.
    out = _feed_blocks(seg, np.concatenate([_speech(0.06), _silence(1.0)]))
    assert out == []


def test_silence_alone_produces_nothing():
    seg = _seg()
    assert _feed_blocks(seg, _silence(5.0)) == []
    assert not seg.in_speech


def test_long_speech_is_force_cut_at_the_quietest_point():
    seg = _seg(max_utterance_seconds=3.0)
    # Speech with a softer (but still voiced) dip at ~2.5 s.
    audio = np.concatenate(
        [_speech(2.5), _speech(0.06, level=0.06), _speech(2.0), _silence(0.6)]
    )
    out = _feed_blocks(seg, audio)
    assert len(out) == 2
    # First cut lands at the dip, not at an arbitrary sample boundary.
    assert 2.4 <= out[0].size / SR <= 2.7
    total = sum(u.size for u in out) / SR
    assert total >= 4.5  # no speech lost across the cut


def test_block_size_does_not_matter():
    audio = np.concatenate([_speech(1.2), _silence(0.7)])
    for block in (0.01, 0.1, 0.25, 1.0):
        seg = _seg()
        out = _feed_blocks(seg, audio, block_seconds=block)
        assert len(out) == 1, block


def test_flush_and_reset():
    seg = _seg()
    _feed_blocks(seg, _speech(1.0))
    out = seg.flush()
    assert len(out) == 1
    assert not seg.in_speech

    _feed_blocks(seg, _speech(1.0))
    seg.reset()
    assert _feed_blocks(seg, _silence(1.0)) == []
