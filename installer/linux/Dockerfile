# Builds the Linux PyInstaller bundle plus an AppImage and a .deb.
# Run from the repo root:
#   docker build -f installer/linux/Dockerfile -t saidcue-linux-build .
#   docker run --rm -v "$PWD/installer/linux/output:/output" saidcue-linux-build
#
# This machine is Windows, so everything Linux-specific happens inside
# this container rather than being cross-built from the host.

FROM python:3.13-slim-bookworm

# Build tools, PortAudio headers (sounddevice), and the common runtime
# libraries PySide6/Qt needs even just to import successfully.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    portaudio19-dev \
    wget \
    file \
    binutils \
    fakeroot \
    dpkg-dev \
    libgl1 \
    libegl1 \
    libopengl0 \
    libxkbcommon0 \
    libxkbcommon-x11-0 \
    libdbus-1-3 \
    libfontconfig1 \
    libfreetype6 \
    libnss3 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxtst6 \
    libxcb-cursor0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-shape0 \
    libxcb-xinerama0 \
    libxcb-xkb1 \
    && rm -rf /var/lib/apt/lists/*

# appimagetool (static AppImage) — extracted so it runs without FUSE,
# which this container doesn't have.
RUN wget -q -O /tmp/appimagetool.AppImage \
        https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage \
    && chmod +x /tmp/appimagetool.AppImage \
    && cd /tmp && /tmp/appimagetool.AppImage --appimage-extract \
    && mv /tmp/squashfs-root /opt/appimagetool \
    && ln -s /opt/appimagetool/AppRun /usr/local/bin/appimagetool \
    && rm /tmp/appimagetool.AppImage

WORKDIR /src
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pyinstaller==6.22.3

COPY . .

RUN chmod +x installer/linux/build.sh
ENTRYPOINT ["installer/linux/build.sh"]
