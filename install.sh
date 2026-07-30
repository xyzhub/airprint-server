#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "install.sh must run as root; use: sudo ./install.sh" >&2
    exit 1
fi

unset CDPATH
PROJECT_DIR=$(cd -- "$(dirname -- "$0")" && pwd -P)
VENV_DIR=/opt/airprint-server/venv
CLI_LINK=/usr/local/bin/airprint-server

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 python3-venv python3-yaml

mkdir -p /opt/airprint-server
if [ ! -x "$VENV_DIR/bin/python" ]; then
    python3 -m venv --system-site-packages "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --no-deps --upgrade "$PROJECT_DIR"
ln -sfn "$VENV_DIR/bin/airprint-server" "$CLI_LINK"

AIRPRINT_SERVER_SOURCE_DIR="$PROJECT_DIR" "$CLI_LINK" install "$@"

echo
echo "airprint-server is installed. Next: sudo airprint-server list-profiles"
