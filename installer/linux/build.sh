#!/bin/sh
# Runs inside the Docker image built from Dockerfile (next to this file).
# Builds the PyInstaller onedir bundle, then packages it as both an
# AppImage and a .deb. Artifacts land in /output (bind-mount the host's
# installer/linux/output/ there to get them out of the container).
set -eu

VERSION="0.1.0"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DIST="$ROOT/dist/saidcue"
OUT="${1:-/output}"
WORK="/tmp/pkg"

mkdir -p "$OUT"
rm -rf "$WORK"
mkdir -p "$WORK"

echo "== PyInstaller build =="
cd "$ROOT"
pyinstaller installer/linux/saidcue.spec --noconfirm --distpath dist --workpath build

echo "== AppImage =="
APPDIR="$WORK/AppDir"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -r "$DIST"/. "$APPDIR/usr/bin/"
cp "$ROOT/installer/linux/saidcue.desktop" "$APPDIR/usr/share/applications/saidcue.desktop"
cp "$ROOT/installer/linux/saidcue.desktop" "$APPDIR/saidcue.desktop"
cp "$ROOT/assets/icon_256.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/saidcue.png"
cp "$ROOT/assets/icon_256.png" "$APPDIR/saidcue.png"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/saidcue" "$@"
EOF
chmod +x "$APPDIR/AppRun" "$APPDIR/usr/bin/saidcue"
ARCH=x86_64 appimagetool "$APPDIR" "$OUT/Saidcue-x86_64.AppImage"

echo "== .deb =="
PKG="$WORK/deb"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/saidcue" "$PKG/usr/bin" \
    "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/256x256/apps"
cp -r "$DIST"/. "$PKG/opt/saidcue/"
ln -s /opt/saidcue/saidcue "$PKG/usr/bin/saidcue"
cp "$ROOT/installer/linux/saidcue.desktop" "$PKG/usr/share/applications/saidcue.desktop"
cp "$ROOT/assets/icon_256.png" "$PKG/usr/share/icons/hicolor/256x256/apps/saidcue.png"
SIZE_KB=$(du -sk "$PKG/opt/saidcue" | cut -f1)
cat > "$PKG/DEBIAN/control" <<EOF
Package: saidcue
Version: $VERSION
Section: utils
Priority: optional
Architecture: amd64
Installed-Size: $SIZE_KB
Maintainer: Saidcue <hello@saidcue.com>
Description: Voice co-pilot: voice triggers, AI answers and dictation
 Say a phrase to fire sounds and overlays, ask out loud for an AI
 answer on screen, and dictate into any app.
EOF
dpkg-deb --build --root-owner-group "$PKG" "$OUT/saidcue_${VERSION}_amd64.deb"

echo "== done =="
ls -la "$OUT"
