#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# Speedrun §7c coverage map (AI-off): lists the AAMC MCAT content outline, marks
# which topics a deck covers (plain + weighted %), and shows readiness abstaining
# when coverage is below the give-up line (MIN_COVERAGE = 0.50).
#
#   ./tools/speedrun_coverage_map.sh                 # synthetic demo + self-test
#   ./tools/speedrun_coverage_map.sh path/to.anki2   # report a real collection
#
# Requires the pylib bridge to have been built once: `./ninja pylib`.
set -e

cd "$(dirname "$0")/.."

if [ ! -x out/pyenv/bin/python ]; then
    echo "built pylib not found; run ./ninja pylib first" >&2
    exit 1
fi

PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
    out/pyenv/bin/python tools/speedrun_coverage_map.py "$@"
