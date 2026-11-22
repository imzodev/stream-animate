"""The website's voice guide lists exactly the commands the app knows."""

from __future__ import annotations

import importlib.util
import pathlib

from saidcue.voice_commands import COMMANDS

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "landing" / "tools" / "build_guide_reference.py"


def _tool():
    spec = importlib.util.spec_from_file_location("build_guide_reference", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guide_reference_is_up_to_date():
    tool = _tool()
    page = tool.PAGE.read_text(encoding="utf-8")
    assert tool.render(page) == page, (
        "landing/public/guide.html is out of date: "
        "run python landing/tools/build_guide_reference.py"
    )


def test_guide_lists_every_command_once():
    page = _tool().PAGE.read_text(encoding="utf-8")
    for command in COMMANDS:
        assert page.count(f'data-id="{command.id}"') == 1, command.id


def test_new_flags_name_real_commands():
    ids = {c.id for c in COMMANDS}
    assert _tool().NEW_IN_0_3 <= ids
