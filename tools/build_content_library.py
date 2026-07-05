#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Assemble the open-licensed MCAT content library from per-chunk source files.

The library fills every one of the 31 AAMC content categories (ids 1A..10A) with
source-cited flashcards and practice questions grounded in openly-licensed
material (OpenStax, CC BY 4.0), so the practice bank, the onboarding diagnostic,
and the e2e deck all have real, per-topic content instead of placeholders.

Inputs: one or more JSON chunk files under ``tools/content_src/``, each shaped
``{"<id>": {"cards": [...], "questions": [...]}}`` (any partition of the 31
ids). This builder merges them, attaches the outline metadata (name / concept /
section) plus the section-subject tag, normalizes the text so it renders cleanly
in both Anki's HTML fields and the phone's plain-text views, validates the
shape, and writes the bundled library:

    qt/aqt/data/web/imgs/speedrun_content_library.json

Run it with any Python 3 (no built pylib needed):

    python tools/build_content_library.py            # build (all 31 required)
    python tools/build_content_library.py --check    # validate only, no write
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SRC_DIR = os.path.join(_HERE, "content_src")
_OUTLINE = os.path.join(_HERE, "speedrun_mcat_outline.json")
_OUT = os.path.join(
    _ROOT, "qt", "aqt", "data", "web", "imgs", "speedrun_content_library.json"
)

# Each content category maps to one of the five practice-bank subject tags used
# by the Practice landing (mirrors qt/aqt/speedrun_mcat.py subjects). CARS has no
# content categories, so it is absent by construction.
SUBJECT_BY_ID = {
    "1A": "biochemistry",
    "1B": "biochemistry",
    "1C": "biology",
    "1D": "biochemistry",
    "2A": "biology",
    "2B": "biology",
    "2C": "biology",
    "3A": "biology",
    "3B": "biology",
    "4A": "physics",
    "4B": "physics",
    "4C": "physics",
    "4D": "physics",
    "4E": "general_chemistry",
    "5A": "general_chemistry",
    "5B": "general_chemistry",
    "5C": "general_chemistry",
    "5D": "biochemistry",
    "5E": "general_chemistry",
    "6A": "psychology_sociology",
    "6B": "psychology_sociology",
    "6C": "psychology_sociology",
    "7A": "psychology_sociology",
    "7B": "psychology_sociology",
    "7C": "psychology_sociology",
    "8A": "psychology_sociology",
    "8B": "psychology_sociology",
    "8C": "psychology_sociology",
    "9A": "psychology_sociology",
    "9B": "psychology_sociology",
    "10A": "psychology_sociology",
}


