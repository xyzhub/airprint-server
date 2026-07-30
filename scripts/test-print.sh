#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 QUEUE" >&2
    exit 2
fi

exec airprint-server test --printer "$1"

