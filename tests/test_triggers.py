from __future__ import annotations

from stream_companion.models import Shortcut
from stream_companion.triggers import (
    TriggerMatcher,
    build_matcher_from_shortcuts,
    find_trigger_phrases,
    find_trigger_words,
)


# ---------------------------------------------------------------------------
# find_trigger_words
# ---------------------------------------------------------------------------


def test_find_trigger_words_basic():
    assert find_trigger_words("oh what a fail", ["fail"]) == ["fail"]


def test_find_trigger_words_case_insensitive():
    assert find_trigger_words("OH WHAT A FAIL", ["fail"]) == ["fail"]


def test_find_trigger_words_preserves_input_casing():
    # The returned word is normalized to lowercase (the matcher keys
    # on the normalized form). The phrase's "Fail" still matches
    # because matching itself is case-insensitive.
    result = find_trigger_words("say Fail out loud", ["FAIL"])
    assert result == ["fail"]


def test_find_trigger_words_phrase_input_lowercases():
    # Multi-word candidates are also lowercased on output.
    result = find_trigger_words("play fail now", ["PLAY FAIL", "win"])
    assert result == ["play fail"]


def test_find_trigger_words_word_boundary_excludes_substring():
    # 'fail' should NOT match 'failful' or 'failsafe'
    assert find_trigger_words("that was failful", ["fail"]) == []
    assert find_trigger_words("the failsafe engaged", ["fail"]) == []


def test_find_trigger_words_unicode():
    # Accented characters and Spanish words
    assert find_trigger_words("hola niño", ["niño"]) == ["niño"]


def test_find_trigger_words_multiple_in_order():
    result = find_trigger_words("play fail and then play win", ["win", "fail"])
    assert result == ["fail", "win"]


def test_find_trigger_words_rejects_empty_words():
    # Empty / whitespace-only words are ignored, but single letters
    # are allowed (e.g. for Spanish "y" or similar).
    assert find_trigger_words("a b c", ["a"]) == ["a"]
    assert find_trigger_words("hello", ["", "  "]) == []


def test_find_trigger_words_skips_empty_and_short():
    assert find_trigger_words("hello world", ["", "x", "world"]) == ["world"]


def test_find_trigger_words_empty_phrase():
    assert find_trigger_words("", ["anything"]) == []


def test_find_trigger_words_punctuation_boundary():
    # Punctuation should not break word boundary detection
    assert find_trigger_words("hey, fail!", ["fail"]) == ["fail"]
    assert find_trigger_words("(fail)", ["fail"]) == ["fail"]


# ---------------------------------------------------------------------------
# find_trigger_phrases — multi-word matching
# ---------------------------------------------------------------------------


def test_find_trigger_phrases_basic():
    assert find_trigger_phrases("oh play fail now", ["play fail"]) == ["play fail"]


def test_find_trigger_phrases_case_insensitive():
    assert find_trigger_phrases("oh PLAY FAIL now", ["play fail"]) == ["play fail"]
    assert find_trigger_phrases("oh play fail now", ["PLAY FAIL"]) == ["play fail"]


def test_find_trigger_phrases_returns_lower_cased():
    assert find_trigger_phrases("say Play Fail out loud", ["play fail"]) == [
        "play fail"
    ]


def test_find_trigger_phrases_requires_contiguity():
    # Filler words between trigger tokens must NOT match
    assert find_trigger_phrases("oh play the fail now", ["play fail"]) == []


def test_find_trigger_phrases_word_boundary_excludes_substring():
    # 'fail' inside a longer word shouldn't match as part of the phrase
    assert find_trigger_phrases("oh failful play now", ["play fail"]) == []
    assert find_trigger_phrases("oh play failsafe now", ["play fail"]) == []


def test_find_trigger_phrases_multiple_candidates_in_phrase():
    candidates = ["play fail", "win", "react with fire"]
    assert find_trigger_phrases("let's play fail and then we win", candidates) == [
        "play fail",
        "win",
    ]