def _normalize(text: str) -> str:
    """Make one text field safe for BOTH Anki HTML fields and phone plain text.

    Source chunks may carry HTML entities (``-&gt;``, ``&lt;``) from the author.
    We unescape them, turn ASCII arrows into a real arrow glyph, and spell out
    the ``<``/``>`` comparisons so a literal ``<`` never opens a bogus tag when
    Anki renders the field as HTML (the phone shows plain text either way).
    """
    s = html.unescape(str(text))
    s = s.replace("->", "→")
    s = re.sub(r"\s*<\s*", " less than ", s)
    s = re.sub(r"\s*>\s*", " greater than ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def _load_outline() -> dict[str, dict]:
    with open(_OUTLINE, encoding="utf-8") as f:
        outline = json.load(f)
    return {t["id"]: t for t in outline["topics"]}


def _load_chunks() -> dict[str, dict]:
    merged: dict[str, dict] = {}
    files = sorted(glob.glob(os.path.join(_SRC_DIR, "*.json")))
    if not files:
        raise SystemExit(f"no chunk files found in {_SRC_DIR}")
    for path in files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for cid, payload in data.items():
            if cid in merged:
                raise SystemExit(f"duplicate category {cid} (in {path})")
            merged[cid] = payload
    return merged


def _clean_card(card: dict) -> dict:
    return {
        "front": _normalize(card["front"]),
        "back": _normalize(card["back"]),
        "source": _normalize(card.get("source", "")),
        "license": card.get("license", "CC BY 4.0"),
    }


def _clean_question(q: dict) -> dict:
    options = [_normalize(o) for o in q["options"]]
    return {
        "stem": _normalize(q["stem"]),
        "options": options,
        "correct_index": int(q["correct_index"]),
        "explanation": _normalize(q.get("explanation", "")),
        "source": _normalize(q.get("source", "")),
        "license": q.get("license", "CC BY 4.0"),
    }


def _validate(cid: str, topic: dict) -> list[str]:
    errs: list[str] = []
    cards = topic["cards"]
    questions = topic["questions"]
    if not cards:
        errs.append(f"{cid}: no cards")
    if not questions:
        errs.append(f"{cid}: no questions")
    for i, card in enumerate(cards):
        if not card["front"] or not card["back"]:
            errs.append(f"{cid} card {i}: empty front/back")
    for i, q in enumerate(questions):
        if len(q["options"]) < 3:
            errs.append(f"{cid} q{i}: needs >=3 options, got {len(q['options'])}")
        if not 0 <= q["correct_index"] < len(q["options"]):
            errs.append(f"{cid} q{i}: correct_index {q['correct_index']} out of range")
        if not q["stem"]:
            errs.append(f"{cid} q{i}: empty stem")
    return errs


def build(check_only: bool, require_all: bool) -> int:
    outline = _load_outline()
    chunks = _load_chunks()

    topics: dict[str, dict] = {}
    errs: list[str] = []
    for cid, payload in chunks.items():
        meta = outline.get(cid)
        if meta is None:
            errs.append(f"{cid}: not in the AAMC outline")
            continue
        # Drop any fully-blank placeholder entries a source chunk may carry.
        raw_cards = [
            c for c in payload.get("cards", []) if c.get("front") or c.get("back")
        ]
        raw_qs = [q for q in payload.get("questions", []) if q.get("stem")]
        topic = {
            "name": meta["name"],
            "concept": meta["concept"],
            "section": meta["section"],
            "weight": float(meta["weight"]),
            "subject": SUBJECT_BY_ID[cid],
            "cards": [_clean_card(c) for c in raw_cards],
            "questions": [_clean_question(q) for q in raw_qs],
        }
        errs.extend(_validate(cid, topic))
        topics[cid] = topic

    missing = [cid for cid in outline if cid not in topics]
    if require_all and missing:
        errs.append(f"missing {len(missing)} categories: {', '.join(missing)}")

    if errs:
        print("VALIDATION FAILED:")
        for e in errs:
            print(f"  - {e}")
        return 1

    n_cards = sum(len(t["cards"]) for t in topics.values())
    n_q = sum(len(t["questions"]) for t in topics.values())
    print(
        f"OK: {len(topics)}/31 categories, {n_cards} cards, {n_q} questions"
        + (f" ({len(missing)} categories not yet present)" if missing else "")
    )

    if check_only:
        return 0

    library = {
        "name": "Speedrun open-licensed MCAT content library",
        "source_note": (
            "Original study cards and practice questions authored for Speedrun, "
            "grounded in openly-licensed sources (primarily OpenStax, CC BY 4.0). "
            "Each item cites its source. This is NOT AAMC/UWorld content."
        ),
        "license_note": (
            "Card and question text is original and distributed under CC BY 4.0 "
            "with attribution to the cited OpenStax sources."
        ),
        "topics": {cid: topics[cid] for cid in outline if cid in topics},
    }
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(library, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"wrote {_OUT}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="validate only; do not write"
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help="allow fewer than 31 categories (for incremental assembly)",
    )
    args = parser.parse_args()
    return build(check_only=args.check, require_all=not args.partial)


if __name__ == "__main__":
    sys.exit(main())
