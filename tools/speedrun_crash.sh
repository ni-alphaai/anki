#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# Speedrun crash-safety + offline (AI-off) proof harness (projectspec 7g).
#   ./tools/speedrun_crash.sh        # 20 hard kills + the offline/AI-off test
#   ./tools/speedrun_crash.sh 50     # run more kills
#
# Kills the engine mid-review N times (real SIGKILLs) and proves zero corrupted
# collections with every committed review preserved, then proves the
# deterministic diagnosis path still scores with the network pulled.
#
# Requires the pylib bridge to have been built once: `./ninja pylib`.
set -e

cd "$(dirname "$0")/.."

if [ ! -x out/pyenv/bin/python ]; then
    echo "built pylib not found; run ./ninja pylib first" >&2
    exit 1
fi

PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
    out/pyenv/bin/python tools/speedrun_crash.py "$@"
