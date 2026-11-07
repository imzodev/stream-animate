# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows build of Saidcue.

Run from the repo root with:
    pyinstaller installer/windows/saidcue.spec --noconfirm

Produces a onedir build at dist/Saidcue/Saidcue.exe. The Inno Setup
script (saidcue.iss, next to this file) packages that folder into an
installer.
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]  # installer/windows/ -> repo root

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "assets" / "icon.ico"), "assets"),
        (str(ROOT / "assets" / "icon.png"), "assets"),
        (str(ROOT / "config" / "schema.json"), "config"),
        (str(ROOT / "config" / "shortcuts.sample.json"), "config"),
    ],
    hiddenimports=[
        "uiautomation",
        "pycaw",
        "pycaw.pycaw",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Saidcue",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # verified working with console=True first; see git history
    icon=str(ROOT / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Saidcue",
)
