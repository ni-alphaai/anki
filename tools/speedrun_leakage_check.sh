#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# Speedrun projectspec s7e leakage check (AI-off).
#   ./tools/speedrun_leakage_check.sh                 # scan the 3 real packs + self-test
#   ./tools/speedrun_leakage_check.sh pack.json ...   # scan specific packs
#
# The engine verbatim layer (get_leakage_report RPC) needs the pylib bridge to
# have been built once: `./ninja pylib`. The pure-Python near-duplicate layer
# and the detector self-test run with or without it, so this wrapper falls back
# to a system Python when the bridge is absent (unlike speedrun_benchmark.sh,
# which hard-requires it). Override the fallback with SPEEDRUN_PY=/path/to/python.
set -e

cd "$(dirname "$0")/.."

if [ -x out/pyenv/bin/python ]; then
    PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
        out/pyenv/bin/python tools/speedrun_leakage_check.py "$@"
else
    echo "built pylib not found (out/pyenv); running the pure-Python near-dup layer only." >&2
    echo "build the bridge with './ninja pylib' to also run the engine verbatim layer." >&2
    PY=""
    for cand in "${SPEEDRUN_PY:-}" python3 python /usr/bin/python3; do
        [ -n "$cand" ] || continue
        if command -v "$cand" >/dev/null 2>&1 && "$cand" -c '' >/dev/null 2>&1; then
            PY="$cand"
            break
        fi
    done
    if [ -z "$PY" ]; then
        echo "no working python found; set SPEEDRUN_PY=/path/to/python" >&2
        exit 1
    fi
    PYTHONDONTWRITEBYTECODE=1 "$PY" tools/speedrun_leakage_check.py "$@"
fi
