#!/usr/bin/env bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# Self-contained two-way sync check (projectspec 7b, desktop side): starts a
# throwaway anki-sync-server on a temp data dir + port, runs the headless
# two-collection sync test against it, and tears the server down. Reproducible
# with no external state.
#
#   tools/speedrun_sync_check.sh
#
# Prereqs: the sync server built (cargo build --release -p anki-sync-server,
# staged at out/bin/anki-sync-server) and the pylib bridge built (just pylib).
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${SYNC_PORT:-27703}"
BASE="$(mktemp -d)"
SERVER="out/bin/anki-sync-server"

[ -x "$SERVER" ] || { echo "sync server not found at $SERVER; build it: cargo build --release -p anki-sync-server" >&2; exit 1; }
[ -x out/pyenv/bin/python ] || { echo "built pylib not found; run just pylib" >&2; exit 1; }

SYNC_USER1=demo:demo SYNC_HOST=127.0.0.1 SYNC_PORT="$PORT" SYNC_BASE="$BASE" \
    "$SERVER" >/tmp/speedrun_sync_server.log 2>&1 &
SRV=$!
trap 'kill "$SRV" 2>/dev/null || true; rm -rf "$BASE"' EXIT

for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
    sleep 0.25
done

PYTHONPATH="out/pylib:pylib" PYTHONDONTWRITEBYTECODE=1 \
    SYNC_ENDPOINT="http://127.0.0.1:$PORT/" SYNC_USERNAME=demo SYNC_PASSWORD=demo \
    out/pyenv/bin/python tools/speedrun_sync_check.py
