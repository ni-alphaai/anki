#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# Full-pipeline end-to-end Speedrun test: a realistic AAMC deck driven from an
# abstaining empty collection to a real, in-range projected score. It builds the
# 31-category outline, matures cards through the real v3 scheduler with the clock
# advanced, records graded + exam-style attempts, and asserts the whole readiness
# pipeline (score + range + coverage + performance + calibration + exam plan +
# points-at-stake + interleave) through the SpeedrunService protobuf boundary.
#
#   ./tools/speedrun_e2e_full.sh
#
# Requires the pylib bridge to have been built once: `./ninja pylib`.
set -e

cd "$(dirname "$0")/.."

if [ ! -x out/pyenv/bin/python ]; then
    echo "built pylib not found; run ./ninja pylib first" >&2
    exit 1
fi

PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
    out/pyenv/bin/python tools/speedrun_e2e_full.py "$@"
