#!/usr/bin/env bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# Speedrun s7f "AI card check": generate MCAT flashcards from one real,
# permissively-licensed source and check them (three counts + a pre-registered
# cutoff). Uses the isolated venv (anki/out/ai-venv) and reads OPENAI_API_KEY
# from the environment or anki/.env.
#
#   tools/speedrun_card_check.sh              # offline, from the committed cache
#   tools/speedrun_card_check.sh --generate   # (re)generate cards (needs API key)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ANKI="$(cd "$HERE/.." && pwd)"
PY="$ANKI/out/ai-venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "venv missing at $PY - create it with:"
  echo "  \$(pyenv prefix 3.12.7)/bin/python -m venv $ANKI/out/ai-venv && $ANKI/out/ai-venv/bin/pip install openai numpy"
  exit 1
fi
exec "$PY" "$HERE/speedrun_card_check.py" "$@"
