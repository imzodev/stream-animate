# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Linux build of Saidcue.

Run from the repo root with:
    pyinstaller installer/linux/saidcue.spec --noconfirm

Produces a onedir build at dist/saidcue/saidcue. build.sh (next to this
file, run inside the Docker image built from Dockerfile) packages that
folder into an AppImage and a .deb.
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]  # installer/linux/ -> repo root

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "assets" / "icon.png"), "assets"),
        (str(ROOT / "config" / "schema.json"), "config"),
        (str(ROOT / "config" / "shortcuts.sample.json"), "config"),
    ],
    hiddenimports=[],
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
