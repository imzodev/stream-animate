from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from stream_companion.tray_indicators import (
    COLOR_FACT_LISTENING,
    COLOR_FACT_STREAMING,
    COLOR_FACT_THINKING,
    COLOR_STT_ACTIVE,
    COLOR_TYPING_ACTIVE,
    FactCheckerIndicatorState,
    TrayIndicatorState,
    compose_fact_check_state,
    compose_state,
    compose_tray_icon,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def solid_red_pixmap() -> QPixmap:
    """A small solid-red pixmap, used as a controllable base icon."""

    pix = QPixmap(64, 64)
    pix.fill(QColor(255, 0, 0))
    return pix


@pytest.fixture
def dark_pixmap() -> QPixmap:
    """A solid dark-gray pixmap. Useful when we want to verify the
    ABSENCE of a color — a red dot stands out clearly, but a red
    background would mask the test."""

    pix = QPixmap(64, 64)
    pix.fill(QColor(50, 50, 50))
    return pix


# ---------------------------------------------------------------------------
# TrayIndicatorState
# ---------------------------------------------------------------------------


def test_indicator_state_tooltip_disabled():
    state = TrayIndicatorState(enabled=False)
    assert "disabled" in state.tooltip.lower()


def test_indicator_state_tooltip_idle():
    state = TrayIndicatorState(enabled=True, stt_active=False, typing_active=False)
    assert "idle" in state.tooltip.lower()


def test_indicator_state_tooltip_listening():
    state = TrayIndicatorState(enabled=True, stt_active=True, typing_active=False)
    assert "listening" in state.tooltip.lower()


def test_indicator_state_tooltip_typing():
    state = TrayIndicatorState(enabled=True, stt_active=True, typing_active=True)
    assert "listening" in state.tooltip.lower()
    assert "typing" in state.tooltip.lower()


def test_indicator_state_any_active():
    assert TrayIndicatorState(enabled=False).any_active is False
    assert TrayIndicatorState(enabled=True).any_active is False
    assert TrayIndicatorState(enabled=True, stt_active=True).any_active is True
    assert TrayIndicatorState(enabled=True, typing_active=True).any_active is True


# ---------------------------------------------------------------------------
# compose_state
# ---------------------------------------------------------------------------


def test_compose_state_disabled():
    state = compose_state(
        stt_configured=False,
        engine_running=False,
        triggers_enabled=True,
        typing_active=True,
    )
    assert state.enabled is False


def test_compose_state_idle_when_no_triggers_or_typing():
    state = compose_state(
        stt_configured=True,
        engine_running=True,
        triggers_enabled=False,
        typing_active=False,
    )
    assert state.stt_active is False
    assert state.typing_active is False


def test_compose_state_listening_when_triggers_enabled():
    state = compose_state(
        stt_configured=True,
        engine_running=True,
        triggers_enabled=True,
        typing_active=False,
    )
    assert state.stt_active is True
    assert state.typing_active is False


def test_compose_state_listening_when_typing():
    state = compose_state(
        stt_configured=True,
        engine_running=True,
        triggers_enabled=False,
        typing_active=True,
    )
    assert state.stt_active is True
    assert state.typing_active is True


def test_compose_state_both():
    state = compose_state(
        stt_configured=True,
        engine_running=True,
        triggers_enabled=True,
        typing_active=True,
    )
    assert state.stt_active is True
    assert state.typing_active is True


def test_compose_state_engine_not_running_means_idle():
    # Even if the user wants triggers/typing, no listening dot until
    # the engine's loop is actually running.
    state = compose_state(
        stt_configured=True,
        engine_running=False,
        triggers_enabled=True,
        typing_active=True,
    )
    assert state.stt_active is False
    # typing_active is reported as the input says, since the icon also
    # shows the blue dot when the user has expressed intent to type
    # (the engine state may simply be 'about to start').
    assert state.typing_active is True


# ---------------------------------------------------------------------------
# compose_tray_icon
# ---------------------------------------------------------------------------


def test_compose_tray_icon_returns_qicon(qt_app, solid_red_pixmap):
    icon = compose_tray_icon(
        TrayIndicatorState(enabled=True, stt_active=True, typing_active=True),
        base_pixmap=solid_red_pixmap,
    )
    assert not icon.isNull()
    # Should have a usable pixmap at the standard sizes
    for size in (16, 24, 32, 64, 128):
        pix = icon.pixmap(size, size)
        assert not pix.isNull(), f"icon has no pixmap at {size}x{size}"


def test_compose_tray_icon_paints_both_dots(qt_app, solid_red_pixmap):
    """When both flags are on, both colors must appear in the rendered icon."""

    state = TrayIndicatorState(enabled=True, stt_active=True, typing_active=True)
    icon = compose_tray_icon(state, base_pixmap=solid_red_pixmap)
    pix = icon.pixmap(64, 64)
    image = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)

    # Sample the top-right and bottom-right anchor areas. With dots of
    # diameter ~20px centered at (0.78*64, 0.12*64) and (0.78*64, 0.72*64),
    # the centers are at roughly (50, 8) and (50, 46).
    found_red = _color_near(image, 50, 8, COLOR_STT_ACTIVE, tolerance=80)
    found_blue = _color_near(image, 50, 46, COLOR_TYPING_ACTIVE, tolerance=80)
    assert found_red, "expected a red dot near the top-right corner"
    assert found_blue, "expected a blue dot near the bottom-right corner"


def test_compose_tray_icon_paints_only_listening(qt_app, solid_red_pixmap):
    state = TrayIndicatorState(enabled=True, stt_active=True, typing_active=False)
    icon = compose_tray_icon(state, base_pixmap=solid_red_pixmap)
    pix = icon.pixmap(64, 64)
    image = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)

    found_red = _color_near(image, 50, 8, COLOR_STT_ACTIVE, tolerance=80)
    found_blue = _color_near(image, 50, 46, COLOR_TYPING_ACTIVE, tolerance=80)
    assert found_red, "expected a red dot near the top-right corner"
    assert not found_blue, "did not expect a blue dot in the bottom-right corner"


