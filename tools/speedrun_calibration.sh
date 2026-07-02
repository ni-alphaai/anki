#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# Memory-model calibration proof for Speedrun (AI-off).
#   ./tools/speedrun_calibration.sh        # both scenarios + abstain demo (n=200)
#   ./tools/speedrun_calibration.sh 500    # larger held-out set
#
# Seeds held-out (predicted, outcome) pairs, scores them through the Rust
# SpeedrunService (get_calibration_report), and (re)writes the reliability
# diagram (speedrun_calibration_chart.svg) and report
# (speedrun_calibration_report.md).
#
# Requires the pylib bridge to have been built once: `./ninja pylib`.
set -e

cd "$(dirname "$0")/.."

if [ ! -x out/pyenv/bin/python ]; then
    echo "built pylib not found; run ./ninja pylib first" >&2
    exit 1
fi

PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
    out/pyenv/bin/python tools/speedrun_calibration.py "$@"
