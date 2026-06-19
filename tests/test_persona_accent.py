"""Unit tests for the persona accent data module (no Qt required)."""

from __future__ import annotations

import pytest

from stream_companion.fact_checker._persona_accent import (
    ACCENTS,
    CUSTOM,
    PersonaAccent,
    accent_for,
)


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------


def test_all_known_personas_have_accents() -> None:
    """Every persona in the LLMConfig.persona enum has a registered accent."""
    expected = {
        "fact_checker",
        "eli5",
        "socratic",
        "devils_advocate",
        "custom",
    }
    assert set(ACCENTS.keys()) == expected


def test_all_accents_have_required_fields() -> None:
    """Every accent has the fields needed to drive the panel widgets."""
    for persona, accent in ACCENTS.items():
        assert isinstance(accent, PersonaAccent)
        assert accent.persona == persona
        assert accent.display_name
        assert accent.glyph
        # Colors are 6-char hex strings (no alpha).
        for color in (
            accent.gradient_top,
            accent.gradient_bottom,
            accent.accent,
            accent.glow,
        ):
            assert len(color) == 7
            assert color.startswith("#")
            int(color[1:], 16)  # raises if not valid hex


def test_display_names_are_uppercase() -> None:
    """Display names are rendered uppercase in the status bar; verify
    the data is consistent (so a future change to lower-case is
    caught at the data layer)."""
    for accent in ACCENTS.values():
        assert accent.display_name == accent.display_name.upper()


# ---------------------------------------------------------------------------
# accent_for()
# ---------------------------------------------------------------------------


def test_accent_for_known_persona() -> None:
    assert accent_for("fact_checker").persona == "fact_checker"
    assert accent_for("eli5").glyph == "💡"


def test_accent_for_unknown_falls_back_to_custom() -> None:
    fallback = accent_for("does-not-exist")
    assert fallback.persona == "custom"
    assert fallback is CUSTOM


def test_accent_for_empty_string_falls_back_to_custom() -> None:
    assert accent_for("").persona == "custom"


# ---------------------------------------------------------------------------
# Per-persona identity (smoke test)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "persona,expected_glyph",
    [
        ("fact_checker", "🔍"),
        ("eli5", "💡"),
        ("socratic", "🏛️"),
        ("devils_advocate", "⚔️"),
        ("custom", "✨"),
    ],
)
def test_each_persona_has_a_distinct_glyph(persona: str, expected_glyph: str) -> None:
    """The glyph is the most visible persona identifier on the panel —
    making sure each persona gets a unique emoji avoids visual
    confusion at a glance."""
    assert accent_for(persona).glyph == expected_glyph


def test_gradients_are_distinct_across_personas() -> None:
    """Two different personas shouldn't share the same gradient stops,
    otherwise the streaming overlay loses its branded identity."""
    stops = [(a.gradient_top, a.gradient_bottom) for a in ACCENTS.values()]
    assert len(set(stops)) == len(stops), "two personas share a gradient"
