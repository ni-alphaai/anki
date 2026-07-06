# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Sync Speedrun's ``sr_attempts`` evidence over ANY Anki sync (AnkiWeb or a
self-hosted server) by encoding each attempt as a hidden Anki note, then decoding
the notes back into ``sr_attempts`` on the other device.

Stock Anki peers - including AnkiWeb - drop Speedrun's custom ``sr_attempts`` sync
chunk, so exam-style practice history otherwise never reaches a second device
unless you self-host the fork's sync server on the same LAN. Encoding attempts as
notes lets them ride the battle-tested note sync everywhere, so plain AnkiWeb
login is enough.

The encode/decode themselves live in ``rslib`` (``speedrun::notesync``) so the
desktop and the Android app drive the *same* implementation and wire format
through the backend RPCs - this module is just the desktop-side call site that
the sync flow hooks (``qt/aqt/speedrun.py``). Each attempt becomes one note in a
dedicated, suspended "Speedrun Data" notetype + deck, keyed by the attempt's
globally-unique millisecond id; encode skips already-noted ids and decode is
insert-if-absent by id, so both are idempotent and converge across devices with
no merge conflict.
"""

from __future__ import annotations

from anki.collection import Collection

# Kept in sync with the Rust constants in ``rslib/src/speedrun/notesync.rs`` so
# call sites and tests can refer to the notetype/deck the attempts live under.
NOTETYPE_NAME = "Speedrun Data"
DECK_NAME = "Speedrun Data"
DATA_TAG = "speedrun_data"


def encode_attempts(col: Collection) -> int:
    """Mirror new ``sr_attempts`` into hidden notes so the standard note sync
    carries them. Returns the number of newly-encoded attempts. Idempotent."""
    return col._backend.encode_attempts_as_notes()


def decode_attempts(col: Collection) -> int:
    """Insert attempts carried by notes that aren't already in ``sr_attempts``.
    Returns the number newly inserted. Idempotent (insert-if-absent by id)."""
    return col._backend.decode_notes_to_attempts()
