"""Fact-checker / concept-explainer feature.

Public surface:

* :class:`FactCheckerEngine` — orchestrator (mic → whisper → LLM)
* :class:`FactCheckerEvent` — observer payload
* :class:`AnswerPanel` — always-on-top streaming answer widget
"""

from .answer_panel import AnswerPanel
from .engine import FactCheckerEngine, FactCheckerEvent, FactCheckerStatus

__all__ = [
    "AnswerPanel",
    "FactCheckerEngine",
    "FactCheckerEvent",
    "FactCheckerStatus",
]
