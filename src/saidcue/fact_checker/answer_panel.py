"""Streaming answer panel — a modern, branded overlay for the
LLM fact-checker.

Public API (preserved across versions):

* :meth:`append_token(token, *, kind='answer')` — thread-safe
* :meth:`clear()` — thread-safe
* :meth:`set_phase(phase)` — thread-safe
* :meth:`set_persona_label(persona)` — thread-safe

Visual structure (top to bottom):

* Animated gradient border (rotating conic gradient) drawn by
  a transparent overlay widget. Border colors come from the
  active persona.
* :class:`_StatusBar` — persona icon, name, state pill, lock
  and close buttons.
* :class:`_QuestionCard` — speech-bubble with the user's
  transcribed question, colored left stripe matching the
  persona. Hidden when no question is active.
* :class:`_AnswerView` — large streaming text with a blinking
  caret at the end while the LLM is responding. Auto-grows
  vertically up to a cap, then scrolls.
* :class:`_FooterBar` — telemetry: char count, model name,
  elapsed time.

Thread-safety: ``append_token``, ``clear``, ``set_phase``, and
``set_persona_label`` can be called from any thread. They use
``QMetaObject.invokeMethod`` with ``QueuedConnection`` to
marshal to the GUI thread.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QMetaObject, QObject, Qt, Q_ARG, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QVBoxLayout,
    QWidget,
)

from ._answer_view import _AnswerView
from ._border_painter import _AnimatedBorder
from ._footer_bar import _FooterBar
from ._persona_accent import accent_for
from ._question_card import _QuestionCard
from ._status_bar import _StatusBar

_LOGGER = logging.getLogger(__name__)


# Default size and position offsets.
_DEFAULT_WIDTH = 560
_DEFAULT_HEIGHT = 320
_MARGIN_FROM_CORNER = 32


class _PanelBridge(QObject):
    """Helper QObject that emits signals on the GUI thread.

    The engine thread calls ``bridge.append_token(token)``; the
    bridge re-emits a :class:`Signal` which is delivered to the
    widget on the GUI thread. This avoids busy-spinning the GUI
    thread from the engine.
    """

    token_appended = Signal(str, str)
    cleared = Signal()
    phase_changed = Signal(str)
    persona_changed = Signal(str)
    question_set = Signal(str)
    stream_started = Signal()
    stream_finished = Signal()
    model_known = Signal(str)


class AnswerPanel(QWidget):
    """A draggable, always-on-top streaming answer widget with a
    branded animated border."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # Frameless, on top, tool window (no taskbar entry).
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # Translucent background so the gradient border shows
        # through the rounded corners.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # No system background paint — we draw everything ourselves.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self._bridge = _PanelBridge()
        self._bridge.token_appended.connect(self._on_token)
        self._bridge.cleared.connect(self._on_clear)
        self._bridge.phase_changed.connect(self._on_phase)
        self._bridge.persona_changed.connect(self._on_persona)
        self._bridge.question_set.connect(self._on_question)
        self._bridge.stream_started.connect(self._on_stream_started)
        self._bridge.stream_finished.connect(self._on_stream_finished)
        self._bridge.model_known.connect(self._on_model_known)

        self._locked = False
        self._current_persona = "custom"
        self._drag_offset = None

        # ------------------------------------------------------------------
        # Content layout
        # ------------------------------------------------------------------
        self._container = QWidget(self)
        # The container has a solid dark background and rounded
        # corners; the border painter sits on top of it as a
        # transparent overlay.
        self._container.setObjectName("AnswerPanelContainer")
        self._container.setStyleSheet(
            "#AnswerPanelContainer {"
            "  background-color: rgba(15, 17, 21, 230);"
            "  border-radius: 18px;"
            "}"
        )

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(2, 2, 2, 2)  # room for the border
        container_layout.setSpacing(0)

        self._status_bar = _StatusBar(self._container)
        self._question_card = _QuestionCard(self._container)
        self._answer_view = _AnswerView(self._container)
        self._footer_bar = _FooterBar(self._container)

        container_layout.addWidget(self._status_bar)
        container_layout.addWidget(self._question_card)
        container_layout.addWidget(self._answer_view, 1)
        container_layout.addWidget(self._footer_bar)

        # The border painter is a transparent overlay that sits on
        # top of the container. We give it a layout that fills the
        # whole panel so the gradient traces the perimeter.
        self._border = _AnimatedBorder(self)
        self._border.setGeometry(self.rect())
        self._border.raise_()

        # Outer layout: just the container. The border sits on top
        # as a sibling.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._container)

        # Drop-shadow glow tinted with the current persona accent.
        self._shadow: QGraphicsDropShadowEffect | None = None
        self._apply_glow(accent_for(self._current_persona))

        # Status bar lock signal — when the user locks, the panel
        # stops responding to drag events. We don't disable mouse
        # events at the Qt level (the lock button itself must
        # stay clickable), but we check the flag in
        # ``mouseMoveEvent``.
        self._status_bar.lock_toggled.connect(self._on_lock_toggled)
        self._status_bar.close_clicked.connect(self.hide)

        # Default size + position.
        self.resize(_DEFAULT_WIDTH, _DEFAULT_HEIGHT)
        self._move_to_default_position()

    # ------------------------------------------------------------------
    # Public API (thread-safe)
    # ------------------------------------------------------------------

    def append_token(self, token: str, *, kind: str = "answer") -> None:
        """Append a single token to the answer text.

        Reasoning tokens are dropped — the chain-of-thought is
        logged for debugging but never shown in the panel.
        """
        if not token:
            return
        if kind == "reasoning":
            # We do still count the chars so the footer reflects
            # the full stream size, but we never show reasoning.
            self._bridge.token_appended.emit("", "answer")
            return
        QMetaObject.invokeMethod(
            self._bridge,
            "token_appended",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, token),
            Q_ARG(str, kind),
        )

    def clear(self) -> None:
        """Empty the answer text. Thread-safe."""
        QMetaObject.invokeMethod(
            self._bridge,
            "cleared",
            Qt.ConnectionType.QueuedConnection,
        )

    def set_phase(self, phase: str) -> None:
        """Update the state pill. Thread-safe."""
        QMetaObject.invokeMethod(
            self._bridge,
            "phase_changed",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, phase),
        )

    def set_persona_label(self, name: str) -> None:
        """Switch the active persona. Thread-safe."""
        QMetaObject.invokeMethod(
            self._bridge,
            "persona_changed",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, name),
        )

    def set_question(self, question: str) -> None:
        """Display the user's question in the speech-bubble card.

        Public extension point used by the engine to push the
        question text once it has been finalized. The card slides
        in / fades in via QPropertyAnimation.
        """
        QMetaObject.invokeMethod(
            self._bridge,
            "question_set",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, question),
        )

    def notify_stream_started(self) -> None:
        """Tell the panel that the LLM is about to start streaming.

        Used to start the footer timer and to show the caret.
        """
        QMetaObject.invokeMethod(
            self._bridge,
            "stream_started",
            Qt.ConnectionType.QueuedConnection,
        )

    def notify_stream_finished(self) -> None:
        """Tell the panel that the LLM has finished streaming.

        Used to stop the caret and the footer timer.
        """
        QMetaObject.invokeMethod(
            self._bridge,
            "stream_finished",
            Qt.ConnectionType.QueuedConnection,
        )

    def set_model(self, model: str) -> None:
        """Show the model name in the footer telemetry."""
        QMetaObject.invokeMethod(
            self._bridge,
            "model_known",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, model),
        )

    # ------------------------------------------------------------------
    # GUI-thread slots
    # ------------------------------------------------------------------

    def _on_token(self, token: str, kind: str = "answer") -> None:
        if not token:
            return
        if kind == "reasoning":
            return  # never rendered (but chars counted via footer if needed)
        try:
            self._answer_view.append_token(token)
        except RuntimeError:
            # Stale C++ object from a previous panel lifetime —
            # skip this token rather than crash the whole engine.
            return
        self._footer_bar.add_chars(len(token))
        # Resize the panel to fit the growing answer view. Cheap
        # (just a height comparison and a resize call) and keeps
        # the answer fully visible without scrolling.
        self._fit_height_to_content()

    def _on_clear(self) -> None:
        """Reset every sub-widget to its idle state.

        Each step is wrapped in try/except so a failure in one
        (e.g. a deleted C++ object from a previous run) does not
        prevent the others from running. The question card in
        particular must always hide, otherwise stale questions
        bleed into the next stream.
        """
        for step in (
            self._answer_view.clear,
            self._question_card.clear,
            self._footer_bar.reset,
            lambda: self._answer_view.set_streaming(False),
        ):
            try:
                step()
            except RuntimeError:
                # Stale C++ object from a previous panel lifetime.
                # We've already moved past the failing step; the
                # next step is independent.
                pass

    def _on_phase(self, phase: str) -> None:
        self._status_bar.set_phase(phase)
        # The phase drives the streaming caret too.
        if phase == "streaming":
            # The question card has served its purpose (the user
            # already saw their question). Hide it now so the
            # answer view gets the full panel height — without
            # this the card stays visible and the answer is cut
            # off by the fixed panel size.
            self._question_card.clear()
            self._answer_view.set_streaming(True)
            # Force the container layout to re-lay out so the
            # answer view moves up to where the (now hidden)
            # question card was. Without this the answer view
            # stays at its old y position and the bottom of the
            # answer gets clipped.
            self._container.layout().invalidate()
            self._container.layout().activate()
            self._fit_height_to_content()
        elif phase in ("done", "error", "idle"):
            self._answer_view.set_streaming(False)
        if phase == "done":
            self._footer_bar.stop_timer()
        elif phase == "listening":
            # New question is being recorded; reset footer in
            # case the previous stream left stale stats.
            self._footer_bar.reset()

    def _on_persona(self, name: str) -> None:
        self._current_persona = name
        accent = accent_for(name)
        self._status_bar.set_persona(name)
        self._question_card.set_persona(name)
        self._border.set_accent(accent)
        self._apply_glow(accent)

    def _on_question(self, question: str) -> None:
        self._question_card.set_question(question)
        # The question card may have changed the panel's ideal
        # height (it's now visible). Resize to fit.
        self._fit_height_to_content()

    def _on_stream_started(self) -> None:
        self._answer_view.set_streaming(True)
        self._footer_bar.start_timer()

    def _on_stream_finished(self) -> None:
        self._answer_view.set_streaming(False)
        self._footer_bar.stop_timer()
        # One final resize in case the last batch of tokens pushed
        # the answer view past the previous fit.
        self._fit_height_to_content()

    def _on_model_known(self, model: str) -> None:
        self._footer_bar.set_model(model)

    # ------------------------------------------------------------------
    # Auto-grow
    # ------------------------------------------------------------------

    def _fit_height_to_content(self) -> None:
        """Resize the panel vertically to fit the current content.

        Sums the heights of the fixed chrome (status bar, footer,
        container margins, border padding) plus the question card
        (if visible) plus the answer view's current height, and
        resizes the panel to that total — capped at the screen
        height so a runaway answer can't push the panel off-screen.

        Called after every token and after the question card is
        shown / hidden. Cheap (one height comparison + one
        resize) and keeps the answer fully visible without the
        user having to scroll inside the answer view.
        """
        # Force the container's layout to re-lay out so the
        # answer view's reported height is the *current* height
        # (after the just-hidden question card collapsed). Without
        # this the answer view still reports its old y position
        # and the panel ends up too short, clipping the answer.
        self._container.layout().invalidate()
        self._container.layout().activate()

        # Fixed chrome: status bar + footer + container margins
        # (2px top + 2px bottom per the container_layout) +
        # drop-shadow blur margin (~16px so the glow isn't clipped).
        chrome_h = (
            self._status_bar.height()
            + self._footer_bar.height()
            + 4  # container vertical margins
            + 16  # shadow blur + offset margin
        )
        # Question card: include its current height only if it's
        # actually visible. During streaming the card is hidden
        # so the answer gets the full height. We read the opacity
        # from the graphics effect because QWidget has no
        # ``opacity()`` method of its own.
        card_effect = self._question_card.graphicsEffect()
        card_opacity = card_effect.opacity() if card_effect is not None else 1.0
        if self._question_card.isVisible() and card_opacity > 0.5:
            card_h = max(
                self._question_card.height(), self._question_card.sizeHint().height()
            )
        else:
            card_h = 0
        # Answer view: whatever height it has auto-grown to.
        # Use the document's intrinsic height if it's larger
        # (the layout's reported height lags behind during streaming).
        answer_doc_h = int(self._answer_view.document().size().height()) + 24
        answer_h = max(self._answer_view.height(), answer_doc_h)
        # Cap the answer view at its own _MAX_HEIGHT to keep the
        # panel from growing past the screen.
        answer_h = min(answer_h, 480)
        ideal = chrome_h + card_h + answer_h

        # Cap at the screen height (minus a margin so the panel
        # doesn't touch the taskbar / dock).
        screen = QApplication.primaryScreen()
        if screen is not None:
            max_h = int(screen.availableGeometry().height()) - 80
        else:
            max_h = ideal
        new_h = max(_DEFAULT_HEIGHT, min(ideal, max_h))

        if abs(self.height() - new_h) > 2:
            self.resize(self.width(), new_h)

    # ------------------------------------------------------------------
    # Glow / shadow
    # ------------------------------------------------------------------

    def _apply_glow(self, accent) -> None:
        """Re-tint the drop shadow to match the persona accent."""
        if self._shadow is None:
            self._shadow = QGraphicsDropShadowEffect(self._container)
            self._shadow.setBlurRadius(36)
            self._shadow.setOffset(0, 0)
            self._container.setGraphicsEffect(self._shadow)
        glow = QColor(accent.glow)
        glow.setAlpha(180)
        self._shadow.setColor(glow)

    # ------------------------------------------------------------------
    # Drag + position
    # ------------------------------------------------------------------

    def _move_to_default_position(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        margin = _MARGIN_FROM_CORNER
        self.move(
            geo.right() - self.width() - margin,
            geo.bottom() - self.height() - margin,
        )

    def _on_lock_toggled(self, locked: bool) -> None:
        self._locked = locked

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._locked:
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._locked:
            return super().mouseMoveEvent(event)
        if self._drag_offset is not None and (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # Keep the border overlay sized to the panel.
        if self._border is not None:
            self._border.setGeometry(self.rect())
            self._border.raise_()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._border is not None:
            self._border.start()
        # Reposition on show in case the screen layout changed.
        if self.pos().x() < 0 or self.pos().y() < 0:
            self._move_to_default_position()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        if self._border is not None:
            self._border.stop()
        # Stop the timer-driven animators (caret blink, status pulse)
        # so they don't keep ticking against their graphics effects
        # while the panel is hidden or being torn down.
        self._answer_view.set_streaming(False)
        self._status_bar.stop_animations()


__all__ = ["AnswerPanel"]
