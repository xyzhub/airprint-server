#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "build-rastertoescpos.sh must run as root" >&2
    exit 1
fi

SOURCE_URL=https://github.com/chunlinyao/rastertoescpos.git
BUILD_DIR=$(mktemp -d /tmp/airprint-server-rastertoescpos.XXXXXX)
cleanup() {
    rm -rf "$BUILD_DIR"
}
trap cleanup EXIT HUP INT TERM

if find /usr/lib/cups/filter /usr/libexec/cups/filter \
    -name rastertoescpos -type f 2>/dev/null | grep -q . &&
    lpinfo -m 2>/dev/null | grep -q 'drv:///escpos.drv/gp58130.ppd' &&
    lpinfo -m 2>/dev/null | grep -q 'drv:///escpos.drv/gp80160.ppd'; then
    echo "rastertoescpos and both expected PPD models are already installed."
    exit 0
fi

git clone --depth 1 "$SOURCE_URL" "$BUILD_DIR/source"
make -C "$BUILD_DIR/source"
make -C "$BUILD_DIR/source" install

if ! find /usr/lib/cups/filter /usr/libexec/cups/filter \
    -name rastertoescpos -type f 2>/dev/null | grep -q .; then
    echo "rastertoescpos filter was not installed in a standard CUPS location" >&2
    exit 1
fi
if ! lpinfo -m | grep -q 'drv:///escpos.drv/gp80160.ppd'; then
    echo "gp80160 ESC/POS model is unavailable after installation" >&2
    exit 1
fi
if ! lpinfo -m | grep -q 'drv:///escpos.drv/gp58130.ppd'; then
    echo "gp58130 ESC/POS model is unavailable after installation" >&2
    exit 1
fi
echo "Installed rastertoescpos from $SOURCE_URL"
