"""Exact system volume control (Windows Core Audio via pycaw).

Media keys can only nudge the volume 2% at a time and toggle mute, so they
can't do "set volume to 50" or a mute that is always *off* when you say
"unmute". This talks to the default playback device directly.
"""

from __future__ import annotations

import re
from typing import Optional

from .parser import normalize

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
_NAMED = {
    "max": 100, "maximum": 100, "full": 100, "hundred": 100,
    "half": 50, "quarter": 25, "off": 0, "nothing": 0,
}  # fmt: skip
_FILLER = {"percent", "per", "cent", "a", "the", "to", "and", "volume", "level"}


def parse_percent(text: str) -> Optional[int]:
    """``"50"``, ``"50%"``, ``"fifty"``, ``"sixty five percent"``, ``"half"`` → 0-100.

    Returns ``None`` when the text isn't a number in range.
    """

    m = re.search(r"\d+", text)
    if m:
        value = int(m.group(0))
        return value if 0 <= value <= 100 else None
    words = [w for w in normalize(text).replace("-", " ").split() if w not in _FILLER]
    if not words:
        return None
    if len(words) == 1 and words[0] in _NAMED:
        return _NAMED[words[0]]
    total = 0
    for word in words:
        if word in _TENS:
            total += _TENS[word]
        elif word in _UNITS:
            total += _UNITS[word]
        elif word == "hundred":
            total = (total or 1) * 100
        else:
            return None
    return total if 0 <= total <= 100 else None


class SystemVolume:
    """Real backend for the default playback device. Tests inject a fake."""

    def __init__(self) -> None:
        from pycaw.pycaw import AudioUtilities  # type: ignore[import-not-found]

        self._utils = AudioUtilities

    def _call(self, fn):
        # Core Audio is COM: make sure this thread is initialized (the STT
        # thread may already be, e.g. by UI Automation — that's fine).
        import comtypes  # type: ignore[import-not-found]

        initialized = False
        try:
            comtypes.CoInitialize()
            initialized = True
        except OSError:
            pass
        try:
            return fn(self._utils.GetSpeakers().EndpointVolume)
        finally:
            if initialized:
                comtypes.CoUninitialize()

    def get_level(self) -> int:
        return self._call(lambda ev: round(ev.GetMasterVolumeLevelScalar() * 100))

    def set_level(self, percent: int) -> None:
        percent = max(0, min(100, int(percent)))

        def apply(ev):
            ev.SetMasterVolumeLevelScalar(percent / 100.0, None)
            if percent > 0:
                ev.SetMute(0, None)  # "set volume to 40" should be audible

        self._call(apply)

    def set_mute(self, muted: bool) -> None:
        self._call(lambda ev: ev.SetMute(1 if muted else 0, None))
