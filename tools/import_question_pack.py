#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Import a hand-authored Speedrun question pack into a collection (AI-off).

Each pack question is registered as a held-out exam-style item in
`sr_question_items` via the SpeedrunService. If a question has a `card_tag`, it
is linked to a source card carrying that tag so the recall-vs-performance gap
can be computed for that concept.

Usage:
    python tools/import_question_pack.py                       # synthetic self-test (bundled pack)
    python tools/import_question_pack.py collection.anki2      # bundled pack -> collection
    python tools/import_question_pack.py pack.json collection.anki2

Run via the wrapper so the built pylib bridge is on the path:
    ./tools/import_question_pack.sh [args]
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

from anki.collection import Collection
from anki import speedrun_pb2

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PACK = os.path.join(_HERE, "speedrun_question_pack.json")

_PAYLOAD_KEYS = ("stem", "options", "correct_index", "explanation")


def import_pack(col: Collection, pack: dict) -> tuple[int, int]:
    """Register each question; return (imported, linked_to_a_card)."""
    backend = col._backend
    imported = 0
    linked = 0
    for question in pack["questions"]:
        card_id = 0
        tag = question.get("card_tag")
        if tag:
            cards = col.find_cards(f"tag:{tag}")
            if cards:
                card_id = cards[0]
                linked += 1
        payload = json.dumps({k: question[k] for k in _PAYLOAD_KEYS if k in question})
        backend.add_question_item(
            speedrun_pb2.QuestionItem(
                card_id=card_id,
                topic=question.get("topic", ""),
                # 0=hand_authored, 1=open_licensed, 2=ai_generated
                provenance=int(question.get("provenance", 0)),
                payload=payload,
            )
        )
        imported += 1
    return imported, linked


def _load_pack(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _self_test() -> int:
    pack = _load_pack(DEFAULT_PACK)

    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(path)
    col = Collection(path)
    try:
        # one tagged card per topic referenced by the pack
        model = col.models.by_name("Basic")
        did = col.decks.id("Default")
        tags = sorted({q["card_tag"] for q in pack["questions"] if q.get("card_tag")})
        tag_to_cid: dict[str, int] = {}
        for tag in tags:
            note = col.new_note(model)
            note["Front"] = f"concept for {tag}"
            note.tags = [tag]
            col.add_note(note, did)
            tag_to_cid[tag] = note.cards()[0].id

        imported, linked = import_pack(col, pack)
        total = len(pack["questions"])
        print(f"imported {imported} questions, linked {linked} to a source card")

        assert imported == total, (imported, total)
        assert linked == total, (linked, total)

        # fc1 has three questions in the bundled pack
        fc1_items = col._backend.get_question_items_for_card(card_id=tag_to_cid["fc1"])
        assert len(fc1_items) == 3, len(fc1_items)
        assert all(i.provenance == 0 for i in fc1_items)
        print("self-test: PASS")
    finally:
        col.close()
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        return _self_test()

    if len(args) == 1:
        pack_path, col_path = DEFAULT_PACK, args[0]
    else:
        pack_path, col_path = args[0], args[1]

    pack = _load_pack(pack_path)
    col = Collection(col_path)
    try:
        imported, linked = import_pack(col, pack)
        print(f"imported {imported} questions, linked {linked} to a source card")
    finally:
        col.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
