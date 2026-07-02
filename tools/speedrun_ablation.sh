#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# Points-at-stake study-feature ablation harness (AI-off).
#   ./tools/speedrun_ablation.sh                       # self-test + 3-arm experiment
#   ./tools/speedrun_ablation.sh --experiment [seeds]  # 3-arm experiment only (JSON)
#   ./tools/speedrun_ablation.sh collection.anki2 [deck_id]
#
# The 3-arm experiment (full app vs feature-off vs plain Anki) is a simulated-
# learner study; see tools/speedrun_ablation_report.md for the pre-registered
# metric and results.
#
# Requires the pylib bridge to have been built once: `./ninja pylib`.
set -e

cd "$(dirname "$0")/.."

if [ ! -x out/pyenv/bin/python ]; then
    echo "built pylib not found; run ./ninja pylib first" >&2
    exit 1
fi

PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
    out/pyenv/bin/python tools/speedrun_ablation.py "$@"
