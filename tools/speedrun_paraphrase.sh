#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# Speedrun section 7d paraphrase test (AI-off): recall vs. performance gap.
#   ./tools/speedrun_paraphrase.sh                 # bundled 30-card demo + asserts
#   ./tools/speedrun_paraphrase.sh path/to/pack.json   # a custom pack
#
# Builds a fresh temp collection of mature cards, answers their held-out reworded
# questions, and reports the recall-vs-performance gap. Regenerates
# tools/speedrun_paraphrase_report.md.
#
# Requires the pylib bridge to have been built once: `./ninja pylib`.
set -e

cd "$(dirname "$0")/.."

if [ ! -x out/pyenv/bin/python ]; then
    echo "built pylib not found; run ./ninja pylib first" >&2
    exit 1
fi

PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
    out/pyenv/bin/python tools/speedrun_paraphrase.py "$@"
