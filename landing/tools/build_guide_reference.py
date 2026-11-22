"""Regenerate the "All commands" table in ``landing/public/guide.html``.

The table is built from the app's voice-command catalog, so the website
lists exactly what the app understands. Run it after changing
``src/saidcue/voice_commands/catalog.py``:

    python landing/tools/build_guide_reference.py

It rewrites only what sits between the ``reference:start`` and
``reference:end`` comments. ``tests/test_landing_guide.py`` fails when the
page is out of date.
"""

from __future__ import annotations

import html
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGE = ROOT / "landing" / "public" / "guide.html"
sys.path.insert(0, str(ROOT / "src"))

from saidcue.voice_commands import COMMANDS  # noqa: E402

START = re.compile(r"[ \t]*<!-- reference:start[^>]*-->\n")
END = "<!-- reference:end -->"

# Commands added after 0.2.0, flagged "New" on the page.
NEW_SINCE_0_2 = frozenset(
    """
    list_windows minimize_window maximize_window restore_window close_window
    snap_left snap_right next_monitor switch_app show_desktop task_view
    start_menu notifications full_screen right_click_name double_click_name
    show_labels mouse_grid window_grid press_key bare_key select_all line_end
    line_start doc_start doc_end word_right word_left select_word
    select_next_word select_previous_word select_line delete_word delete_line
    new_line mouse_click mouse_right_click mouse_double_click
    mouse_middle_click mouse_up mouse_down mouse_left mouse_right drag_mark
    drag_here cut find launch_app close_app open_settings open_explorer
    """.split()
)

# What each slot stands for, shown in italics.
_SLOT_WORDS = {
    "name": "name",
    "key": "key",
    "count": "number",
    "level": "number",
    "question": "question",
}
_SLOT = re.compile(r"\{(\w+)\}")


def phrase_html(phrase: str) -> str:
    out, last = [], 0
    for m in _SLOT.finditer(phrase):
        out.append(html.escape(phrase[last : m.start()]))
        out.append(f"<var>{_SLOT_WORDS.get(m.group(1), m.group(1))}</var>")
        last = m.end()
    out.append(html.escape(phrase[last:]))
    return "".join(out)


def build() -> str:
    groups: dict = {}
    for command in COMMANDS:  # first appearance sets the group order
        groups.setdefault(command.group, []).append(command)
    ind = "      "
    lines = []
    for group, commands in groups.items():
        lines.append(f'{ind}<div class="ref-group" data-group="{html.escape(group)}">')
        lines.append(
            f"{ind}  <h3>{html.escape(group)} <small>{len(commands)}</small></h3>"
        )
        lines.append(
            f'{ind}  <table class="cmds"><thead><tr><th>Say</th>'
            "<th>What happens</th></tr></thead><tbody>"
        )
        for c in commands:
            first, *rest = c.phrases
            new = ' <span class="new">New</span>' if c.id in NEW_SINCE_0_2 else ""
            alt = (
                '<span class="alt">'
                + ", ".join(phrase_html(p) for p in rest)
                + "</span>"
                if rest
                else ""
            )
            lines.append(
                f'{ind}    <tr data-id="{c.id}"><td>{phrase_html(first)}{new}{alt}</td>'
                f"<td>{html.escape(c.description)}</td></tr>"
            )
        lines.append(f"{ind}  </tbody></table>")
        lines.append(f"{ind}</div>")
    return "\n".join(lines) + "\n"


def render(page: str) -> str:
    start = START.search(page)
    end = page.find(END)
    if start is None or end < start.end():
        raise SystemExit("guide.html has no reference:start / reference:end markers")
    line_start = page.rfind("\n", 0, end) + 1
    return page[: start.end()] + build() + page[line_start:]


def main() -> None:
    page = PAGE.read_text(encoding="utf-8")
    PAGE.write_text(render(page), encoding="utf-8", newline="\n")
    print(f"Wrote {len(COMMANDS)} commands to {PAGE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
