#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Ablation harness for the points-at-stake queue (AI-off).

Builds the study order for a deck twice - with the SpeedrunPointsAtStake toggle
OFF and ON - and reports how the ordering changes. This is the study-feature
ablation: it shows the engine change actually reorders due cards (weak,
high-value cards move toward the front) rather than being a no-op.

Usage:
    python tools/speedrun_ablation.py                  # synthetic self-test
    python tools/speedrun_ablation.py collection.anki2 [deck_id]

Run via the wrapper so the built pylib bridge is on the path:
    ./tools/speedrun_ablation.sh [args]
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

from anki.collection import Collection
from anki import speedrun_pb2

_FLAG = "speedrunPointsAtStake"

# card type / queue values for a mature review card
_CARD_TYPE_REVIEW = 2
_QUEUE_REVIEW = 2


def _order(col: Collection, deck_id: int, enabled: bool) -> list[int]:
    col.set_config(_FLAG, enabled)
    return list(col._backend.get_review_order(deck_id=deck_id))


def ablation(col: Collection, deck_id: int) -> dict:
    off = _order(col, deck_id, False)
    on = _order(col, deck_id, True)
    positions_changed = sum(1 for a, b in zip(off, on) if a != b)
    return {
        "deck_id": deck_id,
        "order_off": off,
        "order_on": on,
        "positions_changed": positions_changed,
        "same_cards": sorted(off) == sorted(on),
    }


def _make_review_card(col: Collection, did: int, front: str) -> int:
    model = col.models.by_name("Basic")
    note = col.new_note(model)
    note["Front"] = front
    col.add_note(note, did)
    card = note.cards()[0]
    card.type = _CARD_TYPE_REVIEW
    card.queue = _QUEUE_REVIEW
    card.ivl = 10
    card.due = 0
    col.update_card(card)
    return card.id


def _record_miss(col: Collection, cid: int) -> None:
    col._backend.record_attempt(
        speedrun_pb2.RecordAttemptRequest(
            card_id=cid,
            note_id=1,
            question_type=0,
            correct=False,
            signals=speedrun_pb2.ClassifyAttemptRequest(
                correct=False, recall_failed=True, question_type=0
            ),
        )
    )


def _self_test() -> int:
    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(path)
    col = Collection(path)
    try:
        did = col.decks.id("Default")
        # three due review cards
        for i in range(3):
            _make_review_card(col, did, f"card {i}")

        # The default review order is non-deterministic across runs, so derive
        # the weak card from it: whichever card sorts LAST with the feature OFF
        # becomes the weak one, so turning the feature ON must move it to the
        # front - a guaranteed, deterministic ordering change.
        off = _order(col, did, False)
        weak_id = off[-1]
        _record_miss(col, weak_id)
        _record_miss(col, weak_id)

        on = _order(col, did, True)
        positions_changed = sum(1 for a, b in zip(off, on) if a != b)
        report = {
            "deck_id": did,
            "order_off": off,
            "order_on": on,
            "positions_changed": positions_changed,
            "same_cards": sorted(off) == sorted(on),
            "weak_id": weak_id,
        }
        print(json.dumps(report, indent=2))

        assert report["same_cards"], report
        assert len(on) == 3, report
        # the weak, high-value card is surfaced first when the feature is ON
        assert on[0] == weak_id, report
        # and the ordering actually changed vs OFF
        assert positions_changed > 0, report
        print("\nself-test: PASS")
    finally:
        col.close()
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        return _self_test()

    col_path = args[0]
    deck_id = int(args[1]) if len(args) > 1 else 1
    col = Collection(col_path)
    try:
        print(json.dumps(ablation(col, deck_id), indent=2))
    finally:
        col.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
