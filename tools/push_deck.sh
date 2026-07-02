#!/bin/bash
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
#
# Push the desktop MCAT collection onto the phone for the Speedrun Android app.
#
#   tools/push_deck.sh [--media] [path/to/collection.anki2]
#
# For the Wednesday MVP there is no sync yet, so we simply copy the collection
# over USB-C into the app's external files dir, where the app opens it. Run this
# AFTER installing the app once (so its data dir exists) and with the phone
# connected (USB debugging on).
#
# Default source is the macOS Anki2 profile; override with an argument or the
# COLLECTION env var. We copy the DB (and any -wal/-shm) to a temp dir and fold
# the WAL in with a checkpoint before pushing, so the phone gets a consistent,
# single-file collection even while the desktop app is running.
#
# Pass --media to also push the collection.media folder (needed for image-heavy
# decks like MileDown to render pictures; it is larger and slower).
set -euo pipefail

PKG="net.speedrun.app"
DEST="/sdcard/Android/data/${PKG}/files"

PUSH_MEDIA=0
ARGS=()
for a in "$@"; do
    if [ "$a" = "--media" ]; then PUSH_MEDIA=1; else ARGS+=("$a"); fi
done
COL="${ARGS[0]:-${COLLECTION:-$HOME/Library/Application Support/Anki2/User 1/collection.anki2}}"

if [ ! -f "$COL" ]; then
    echo "Collection not found: $COL" >&2
    echo "Pass your MCAT collection.anki2 path as an argument, or set COLLECTION." >&2
    exit 1
fi

if ! command -v adb >/dev/null 2>&1; then
    echo "adb not found on PATH. Install platform-tools (or add the SDK to PATH)." >&2
    exit 1
fi

if [ "$(adb get-state 2>/dev/null || true)" != "device" ]; then
    echo "No device detected. Connect the phone over USB-C, enable USB debugging," >&2
    echo "and accept the 'Allow USB debugging?' prompt, then re-run." >&2
    adb devices || true
    exit 1
fi

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
TMP="$TMPDIR/collection.anki2"

cp "$COL" "$TMP"
[ -f "$COL-wal" ] && cp "$COL-wal" "$TMP-wal"
[ -f "$COL-shm" ] && cp "$COL-shm" "$TMP-shm"
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$TMP" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 || true
fi

echo "Pushing $(du -h "$TMP" | cut -f1) collection to ${DEST} ..."
if ! adb shell "mkdir -p '$DEST'" 2>/dev/null; then
    echo "Could not create $DEST. Install and launch the app once, then re-run." >&2
    exit 1
fi
adb push "$TMP" "$DEST/collection.anki2"

if [ "$PUSH_MEDIA" = "1" ]; then
    MDIR="$(dirname "$COL")/collection.media"
    if [ -d "$MDIR" ]; then
        echo "Pushing media ($(du -sh "$MDIR" | cut -f1)); this can take a minute..."
        adb shell "mkdir -p '$DEST/collection.media'"
        adb push "$MDIR/." "$DEST/collection.media/"
    else
        echo "No media folder at $MDIR - skipping." >&2
    fi
fi

echo "Done. Open Speedrun on the phone and start reviewing."