def test_compose_tray_icon_no_dots_when_disabled(qt_app, dark_pixmap):
    state = TrayIndicatorState(enabled=False, stt_active=False, typing_active=False)
    icon = compose_tray_icon(state, base_pixmap=dark_pixmap)
    pix = icon.pixmap(64, 64)
    image = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)

    found_red = _color_near(image, 50, 8, COLOR_STT_ACTIVE, tolerance=80)
    found_blue = _color_near(image, 50, 46, COLOR_TYPING_ACTIVE, tolerance=80)
    assert not found_red
    assert not found_blue


def test_compose_tray_icon_no_dots_when_idle(qt_app, dark_pixmap):
    state = TrayIndicatorState(enabled=True, stt_active=False, typing_active=False)
    icon = compose_tray_icon(state, base_pixmap=dark_pixmap)
    pix = icon.pixmap(64, 64)
    image = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)

    found_red = _color_near(image, 50, 8, COLOR_STT_ACTIVE, tolerance=80)
    found_blue = _color_near(image, 50, 46, COLOR_TYPING_ACTIVE, tolerance=80)
    assert not found_red
    assert not found_blue


def test_compose_tray_icon_uses_fallback_when_no_base(qt_app, monkeypatch):
    """When no asset is found, the painter should not crash and should
    still produce a valid icon (synthesized from the SC fallback)."""

    # Make find_base_icon_pixmap return None
    from stream_companion import tray_indicators

    monkeypatch.setattr(tray_indicators, "find_base_icon_pixmap", lambda size=64: None)
    icon = compose_tray_icon(
        TrayIndicatorState(enabled=True, stt_active=True, typing_active=True),
        size=64,
    )
    assert not icon.isNull()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _color_near(
    image: QImage,
    x: int,
    y: int,
    target: QColor,
    *,
    tolerance: int = 50,
) -> bool:
    """Return True if any pixel in a small box around (x, y) is close to
    ``target`` in RGB. Used to verify the painter placed a dot in the
    expected corner without depending on the exact antialiased edge.
    """

    w, h = image.width(), image.height()
    box = 8
    for dx in range(-box, box + 1):
        for dy in range(-box, box + 1):
            sx = max(0, min(w - 1, x + dx))
            sy = max(0, min(h - 1, y + dy))
            color = QColor(image.pixel(sx, sy))
            if (
                abs(color.red() - target.red()) <= tolerance
                and abs(color.green() - target.green()) <= tolerance
                and abs(color.blue() - target.blue()) <= tolerance
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# Fact-checker indicator (third dot)
# ---------------------------------------------------------------------------


def test_fact_check_state_unconfigured_has_no_color() -> None:
    fc = FactCheckerIndicatorState(configured=False, phase="listening")
    assert fc.any_active is False
    assert fc.color is None


def test_fact_check_state_idle_has_no_color() -> None:
    fc = FactCheckerIndicatorState(configured=True, phase="idle")
    assert fc.any_active is False
    assert fc.color is None


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("listening", COLOR_FACT_LISTENING),
        ("thinking", COLOR_FACT_THINKING),
        ("streaming", COLOR_FACT_STREAMING),
    ],
)
def test_fact_check_state_phase_color(phase: str, expected: QColor) -> None:
    fc = FactCheckerIndicatorState(configured=True, phase=phase)
    assert fc.any_active is True
    assert fc.color == expected


def test_compose_fact_check_state() -> None:
    fc = compose_fact_check_state(configured=True, phase="listening")
    assert fc.configured is True
    assert fc.phase == "listening"


def test_compose_tray_icon_paints_fact_check_dot(qt_app) -> None:
    """A non-idle fact-checker phase must paint a top-left dot."""
    state = TrayIndicatorState(
        stt_active=False,
        typing_active=False,
        enabled=True,
        fact_check=FactCheckerIndicatorState(
            configured=True, phase="listening"
        ),
    )
    icon = compose_tray_icon(state, size=64)
    pix = icon.pixmap(64, 64)
    assert not pix.isNull()
    image = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    # Top-left region: dot centre is at (0.22, 0.12) of 64 = (14, 8).
    assert _color_near(image, 14, 8, COLOR_FACT_LISTENING)


def test_compose_tray_icon_no_fact_check_dot_when_unconfigured(qt_app) -> None:
    """An unconfigured fact-checker must not paint a third dot."""
    state = TrayIndicatorState(
        stt_active=False,
        typing_active=False,
        enabled=True,
        fact_check=FactCheckerIndicatorState(configured=False),
    )
    icon = compose_tray_icon(state, size=64)
    pix = icon.pixmap(64, 64)
    image = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    # Top-left should still be the base icon background, not green.
    color = QColor(image.pixel(14, 8))
    is_green = (
        color.green() > color.red() + 30
        and color.green() > color.blue() + 30
    )
    assert not is_green


def test_compose_tray_icon_idle_fact_check_paints_no_dot(qt_app) -> None:
    """A configured-but-idle fact-checker must not paint a third dot."""
    state = TrayIndicatorState(
        stt_active=False,
        typing_active=False,
        enabled=True,
        fact_check=FactCheckerIndicatorState(configured=True, phase="idle"),
    )
    icon = compose_tray_icon(state, size=64)
    pix = icon.pixmap(64, 64)
    image = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    color = QColor(image.pixel(14, 8))
    is_green = (
        color.green() > color.red() + 30
        and color.green() > color.blue() + 30
    )
    assert not is_green
