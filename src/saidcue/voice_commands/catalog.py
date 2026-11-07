"""The built-in English voice commands and wake-word presets.

Commands are not user-defined: the app ships this catalog and the user only
turns individual commands on or off (``disabled_commands`` in the config).
The wake word is the one thing the user can type freely, with
:data:`WAKE_WORD_PRESETS` offered as suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from .config import validate_action

# Short, distinct words Whisper transcribes reliably. The "hey …" forms are
# less likely to start an ordinary sentence on stream.
WAKE_WORD_PRESETS: Tuple[str, ...] = (
    "computer",
    "hey computer",
    "assistant",
    "hey assistant",
    "companion",
)
DEFAULT_WAKE_WORD = WAKE_WORD_PRESETS[0]


@dataclass(frozen=True)
class CatalogCommand:
    """A built-in command: stable id, settings label, phrases and actions."""

    id: str
    group: str
    description: str
    phrases: Tuple[str, ...]
    actions: Tuple[Dict[str, Any], ...]

    def __hash__(self) -> int:  # actions are dicts; the id is unique
        return hash(self.id)


def _cmd(id: str, group: str, description: str, phrases, *actions) -> CatalogCommand:
    return CatalogCommand(
        id=id,
        group=group,
        description=description,
        phrases=tuple(phrases),
        actions=tuple(validate_action(a) for a in actions),
    )


def _keys(combo: str) -> Dict[str, Any]:
    return {"type": "keys", "keys": combo}


def _media(key: str, repeat: int = 1) -> Dict[str, Any]:
    return {"type": "media", "key": key, "repeat": repeat}


def _scroll(direction: str, amount: str = "small") -> Dict[str, Any]:
    return {"type": "scroll", "direction": direction, "amount": amount}


# Labels of YouTube's skip-ad button. The page's language decides the label,
# not the user's speech, so common translations are included.
SKIP_AD_LABELS = [
    "Skip", "Skip ad", "Skip ads",
    "Omitir", "Omitir anuncio", "Omitir anuncios", "Saltar", "Saltar anuncio",
    "Pular", "Pular anúncio", "Passer", "Ignorer l'annonce", "Überspringen",
]  # fmt: skip


def _focus_then_dictate() -> tuple:
    return (
        {"type": "focus_window", "title": "{name}"},
        {"type": "wait", "ms": 150},
        {"type": "dictation", "state": "on"},
    )


COMMANDS: Tuple[CatalogCommand, ...] = (
    # -- windows & tabs ------------------------------------------------------
    _cmd("terminal_tab", "Windows & tabs", "Switch to a Windows Terminal tab by name or number",
         ["terminal {name}", "go to terminal {name}"],
         {"type": "switch_tab", "app": "terminal", "tab": "{name}"}),
    _cmd("browser_tab", "Windows & tabs", "Switch to a browser tab by name or number",
         ["tab {name}", "browser tab {name}", "go to tab {name}"],
         {"type": "switch_tab", "app": "browser", "tab": "{name}"}),
    _cmd("focus_window", "Windows & tabs", "Bring a window to the front by its title",
         ["go to {name}", "switch to {name}", "switch window to {name}"],
         {"type": "focus_window", "title": "{name}"}),
    _cmd("new_tab", "Windows & tabs", "Open a new tab (Ctrl+T)", ["new tab"], _keys("<ctrl>+t")),
    _cmd("close_tab", "Windows & tabs", "Close the current tab (Ctrl+W)", ["close tab"], _keys("<ctrl>+w")),
    _cmd("press_enter", "Windows & tabs", "Press Enter (submit the form or message you just typed)",
         ["press enter", "hit enter", "enter", "press return", "hit return",
          "submit", "submit it", "send it", "send message", "send that",
          "presiona enter", "oprime enter", "presiona intro", "enviar"],
         _keys("<enter>")),
    _cmd("reopen_tab", "Windows & tabs", "Reopen the last closed tab (Ctrl+Shift+T)",
         ["reopen tab", "restore tab"], _keys("<ctrl>+<shift>+t")),
    # -- clicking & scrolling -----------------------------------------------
    _cmd("click", "Click & scroll", "Click a button or link, or focus a text box, by its label",
         ["click {name}", "click on {name}", "press {name}",
          "focus {name}", "focus on {name}", "focus the {name}"],
         {"type": "click", "target": "{name}"}),
    _cmd("skip_ad", "Click & scroll", "Click YouTube's “Skip” ad button",
         ["skip ad", "skip ads", "skip the ad", "skip this ad", "skip add", "skip adds"],
         {"type": "click", "any": SKIP_AD_LABELS, "exact": True, "missing": "No ad to skip"}),
    _cmd("scroll_down", "Click & scroll", "Scroll down a little",
         ["scroll down"], _scroll("down")),
    _cmd("scroll_up", "Click & scroll", "Scroll up a little",
         ["scroll up"], _scroll("up")),
    _cmd("scroll_down_more", "Click & scroll", "Scroll down a lot",
         ["scroll down more", "scroll down a lot"], _scroll("down", "large")),
    _cmd("scroll_up_more", "Click & scroll", "Scroll up a lot",
         ["scroll up more", "scroll up a lot"], _scroll("up", "large")),
    _cmd("page_down", "Click & scroll", "Scroll down one screen",
         ["page down"], _scroll("down", "page")),
    _cmd("page_up", "Click & scroll", "Scroll up one screen",
         ["page up"], _scroll("up", "page")),
    _cmd("scroll_top", "Click & scroll", "Scroll to the top",
         ["scroll to top", "scroll to the top", "back to top"], _scroll("top")),
    _cmd("scroll_bottom", "Click & scroll", "Scroll to the bottom",
         ["scroll to bottom", "scroll to the bottom"], _scroll("bottom")),
    # -- dictation -----------------------------------------------------------
    _cmd("dictate_in", "Dictation", "Focus a window and start dictating into it",
         ["dictate in {name}", "type in {name}", "write in {name}"], *_focus_then_dictate()),
    _cmd("start_dictation", "Dictation", "Start dictating into the focused window",
         ["start dictation", "start typing", "dictate here"], {"type": "dictation", "state": "on"}),
    _cmd("stop_dictation", "Dictation", "Stop dictating",
         ["stop dictation", "stop typing"], {"type": "dictation", "state": "off"}),
    # -- editing -------------------------------------------------------------
    _cmd("save", "Editing", "Save (Ctrl+S)", ["save", "save file"], _keys("<ctrl>+s")),
    _cmd("copy", "Editing", "Copy (Ctrl+C)", ["copy", "copy that"], _keys("<ctrl>+c")),
    _cmd("paste", "Editing", "Paste (Ctrl+V)", ["paste", "paste that"], _keys("<ctrl>+v")),
    _cmd("undo", "Editing", "Undo (Ctrl+Z)", ["undo"], _keys("<ctrl>+z")),
    _cmd("redo", "Editing", "Redo (Ctrl+Y)", ["redo"], _keys("<ctrl>+y")),
    # -- media ---------------------------------------------------------------
    _cmd("play_pause", "Media", "Play / pause",
         ["pause", "play", "resume", "pause music", "play music"], _media("play_pause")),
    _cmd("next_track", "Media", "Next track",
         ["next song", "next track", "skip song", "skip"], _media("next")),
    _cmd("previous_track", "Media", "Previous track",
         ["previous song", "previous track", "last song"], _media("previous")),
    # One volume key press is 2% in Windows, so these press it several times.
    _cmd("volume_up", "Media", "Volume up 10%",
         ["volume up", "louder", "turn up the volume", "turn the volume up", "turn it up",
          "raise the volume", "increase the volume", "increase volume"],
         _media("volume_up", 5)),
    _cmd("volume_down", "Media", "Volume down 10%",
         ["volume down", "quieter", "turn down the volume", "turn the volume down",
          "turn it down", "lower the volume", "decrease the volume", "decrease volume"],
         _media("volume_down", 5)),
    _cmd("volume_up_more", "Media", "Volume up 30%",
         ["volume up more", "volume up a lot", "much louder"], _media("volume_up", 15)),
    _cmd("volume_down_more", "Media", "Volume down 30%",
         ["volume down more", "volume down a lot", "much quieter"], _media("volume_down", 15)),
    _cmd("set_volume", "Media", "Set the volume to an exact level (0-100)",
         ["set volume to {level}", "set the volume to {level}", "volume to {level}",
          "volume at {level}", "set volume {level}"],
         {"type": "volume", "level": "{level}"}),
    _cmd("mute", "Media", "Mute the sound",
         ["mute", "volume off", "sound off", "turn off the volume", "turn the volume off",
          "turn off the sound", "turn the sound off", "silence"],
         {"type": "volume", "mute": True}),
    _cmd("unmute", "Media", "Unmute the sound",
         ["unmute", "volume on", "sound on", "turn on the volume", "turn the volume on",
          "turn on the sound", "turn the sound on"],
         {"type": "volume", "mute": False}),
    # -- apps & sites --------------------------------------------------------
    _cmd("open_youtube", "Apps & sites", "Open YouTube", ["open youtube"],
         {"type": "open", "target": "https://www.youtube.com"}),
    _cmd("open_twitch", "Apps & sites", "Open Twitch", ["open twitch"],
         {"type": "open", "target": "https://www.twitch.tv"}),
    _cmd("open_notepad", "Apps & sites", "Open Notepad", ["open notepad"],
         {"type": "open", "target": "notepad.exe"}),
    _cmd("open_calculator", "Apps & sites", "Open Calculator", ["open calculator"],
         {"type": "open", "target": "calc.exe"}),
)  # fmt: skip

COMMAND_IDS: Tuple[str, ...] = tuple(c.id for c in COMMANDS)
