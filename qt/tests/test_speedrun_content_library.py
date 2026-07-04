# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Guards on the bundled open-licensed MCAT content library.

The library (built by ``tools/build_content_library.py``) fills every AAMC
content category with source-cited cards + practice questions that power the
practice bank, the onboarding diagnostic, and the e2e deck. This test is Qt-free
(json + pathlib), so it runs headlessly and just asserts the shipped data is
well-formed.
"""

from __future__ import annotations

import json
from pathlib import Path

_LIB = (
    Path(__file__).resolve().parents[1]
    / "aqt"
    / "data"
    / "web"
    / "imgs"
    / "speedrun_content_library.json"
)
_SUBJECTS = {
    "biology",
    "biochemistry",
    "general_chemistry",
    "physics",
    "psychology_sociology",
}


def _topics() -> dict:
    return json.loads(_LIB.read_text(encoding="utf-8"))["topics"]


def test_covers_all_31_content_categories() -> None:
    assert len(_topics()) == 31


def test_every_topic_has_content_and_a_known_subject() -> None:
    for cid, topic in _topics().items():
        assert topic["cards"], f"{cid} has no cards"
        assert topic["questions"], f"{cid} has no questions"
        assert topic["subject"] in _SUBJECTS, f"{cid}: {topic['subject']}"


def test_questions_are_well_formed_and_source_cited() -> None:
    for cid, topic in _topics().items():
        for q in topic["questions"]:
            assert len(q["options"]) >= 3, cid
            assert 0 <= q["correct_index"] < len(q["options"]), cid
            assert q["stem"], cid
            assert q["source"], cid


def test_cards_are_normalized_for_html_and_plain_text() -> None:
    # normalization strips HTML entities so cards render in both Anki HTML fields
    # and the phone's plain-text views (see tools/build_content_library.py).
    blob = _LIB.read_text(encoding="utf-8")
    assert "&gt;" not in blob
    assert "&lt;" not in blob
