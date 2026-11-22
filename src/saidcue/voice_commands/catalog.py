"""The built-in English voice commands and wake-word presets.

Commands are not user-defined: the app ships this catalog and the user only
turns individual commands on or off (``disabled_commands`` in the config).
The wake word is the one thing the user can type freely, with
:data:`WAKE_WORD_PRESETS` offered as suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from .config import validate_action
from .spoken_keys import parse_count, parse_key_press

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
    # Optional slot check: the phrase only matches when this returns true
    # (see :class:`~.parser.CommandMatcher`).
    accepts: Optional[Callable[[Dict[str, str]], bool]] = None

    def __hash__(self) -> int:  # actions are dicts; the id is unique
        return hash(self.id)


def _cmd(
    id: str, group: str, description: str, phrases, *actions, accepts=None
) -> CatalogCommand:
    return CatalogCommand(
        id=id,
        group=group,
        description=description,
        phrases=tuple(phrases),
        actions=tuple(validate_action(a) for a in actions),
        accepts=accepts,
    )


def _is_key(slots: Dict[str, str]) -> bool:
    return parse_key_press(slots.get("key", "")) is not None


def _is_count(slots: Dict[str, str]) -> bool:
    """No count said ("next word"), or one that reads as a number."""

    return "count" not in slots or parse_count(slots["count"]) is not None


def _mouse(op: str, **extra) -> Dict[str, Any]:
    return {"type": "mouse", "op": op, **extra}


def _window(op: str) -> Dict[str, Any]:
    return {"type": "window", "op": op}


def _nudge(direction: str) -> CatalogCommand:
    return _cmd(f"mouse_{direction}", "Mouse",
                f"Move the mouse {direction} (add a number to go further)",
                [f"mouse {direction}", f"move mouse {direction}", f"move the mouse {direction}",
                 f"mouse {direction} {{count}}", f"move mouse {direction} {{count}}"],
                _mouse("nudge", direction=direction, repeat="{count}"),
                accepts=_is_count)  # fmt: skip


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
    _cmd("terminal_tab", "Windows & tabs",
         "Switch to a Windows Terminal tab by name or number "
         "(shows a list when several match)",
         ["terminal {name}", "go to terminal {name}", "terminal with {name}",
          "terminal {name} tab"],
         {"type": "switch_tab", "app": "terminal", "tab": "{name}"}),
    # "tab ..." searches every app that has tabs (browsers and Windows
    # Terminal). The name can come first or last: people say both
    # "tab youtube" and "the youtube tab".
    _cmd("browser_tab", "Windows & tabs",
         "Switch to a browser or terminal tab by name or number "
         "(shows a list when several match)",
         ["tab {name}", "tab with {name}", "browser tab {name}", "go to tab {name}",
          "{name} tab", "the {name} tab", "go to the {name} tab",
          "switch to the {name} tab"],
         {"type": "switch_tab", "app": "any", "tab": "{name}"}),
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
    _cmd("list_windows", "Windows & tabs", "List the open windows and pick one by number",
         ["switch window", "list windows", "show windows", "which windows", "pick a window",
          "choose window"],
         {"type": "list_windows"}),
    _cmd("minimize_window", "Windows & tabs", "Minimize the window in front",
         ["minimize", "minimize window", "minimize this", "minimize this window"],
         _window("minimize")),
    _cmd("maximize_window", "Windows & tabs", "Maximize the window in front",
         ["maximize", "maximize window", "maximize this", "maximize this window"],
         _window("maximize")),
    _cmd("restore_window", "Windows & tabs", "Restore the window in front to its normal size",
         ["restore window", "unmaximize", "restore this window"], _window("restore")),
    _cmd("close_window", "Windows & tabs", "Close the window in front (apps still ask to save)",
         ["close window", "close this window", "close the window"], _window("close")),
    _cmd("snap_left", "Windows & tabs", "Snap the window to the left half",
         ["snap left", "snap window left", "window left", "snap to the left"],
         _window("snap_left")),
    _cmd("snap_right", "Windows & tabs", "Snap the window to the right half",
         ["snap right", "snap window right", "window right", "snap to the right"],
         _window("snap_right")),
    _cmd("next_monitor", "Windows & tabs", "Move the window to the next monitor",
         ["next monitor", "move to next monitor", "move window to next monitor",
          "other monitor", "move to other monitor", "other screen", "move to other screen"],
         _window("next_monitor")),
    _cmd("switch_app", "Windows & tabs", "Go back to the previous window (Alt+Tab)",
         ["alt tab", "previous window", "last window", "switch app"],
         _keys("<alt>+<tab>")),
    _cmd("show_desktop", "Windows & tabs", "Show the desktop (Win+D)",
         ["show desktop", "go to desktop", "hide all windows"], _keys("<cmd>+d")),
    _cmd("task_view", "Windows & tabs", "Show every window (Task View)",
         ["task view", "show all windows"], _keys("<cmd>+<tab>")),
    _cmd("start_menu", "Windows & tabs", "Open the Start menu",
         ["start menu", "open start", "open start menu", "windows menu"], _keys("<cmd>")),
    _cmd("notifications", "Windows & tabs", "Open the notifications panel",
         ["notifications", "show notifications", "open notifications"], _keys("<cmd>+n")),
    _cmd("full_screen", "Windows & tabs", "Toggle full screen (F11)",
         ["full screen", "fullscreen", "toggle full screen", "exit full screen"], _keys("<f11>")),
    # -- clicking & scrolling -----------------------------------------------
    _cmd("click", "Click & scroll", "Click a button or link, or focus a text box, by its label",
         ["click {name}", "click on {name}",
          "focus {name}", "focus on {name}", "focus the {name}"],
         {"type": "click", "target": "{name}"}),
    _cmd("right_click_name", "Click & scroll", "Right-click something by its label",
         ["right click {name}", "right click on {name}"],
         {"type": "click", "target": "{name}", "button": "right"}),
    _cmd("double_click_name", "Click & scroll", "Double-click something by its label",
         ["double click {name}", "double click on {name}"],
         {"type": "click", "target": "{name}", "button": "double"}),
    _cmd("show_labels", "Click & scroll",
         "Put a number on everything clickable; say the number (“12”, “right click 12”)",
         ["show numbers", "numbers", "show labels", "label everything", "number everything",
          "show hints", "muestra numeros"],
         {"type": "labels"}),
    _cmd("mouse_grid", "Mouse",
         "Show a 3×3 grid: say numbers to zoom in, then “click”, “right click” or “move”",
         ["mouse grid", "grid", "show grid", "show the grid", "screen grid", "cuadricula"],
         {"type": "grid", "scope": "screen"}),
    _cmd("window_grid", "Mouse", "Show the mouse grid over just the window in front",
         ["window grid", "grid window", "grid this window"],
         {"type": "grid", "scope": "window"}),
    _cmd("skip_ad", "Click & scroll", "Click YouTube's “Skip” ad button",
         ["skip ad", "skip ads", "skip the ad", "skip this ad", "skip add", "skip adds"],
         {"type": "click", "any": SKIP_AD_LABELS, "exact": True, "missing": "No ad to skip"}),
    _cmd("scroll_down", "Click & scroll", "Scroll down one screen, to see the rest of the page",
         ["scroll down"], _scroll("down", "page")),
    _cmd("scroll_up", "Click & scroll", "Scroll up one screen",
         ["scroll up"], _scroll("up", "page")),
    _cmd("scroll_down_little", "Click & scroll", "Scroll down a little",
         ["scroll down a little", "scroll down a bit", "scroll down slightly"],
         _scroll("down")),
    _cmd("scroll_up_little", "Click & scroll", "Scroll up a little",
         ["scroll up a little", "scroll up a bit", "scroll up slightly"],
         _scroll("up")),
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
    # -- keyboard -------------------------------------------------------------
    # "press …" presses a key when it is one ("press escape", "press
    # control shift t") and otherwise clicks the button with that label.
    _cmd("press_key", "Keyboard",
         "Press a key or shortcut (“press escape”, “press control shift t”, "
         "“press tab 3 times”), or click a button by its label",
         ["press {key}", "hit {key}", "tap {key}", "key {key}"],
         {"type": "press", "target": "{key}"}),
    _cmd("bare_key", "Keyboard",
         "Say just the key (“escape”, “backspace 3 times”, “control z”)",
         ["{key}"], {"type": "press", "target": "{key}"}, accepts=_is_key),
    _cmd("select_all", "Text", "Select all (Ctrl+A)", ["select all", "select everything"],
         _keys("<ctrl>+a")),
    _cmd("line_end", "Text", "Move to the end of the line",
         ["end of line", "go to end of line", "go to the end of the line", "end of the line"],
         _keys("<end>")),
    _cmd("line_start", "Text", "Move to the start of the line",
         ["start of line", "beginning of line", "go to start of line",
          "go to the beginning of the line", "go to the start of the line"],
         _keys("<home>")),
    _cmd("doc_start", "Text", "Move to the start of the document",
         ["top of document", "start of document", "go to the start of the document"],
         _keys("<ctrl>+<home>")),
    _cmd("doc_end", "Text", "Move to the end of the document",
         ["end of document", "bottom of document", "go to the end of the document"],
         _keys("<ctrl>+<end>")),
    _cmd("word_right", "Text", "Move one word right (add a number for more)",
         ["next word", "word right", "{count} words right", "{count} words forward"],
         {"type": "keys", "keys": "<ctrl>+<right>", "repeat": "{count}"}, accepts=_is_count),
    _cmd("word_left", "Text", "Move one word left (add a number for more)",
         ["previous word", "word left", "{count} words left", "{count} words back"],
         {"type": "keys", "keys": "<ctrl>+<left>", "repeat": "{count}"}, accepts=_is_count),
    _cmd("select_word", "Text", "Select the word at the cursor",
         ["select word", "select this word", "select the word"],
         _keys("<ctrl>+<left>"), _keys("<ctrl>+<shift>+<right>")),
    _cmd("select_next_word", "Text", "Extend the selection one word right",
         ["select next word", "select {count} words"],
         {"type": "keys", "keys": "<ctrl>+<shift>+<right>", "repeat": "{count}"}, accepts=_is_count),
    _cmd("select_previous_word", "Text", "Extend the selection one word left",
         ["select previous word", "select last word"],
         _keys("<ctrl>+<shift>+<left>")),
    _cmd("select_line", "Text", "Select the current line",
         ["select line", "select this line", "select the line"],
         _keys("<home>"), _keys("<shift>+<end>")),
    _cmd("delete_word", "Text", "Delete the word before the cursor",
         ["delete word", "delete last word", "delete {count} words"],
         {"type": "keys", "keys": "<ctrl>+<backspace>", "repeat": "{count}"}, accepts=_is_count),
    _cmd("delete_line", "Text", "Delete the current line",
         ["delete line", "delete this line", "delete the line"],
         _keys("<home>"), _keys("<shift>+<end>"), _keys("<backspace>")),
    _cmd("new_line", "Text", "Start a new line (Enter)", ["new line", "next line"],
         _keys("<enter>")),
    # -- mouse ----------------------------------------------------------------
    _cmd("mouse_click", "Mouse", "Click where the mouse is",
         ["click", "left click", "mouse click", "click here", "click it"],
         _mouse("click", button="left")),
    _cmd("mouse_right_click", "Mouse", "Right-click where the mouse is",
         ["right click", "right click here", "mouse right click"],
         _mouse("click", button="right")),
    _cmd("mouse_double_click", "Mouse", "Double-click where the mouse is",
         ["double click", "double click here"], _mouse("click", button="double")),
    _cmd("mouse_middle_click", "Mouse", "Middle-click where the mouse is",
         ["middle click", "middle click here"], _mouse("click", button="middle")),
    _nudge("up"),
    _nudge("down"),
    _nudge("left"),
    _nudge("right"),
    _cmd("drag_mark", "Mouse", "Mark where a drag starts (then move and say “drag here”)",
         ["mark", "mark here", "start drag", "drag from here"], _mouse("drag_start")),
    _cmd("drag_here", "Mouse", "Drag from the mark to where the mouse is",
         ["drag here", "drop here", "drag to here"], _mouse("drag_end")),
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
    _cmd("cut", "Editing", "Cut (Ctrl+X)", ["cut", "cut that"], _keys("<ctrl>+x")),
    _cmd("find", "Editing", "Find on the page (Ctrl+F)",
         ["find", "find on page", "search page"], _keys("<ctrl>+f")),
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
    # -- AI assistant ---------------------------------------------------------
    _cmd("ask_listen", "AI assistant", "Start listening for a question to the AI assistant",
         ["ask a question", "i have a question", "question", "ask the assistant", "ask the ai",
          "ask assistant", "ask ai", "ask", "assistant",
          "pregunta", "tengo una pregunta", "pregunta al asistente"],
         {"type": "ask_listen"}),
    _cmd("ask", "AI assistant", "Ask the AI assistant a question in one sentence",
         ["ask {question}", "ask the assistant {question}", "ask the ai {question}",
          "ask assistant {question}", "ask ai {question}",
          "pregunta {question}", "pregunta al asistente {question}"],
         {"type": "ask", "question": "{question}"}),
    _cmd("explain", "AI assistant", "Have the AI assistant explain something",
         ["explain {question}", "explica {question}"],
         {"type": "ask", "question": "Explain {question}"}),
    _cmd("what_is", "AI assistant", "Ask the AI assistant “what is …”",
         ["what is {question}", "what s {question}"],
         {"type": "ask", "question": "What is {question}"}),
    _cmd("what_are", "AI assistant", "Ask the AI assistant “what are …”",
         ["what are {question}"],
         {"type": "ask", "question": "What are {question}"}),
    _cmd("who_is", "AI assistant", "Ask the AI assistant “who is …”",
         ["who is {question}", "who s {question}"],
         {"type": "ask", "question": "Who is {question}"}),
    _cmd("why", "AI assistant", "Ask the AI assistant “why …”",
         ["why {question}"], {"type": "ask", "question": "Why {question}"}),
    _cmd("how", "AI assistant", "Ask the AI assistant “how …”",
         ["how {question}"], {"type": "ask", "question": "How {question}"}),
    # -- apps & sites --------------------------------------------------------
    _cmd("open_youtube", "Apps & sites", "Open YouTube", ["open youtube"],
         {"type": "open", "target": "https://www.youtube.com"}),
    _cmd("open_twitch", "Apps & sites", "Open Twitch", ["open twitch"],
         {"type": "open", "target": "https://www.twitch.tv"}),
    _cmd("open_notepad", "Apps & sites", "Open Notepad", ["open notepad"],
         {"type": "open", "target": "notepad.exe"}),
    _cmd("open_calculator", "Apps & sites", "Open Calculator", ["open calculator"],
         {"type": "open", "target": "calc.exe"}),
    # Any installed app by its Start-menu name. The exact "open …"
    # entries above win when they match.
    _cmd("launch_app", "Apps & sites",
         "Open any installed app by name (shows a list when several match)",
         ["open {name}", "launch {name}", "start {name}", "run {name}", "abre {name}"],
         {"type": "launch_app", "name": "{name}"}),
    _cmd("close_app", "Apps & sites", "Close an app's window by its name or title",
         ["close {name}", "quit {name}", "exit {name}", "cierra {name}"],
         {"type": "close_app", "name": "{name}"}),
    _cmd("open_settings", "Apps & sites", "Open Windows Settings",
         ["open settings", "windows settings", "open windows settings"],
         {"type": "open", "target": "ms-settings:"}),
    _cmd("open_explorer", "Apps & sites", "Open File Explorer",
         ["open file explorer", "open explorer", "open files", "file explorer"],
         _keys("<cmd>+e")),
)  # fmt: skip

COMMAND_IDS: Tuple[str, ...] = tuple(c.id for c in COMMANDS)
