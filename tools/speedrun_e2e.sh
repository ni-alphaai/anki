#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# End-to-end Speedrun engine workflow test: a small suite of memory cards and
# reasoning passages driven through the SpeedrunService, asserting the engine
# classifies failures, tracks the recall-vs-performance gap, and abstains
# honestly.
#
#   ./tools/speedrun_e2e.sh
#
# Requires the pylib bridge to have been built once: `./ninja pylib`.
set -e

cd "$(dirname "$0")/.."

if [ ! -x out/pyenv/bin/python ]; then
    echo "built pylib not found; run ./ninja pylib first" >&2
    exit 1
fi

PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
    out/pyenv/bin/python tools/speedrun_e2e.py "$@"
