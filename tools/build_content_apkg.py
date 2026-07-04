#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Build the Android assets for the open-licensed MCAT content library.

Desktop creates the content-library cards directly via the collection API;
Android imports decks as .apkg, so we generate one from the same bundled library
JSON, plus a questions JSON, so both platforms ship the identical multi-topic
content across all 31 AAMC content categories. Cards are tagged with their
content-category id and subject, exactly like the desktop import.

Outputs (Android assets):
  androidapp/app/src/main/assets/speedrun_content_library.apkg   (186 cards)
  androidapp/app/src/main/assets/speedrun_content_questions.json (124 questions)

Run via the wrapper so the built pylib bridge is on the path:
    PYTHONPATH=out/pylib:pylib out/pyenv/bin/python tools/build_content_apkg.py
"""

from __future__ import annotations

import json
import os
import tempfile

from anki.collection import Collection, DeckIdLimit, ExportAnkiPackageOptions

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_LIBRARY = os.path.join(
    _ROOT, "qt", "aqt", "data", "web", "imgs", "speedrun_content_library.json"
)
_ASSETS = os.path.join(_ROOT, "androidapp", "app", "src", "main", "assets")
_APKG = os.path.join(_ASSETS, "speedrun_content_library.apkg")
_QUESTIONS = os.path.join(_ASSETS, "speedrun_content_questions.json")
_TOPIC_MAP = os.path.join(_ASSETS, "speedrun_content_topics.json")
_DECK = "MCAT Content Library"


def main() -> int:
    with open(_LIBRARY, encoding="utf-8") as f:
        topics = json.load(f)["topics"]

    # The 31-category coverage map (id -> label + weight) so Android can load the
    # same content outline as desktop via SetTopicMap.
    topic_map = [
        {"topic": cid, "label": t.get("name", cid), "weight": float(t.get("weight", 1.0))}
        for cid, t in topics.items()
    ]

    fd, tmp = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(tmp)
    col = Collection(tmp)
    n_cards = 0
    questions: list[dict] = []
    try:
        did = col.decks.id(_DECK)
        model = col.models.by_name("Basic")
        for cid, topic in topics.items():
            subject = str(topic.get("subject", ""))
            for card in topic.get("cards", []):
                note = col.new_note(model)
                note["Front"] = card.get("front", "")
                note["Back"] = card.get("back", "")
                note.tags = [tag for tag in (cid, subject) if tag]
                col.add_note(note, did)
                n_cards += 1
            for q in topic.get("questions", []):
                questions.append(
                    {
                        "topic": subject,
                        "card_tag": cid,
                        "stem": q.get("stem", ""),
                        "options": q.get("options", []),
                        "correct_index": q.get("correct_index", 0),
                        "explanation": q.get("explanation", ""),
                        "provenance": 1,
                    }
                )
        opts = ExportAnkiPackageOptions(
            with_scheduling=False,
            with_deck_configs=False,
            with_media=False,
            legacy=True,
        )
        os.makedirs(_ASSETS, exist_ok=True)
        col.export_anki_package(
            out_path=_APKG, options=opts, limit=DeckIdLimit(deck_id=did)
        )
    finally:
        col.close()

    with open(_QUESTIONS, "w", encoding="utf-8") as f:
        json.dump({"questions": questions}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(_TOPIC_MAP, "w", encoding="utf-8") as f:
        json.dump({"topics": topic_map}, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"exported {n_cards} cards -> {_APKG}")
    print(f"wrote {len(questions)} questions -> {_QUESTIONS}")
    print(f"wrote {len(topic_map)} topic-map entries -> {_TOPIC_MAP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
