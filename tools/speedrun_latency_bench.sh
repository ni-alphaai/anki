#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# §7h one-command latency benchmark for the Speedrun engine (AI-off).
#   ./tools/speedrun_latency_bench.sh            # small-deck self-test
#   ./tools/speedrun_latency_bench.sh 50000      # headline 50k-card run
#   ./tools/speedrun_latency_bench.sh 10000 300  # custom deck size + iterations
#
# Requires the pylib bridge to have been built once: `./ninja pylib`.
set -e

cd "$(dirname "$0")/.."

if [ ! -x out/pyenv/bin/python ]; then
    echo "built pylib not found; run ./ninja pylib first" >&2
    exit 1
fi

PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
    out/pyenv/bin/python tools/speedrun_latency_bench.py "$@"