def test_find_trigger_phrases_multiple_phrases_on_same_shortcut():
    # Same shortcut declaring "play fail" and "play win" — both should
    # be checked against the phrase.
    assert find_trigger_phrases("now we play win", ["play fail", "play win"]) == [
        "play win"
    ]


def test_find_trigger_phrases_punctuation_handling():
    assert find_trigger_phrases("oh, play fail!", ["play fail"]) == ["play fail"]
    assert find_trigger_phrases("play-fail", ["play fail"]) == ["play fail"]


def test_find_trigger_phrases_unicode():
    # Accented characters in the trigger phrase still work
    assert find_trigger_phrases("dile hola niño ahora", ["hola niño"]) == ["hola niño"]


def test_find_trigger_phrases_single_word_still_works():
    # A single-token candidate behaves like a word trigger.
    assert find_trigger_phrases("oh what a fail", ["fail"]) == ["fail"]


def test_find_trigger_phrases_dedups_repeats():
    # Same phrase appearing twice in the candidates should only match once.
    assert find_trigger_phrases("play fail play fail", ["play fail"]) == ["play fail"]


def test_find_trigger_phrases_skips_empty_candidates():
    assert find_trigger_phrases("play fail", ["", "  ", "play fail"]) == ["play fail"]


def test_find_trigger_phrases_empty_phrase():
    assert find_trigger_phrases("", ["anything"]) == []


def test_find_trigger_phrases_phrase_at_phrase_boundaries():
    # The match must respect word boundaries
    assert find_trigger_phrases("iplay failz", ["play fail"]) == []


def test_find_trigger_words_alias_returns_same_results():
    # The legacy name is preserved as an alias.
    assert find_trigger_words("play fail", ["play fail"]) == find_trigger_phrases(
        "play fail", ["play fail"]
    )


# ---------------------------------------------------------------------------
# TriggerMatcher
# ---------------------------------------------------------------------------


def _make_matcher(cooldown_ms: int = 1500) -> tuple[TriggerMatcher, list[list[str]]]:
    """Build a matcher with a controllable clock and a fire log."""

    fires: list[list[str]] = []
    clock = [0.0]

    def fake_clock() -> float:
        return clock[0]

    matcher = TriggerMatcher(cooldown_ms=cooldown_ms, clock=fake_clock)

    def make_callback(word: str) -> None:
        fires.append([word])

    matcher.register("fail", lambda w: make_callback(w))
    matcher.register("win", lambda w: make_callback(w))
    return matcher, fires, clock


def test_matcher_dispatch_fires_callbacks():
    matcher, fires, _ = _make_matcher()
    fired = matcher.dispatch("oh what a fail")
    assert fired == ["fail"]
    assert fires == [["fail"]]


def test_matcher_cooldown_blocks_immediate_refire():
    matcher, fires, clock = _make_matcher()
    matcher.dispatch("fail")
    assert matcher.fire_count == 1
    matcher.dispatch("fail")  # within cooldown
    assert matcher.fire_count == 1
    assert matcher.skip_count == 1
    # Advance past the cooldown
    clock[0] += 2.0
    matcher.dispatch("fail")
    assert matcher.fire_count == 2


def test_matcher_zero_cooldown_always_fires():
    matcher, fires, _ = _make_matcher(cooldown_ms=0)
    matcher.dispatch("fail")
    matcher.dispatch("fail")
    matcher.dispatch("fail")
    assert matcher.fire_count == 3


def test_matcher_cooldown_per_word():
    matcher, fires, clock = _make_matcher()
    matcher.dispatch("fail")
    # 'win' should still fire even though 'fail' is on cooldown
    matcher.dispatch("win")
    assert matcher.fire_count == 2
    assert matcher.skip_count == 0


