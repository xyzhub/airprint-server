#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "uninstall.sh must run as root; use: sudo ./uninstall.sh" >&2
    exit 1
fi

CLI=/usr/local/bin/airprint-server
VENV_DIR=/opt/airprint-server/venv
ASSUME_YES=false
for argument in "$@"; do
    if [ "$argument" = "--yes" ]; then
        ASSUME_YES=true
    fi
done

if [ -x "$CLI" ]; then
    "$CLI" uninstall "$@"
elif [ -x "$VENV_DIR/bin/airprint-server" ]; then
    "$VENV_DIR/bin/airprint-server" uninstall "$@"
else
    echo "airprint-server CLI is not installed; system queues were not changed." >&2
fi

REMOVE_CLI=false
if [ "$ASSUME_YES" = true ]; then
    REMOVE_CLI=true
elif [ -t 0 ]; then
    printf 'Remove the airprint-server CLI runtime? [y/N] '
    read -r answer
    case "$answer" in
        y|Y|yes|YES) REMOVE_CLI=true ;;
    esac
fi

if [ "$REMOVE_CLI" = true ]; then
    if [ -L "$CLI" ] && [ "$(readlink "$CLI")" = "$VENV_DIR/bin/airprint-server" ]; then
        rm "$CLI"
    fi
    if [ -d "$VENV_DIR" ]; then
        rm -rf "$VENV_DIR"
    fi
    rmdir /opt/airprint-server 2>/dev/null || true
    echo "Removed the airprint-server CLI runtime."
else
    echo "Kept the airprint-server CLI runtime."
fi
echo "CUPS and Avahi were left installed."
