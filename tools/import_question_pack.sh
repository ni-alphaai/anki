#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# Import a hand-authored Speedrun question pack (AI-off).
#   ./tools/import_question_pack.sh                      # synthetic self-test
#   ./tools/import_question_pack.sh collection.anki2     # bundled pack -> collection
#   ./tools/import_question_pack.sh pack.json col.anki2
#
# Requires the pylib bridge to have been built once: `./ninja pylib`.
set -e

cd "$(dirname "$0")/.."

if [ ! -x out/pyenv/bin/python ]; then
    echo "built pylib not found; run ./ninja pylib first" >&2
    exit 1
fi

PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
    out/pyenv/bin/python tools/import_question_pack.py "$@"