def test_matcher_unregister_removes_callback():
    matcher, fires, _ = _make_matcher()
    assert matcher.unregister("fail") is True
    matcher.dispatch("fail")
    assert matcher.fire_count == 0
    assert matcher.skip_count == 0
    # Unregistering a non-existent word is a no-op
    assert matcher.unregister("never") is False


def test_matcher_clear():
    matcher, fires, _ = _make_matcher()
    matcher.clear()
    assert matcher.registered_words() == []
    matcher.dispatch("fail")
    assert matcher.fire_count == 0


def test_matcher_register_replaces_callback():
    matcher = TriggerMatcher(cooldown_ms=0)
    calls: list[str] = []
    matcher.register("fail", lambda w: calls.append("a"))
    matcher.register("fail", lambda w: calls.append("b"))
    matcher.dispatch("fail")
    assert calls == ["b"]


def test_matcher_callback_exception_is_swallowed():
    matcher = TriggerMatcher(cooldown_ms=0)

    def bad_callback(word: str) -> None:
        raise RuntimeError("boom")

    matcher.register("fail", bad_callback)
    matcher.register("win", lambda w: None)  # sentinel
    fired = matcher.dispatch("fail and win")
    # The 'win' callback still fires after the 'fail' one raises
    assert "win" in fired


def test_matcher_rejects_empty_words():
    matcher = TriggerMatcher()
    import pytest

    with pytest.raises(ValueError):
        matcher.register("", lambda w: None)
    with pytest.raises(ValueError):
        matcher.register("   ", lambda w: None)
    # Single character is fine
    matcher.register("a", lambda w: None)
    assert matcher.registered_words() == ["a"]


def test_matcher_cooldown_setter_validates():
    matcher = TriggerMatcher()
    import pytest

    matcher.cooldown_ms = 500
    assert matcher.cooldown_ms == 500
    with pytest.raises(ValueError):
        matcher.cooldown_ms = -1


def test_matcher_match_does_not_fire():
    matcher, fires, _ = _make_matcher()
    matched = matcher.match("oh fail")
    assert matched == ["fail"]
    assert fires == []  # no callbacks fired
    assert matcher.fire_count == 0


def test_matcher_on_skip_callback():
    skips: list[tuple[str, float]] = []
    clock = [0.0]
    matcher = TriggerMatcher(
        cooldown_ms=1000,
        clock=lambda: clock[0],
        on_skip=lambda word, remaining: skips.append((word, remaining)),
    )
    matcher.register("fail", lambda w: None)
    matcher.dispatch("fail")
    assert skips == []
    clock[0] = 0.2
    matcher.dispatch("fail")
    assert len(skips) == 1
    assert skips[0][0] == "fail"
    assert 0.7 < skips[0][1] <= 0.8


# ---------------------------------------------------------------------------
# build_matcher_from_shortcuts
# ---------------------------------------------------------------------------


def _sc(
    label: str,
    hotkey: str,
    trigger_word: str | None = None,
    trigger_phrases: tuple[str, ...] | None = None,
) -> Shortcut:
    return Shortcut(
        hotkey=hotkey,
        sound_path=f"/tmp/{label}.wav",
        trigger_word=trigger_word,
        trigger_phrases=trigger_phrases,
    )


def test_build_matcher_registers_all_trigger_words():
    scs = [
        _sc("a", "<ctrl>+1", "fail"),
        _sc("b", "<ctrl>+2", "win"),
        _sc("c", "<ctrl>+3"),  # no trigger word
    ]
    matcher, duplicates = build_matcher_from_shortcuts(scs, cooldown_ms=0)
    assert duplicates == []
    assert sorted(matcher.registered_words()) == ["fail", "win"]


def test_build_matcher_returns_duplicates():
    scs = [
        _sc("a", "<ctrl>+1", "fail"),
        _sc("b", "<ctrl>+2", "fail"),
    ]
    matcher, duplicates = build_matcher_from_shortcuts(scs, cooldown_ms=0)
    # First registration wins; the duplicate is reported
    assert len(duplicates) == 1
    assert duplicates[0][0] == "fail"
    # Dispatching 'fail' only fires the first shortcut
    fired = matcher.dispatch("fail")
    assert fired == ["fail"]


