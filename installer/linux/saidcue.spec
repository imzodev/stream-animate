# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Linux build of Saidcue.

Run from the repo root with:
    pyinstaller installer/linux/saidcue.spec --noconfirm

Produces a onedir build at dist/saidcue/saidcue. build.sh (next to this
file, run inside the Docker image built from Dockerfile) packages that
folder into an AppImage and a .deb.
"""

import glob
import os
from pathlib import Path

import PySide6

ROOT = Path(SPECPATH).resolve().parents[1]  # installer/linux/ -> repo root

# PyInstaller's Qt hooks do not pick up the platform plugins on Linux
# (the PySide6 wheel keeps them in PySide6/Qt/plugins, the hooks look
# in the Windows layout), so a frozen build has no way to open a window:
# "could not find or load the Qt platform plugin xcb". Collect them here.
_QT_ROOT = os.path.dirname(PySide6.__file__)
_QT_PLUGINS = os.path.join(_QT_ROOT, "Qt", "plugins")
_QT_TRANSLATIONS = os.path.join(_QT_ROOT, "Qt", "translations")

qt_binaries = [
    (
        path,
        os.path.join("PySide6", "Qt", "plugins", os.path.relpath(path, _QT_PLUGINS)),
    )
    for path in sorted(glob.glob(os.path.join(_QT_PLUGINS, "*", "*")))
    if os.path.isfile(path)
]
qt_datas = [
    (path, os.path.join("PySide6", "Qt", "translations"))
    for path in sorted(glob.glob(os.path.join(_QT_TRANSLATIONS, "*")))
    if os.path.isfile(path)
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=qt_binaries,
    datas=[
        (str(ROOT / "assets" / "icon.png"), "assets"),
        (str(ROOT / "config" / "schema.json"), "config"),
        (str(ROOT / "config" / "shortcuts.sample.json"), "config"),
    ]
    + qt_datas,
    hiddenimports=[
        # pynput picks its backend at runtime, so PyInstaller cannot see
        # it: without these the frozen app dies on "this platform is not
        # supported" before it even opens a window.
        "pynput.keyboard._xorg",
        "pynput.mouse._xorg",
        "Xlib",
        "Xlib.display",
        "Xlib.ext.xtest",
        "Xlib.keysym",
        "Xlib.protocol",
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
    name="saidcue",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="saidcue",
)
