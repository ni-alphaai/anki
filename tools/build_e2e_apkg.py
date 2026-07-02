#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Build the curated biology e2e deck as an .apkg for the Android app.

Desktop creates the e2e cards directly via the collection API; Android imports
decks as .apkg, so we generate one from the same bundled JSON so both platforms
ship the identical curated deck (name "Speedrun Biology (e2e)", cards tagged
"biology"). The deck name resolves to the "biology" topic, so the reasoning
round pulls the matched biology questions.

Run via the wrapper so the built pylib bridge is on the path:
    PYTHONPATH=out/pylib:pylib out/pyenv/bin/python tools/build_e2e_apkg.py [out.apkg]
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

from anki.collection import Collection, DeckIdLimit, ExportAnkiPackageOptions

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PACK = os.path.join(_ROOT, "qt", "aqt", "data", "web", "imgs", "speedrun_e2e_biology.json")
DEFAULT_OUT = os.path.join(
    _ROOT, "androidapp", "app", "src", "main", "assets", "speedrun_e2e_biology.apkg"
)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    with open(PACK, encoding="utf-8") as handle:
        pack = json.load(handle)

    fd, tmp = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(tmp)
    col = Collection(tmp)
    try:
        did = col.decks.id(pack["deck"])
        model = col.models.by_name("Basic")
        for card in pack["cards"]:
            note = col.new_note(model)
            note["Front"] = card["front"]
            note["Back"] = card["back"]
            note.tags = [pack["topic"]]
            col.add_note(note, did)
        opts = ExportAnkiPackageOptions(
            with_scheduling=False,
            with_deck_configs=False,
            with_media=False,
            legacy=True,
        )
        os.makedirs(os.path.dirname(out), exist_ok=True)
        count = col.export_anki_package(
            out_path=out, options=opts, limit=DeckIdLimit(deck_id=did)
        )
        print(f"exported {count} notes -> {out}")
    finally:
        col.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
