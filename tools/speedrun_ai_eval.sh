#!/usr/bin/env bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# One-command held-out eval for the Speedrun AI diagnosis coach.
# Uses the isolated venv (anki/out/ai-venv) and reads OPENAI_API_KEY from the
# environment or anki/.env. Re-runs are deterministic from the committed cache.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ANKI="$(cd "$HERE/.." && pwd)"
PY="$ANKI/out/ai-venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "venv missing at $PY - create it with:"
  echo "  \$(pyenv prefix 3.12.7)/bin/python -m venv $ANKI/out/ai-venv && $ANKI/out/ai-venv/bin/pip install openai numpy"
  exit 1
fi
exec "$PY" "$HERE/speedrun_ai_eval.py" "$@"
