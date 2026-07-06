# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Round-trip tests for the AnkiWeb note-encoding sync path.

sr_attempts is encoded as hidden notes that ride the standard note sync, then
decoded back on the other device. The properties that matter: the round-trip
preserves attempt identity + fields, the data cards are suspended (never surface
for review), and both encode and decode are idempotent + convergent.
"""

from __future__ import annotations

from anki.collection import Collection
from aqt import speedrun_notesync as notesync

_INSERT = (
    "insert into sr_attempts (id, cid, nid, session_id, answered_at_ms, took_ms, "
    "question_type, selected, correct, diagnosis_kind, diagnosis_confidence, "
    "routed_action, action_status, usn, data, predicted, topic) "
    "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _insert_attempt(col: Collection, aid: int, correct: bool, topic: str) -> None:
    col.db.execute(
        _INSERT,
        aid,
        12,
        34,
        "s",
        aid,
        5000,
        1,
        None,
        1 if correct else 0,
        0,
        0.0,
        0,
        0,
        -1,
        "{}",
        None,
        topic,
    )


class TestNoteSyncRoundTrip:
    def test_round_trip_preserves_attempts_and_is_idempotent(self, tmp_path) -> None:
        col = Collection(str(tmp_path / "collection.anki2"))
        try:
            _insert_attempt(col, 1_700_000_000_001, correct=True, topic="biology")
            _insert_attempt(col, 1_700_000_000_002, correct=False, topic="physics")
            orig = col.db.all(
                "select id, cid, correct, topic from sr_attempts order by id"
            )

            # encode -> one hidden note per attempt
            assert notesync.encode_attempts(col) == 2
            assert len(col.find_notes(f'note:"{notesync.NOTETYPE_NAME}"')) == 2
            # the data cards are suspended (queue -1) so they never appear in study
            cids = list(col.find_cards(f'deck:"{notesync.DECK_NAME}"'))
            assert cids
            assert all(col.get_card(c).queue == -1 for c in cids)

            # simulate the second device: attempts gone, only the synced notes remain
            col.db.execute("delete from sr_attempts")
            assert notesync.decode_attempts(col) == 2
            assert (
                col.db.all(
                    "select id, cid, correct, topic from sr_attempts order by id"
                )
                == orig
            )

            # idempotent both directions (no duplicate notes / rows)
            assert notesync.encode_attempts(col) == 0
            assert notesync.decode_attempts(col) == 0
        finally:
            col.close()

    def test_encode_with_no_attempts_is_a_noop(self, tmp_path) -> None:
        col = Collection(str(tmp_path / "collection.anki2"))
        try:
            assert notesync.encode_attempts(col) == 0
            assert notesync.decode_attempts(col) == 0
        finally:
            col.close()