def test_build_matcher_normalizes_case():
    scs = [
        _sc("a", "<ctrl>+1", "FAIL"),
        _sc("b", "<ctrl>+2", "Win"),
    ]
    matcher, _ = build_matcher_from_shortcuts(scs, cooldown_ms=0)
    assert sorted(matcher.registered_words()) == ["fail", "win"]


def test_build_matcher_accepts_short_trigger_words():
    scs = [
        _sc("a", "<ctrl>+1", "y"),  # Spanish 'y' is fine
        _sc("b", "<ctrl>+2", "fail"),
    ]
    matcher, _ = build_matcher_from_shortcuts(scs, cooldown_ms=0)
    assert sorted(matcher.registered_words()) == ["fail", "y"]


def test_build_matcher_respects_cooldown():
    scs = [_sc("a", "<ctrl>+1", "fail")]
    matcher, _ = build_matcher_from_shortcuts(scs, cooldown_ms=500)
    assert matcher.cooldown_ms == 500


def test_build_matcher_registers_phrases():
    scs = [
        _sc("a", "<ctrl>+1", trigger_phrases=("play fail", "react with fire")),
        _sc("b", "<ctrl>+2", trigger_phrases=("play win",)),
        _sc("c", "<ctrl>+3"),  # no triggers
    ]
    matcher, duplicates = build_matcher_from_shortcuts(scs, cooldown_ms=0)
    assert duplicates == []
    assert sorted(matcher.registered_words()) == [
        "play fail",
        "play win",
        "react with fire",
    ]


def test_build_matcher_combines_word_and_phrases():
    # Same shortcut has a single-word trigger AND a multi-word phrase
    scs = [
        _sc(
            "a",
            "<ctrl>+1",
            trigger_word="fail",
            trigger_phrases=("play fail", "react with fire"),
        ),
    ]
    matcher, _ = build_matcher_from_shortcuts(scs, cooldown_ms=0)
    # All three triggers registered
    assert sorted(matcher.registered_words()) == [
        "fail",
        "play fail",
        "react with fire",
    ]
    # "oh fail now" matches only the single-word trigger
    fired = matcher.dispatch("oh fail now")
    assert fired == ["fail"]
    # "now we play fail" matches BOTH "play fail" (phrase) and "fail"
    # (single-word). The matcher returns both; the application
    # de-duplicates by shortcut downstream.
    fired = matcher.dispatch("now we play fail")
    assert sorted(fired) == ["fail", "play fail"]
    # A phrase that only matches the multi-word trigger
    fired = matcher.dispatch("let us react with fire")
    assert fired == ["react with fire"]


def test_build_matcher_phrase_duplicates_across_shortcuts():
    scs = [
        _sc("a", "<ctrl>+1", trigger_phrases=("play fail",)),
        _sc("b", "<ctrl>+2", trigger_phrases=("play fail",)),  # dup
    ]
    matcher, duplicates = build_matcher_from_shortcuts(scs, cooldown_ms=0)
    assert len(duplicates) == 1
    assert duplicates[0][0] == "play fail"
    # First registration wins
    fired = matcher.dispatch("oh play fail")
    assert fired == ["play fail"]


def test_build_matcher_phrase_and_word_deduped_together():
    scs = [
        _sc("a", "<ctrl>+1", trigger_word="play fail"),  # legacy single
        _sc("b", "<ctrl>+2", trigger_phrases=("play fail",)),  # phrase
    ]
    matcher, duplicates = build_matcher_from_shortcuts(scs, cooldown_ms=0)
    # The two are treated as the same trigger, so one is a duplicate
    assert len(duplicates) == 1
    assert duplicates[0][0] == "play fail"
