#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1 && \
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    exec python3 run.py
elif command -v python >/dev/null 2>&1 && \
    python -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    exec python run.py
else
    echo "Python 3.10 or later is required. Install it and run this script again." >&2
    exit 1
fi
