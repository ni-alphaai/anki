# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Auto-group a deck's cards into the 31 AAMC MCAT content categories.

Imported decks (e.g. MileDown) aren't tagged with our content-category ids, so
they never populate the topic dashboard. This classifies each untagged note into
a content category and tags it, so coverage/memory/performance attribute per
topic exactly like the bundled content library.

Hybrid, in order of confidence:
  1. Skip notes that already carry a content-category tag (1A..10A).
  2. A deterministic keyword classifier built from the content library's own
     card text (idf-weighted term overlap) -- fully offline, the robust core.
  3. For notes the keyword pass can't place confidently, the optional AI coach
     (when enabled) classifies the residual; it degrades to leaving them untagged
     when AI is off/unavailable, so grouping never depends on the network.
"""

from __future__ import annotations

import math
import re
from typing import Any

from aqt.utils import tooltip

from . import speedrun_ai
from . import speedrun_library as library

_TOKEN_RE = re.compile(r"[a-z][a-z0-9-]{2,}")
_HTML_RE = re.compile(r"<[^>]+>")

# Common English + Anki-template words that carry no topical signal.
_STOP = {
    "the",
    "and",
    "for",
    "are",
    "was",
    "were",
    "with",
    "that",
    "this",
    "from",
    "which",
    "into",
    "have",
    "has",
    "had",
    "not",
    "but",
    "its",
    "their",
    "them",
    "these",
    "those",
    "than",
    "then",
    "when",
    "what",
    "why",
    "how",
    "who",
    "can",
    "will",
    "would",
    "may",
    "might",
    "such",
    "also",
    "each",
    "both",
    "between",
    "during",
    "because",
    "about",
    "over",
    "under",
    "more",
    "most",
    "some",
    "any",
    "all",
    "one",
    "two",
    "type",
    "types",
    "form",
    "forms",
    "used",
    "use",
    "using",
    "example",
    "examples",
    "following",
    "called",
    "known",
    "due",
    "within",
    "via",
    "front",
    "back",
    "card",
    "cards",
    "question",
    "answer",
    "true",
    "false",
}

# A keyword hit below this idf-weighted score is treated as "unplaced".
_MIN_SCORE = 2.4
# Cap a single grouping pass so a huge deck can't freeze the UI thread.
_SCAN_LIMIT = 4000

_model_cache: dict[str, Any] | None = None


def _plain(text: str) -> str:
    return _HTML_RE.sub(" ", text or "")


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(_plain(text).lower()) if t not in _STOP]


def _build_model() -> dict[str, Any]:
    """Per content-category term set + a global idf, from the content library's
    names, concepts and card text -- a rich topical vocabulary per category."""
    lib = library._load_pack(library._CONTENT_PACK) or {}
    topics: dict[str, dict] = lib.get("topics", {})
    cat_terms: dict[str, set[str]] = {}
    for cid, t in topics.items():
        parts = [str(t.get("name", "")), str(t.get("concept", ""))]
        for card in t.get("cards", []):
            parts.append(str(card.get("front", "")))
            parts.append(str(card.get("back", "")))
        cat_terms[cid] = set(_tokens(" ".join(parts)))
    df: dict[str, int] = {}
    for terms in cat_terms.values():
        for term in terms:
            df[term] = df.get(term, 0) + 1
    n = max(len(cat_terms), 1)
    idf = {term: math.log((n + 1) / (d + 0.5)) for term, d in df.items()}
    return {"cat_terms": cat_terms, "idf": idf, "topics": topics}


def _model() -> dict[str, Any]:
    global _model_cache
    if _model_cache is None:
        _model_cache = _build_model()
    return _model_cache


def classify_text(
    text: str, model: dict[str, Any] | None = None
) -> tuple[str | None, float]:
    """Best content-category id for a note's text and its idf-weighted score.
    Distinctive terms (rare across categories) dominate, so a couple of on-topic
    technical words outweigh many generic ones."""
    m = model or _model()
    seen = set(_tokens(text))
    if not seen:
        return (None, 0.0)
    best_cid, best_score = None, 0.0
    for cid, terms in m["cat_terms"].items():
        score = sum(m["idf"].get(t, 0.0) for t in seen if t in terms)
        if score > best_score:
            best_cid, best_score = cid, score
    return (best_cid, best_score)


def ungrouped_note_count(col: Any) -> int:
    """How many notes carry no content-category tag yet (so the UI can offer to
    group them). Returns 0 on any error or when no content categories exist."""
    m = _model()
    cat_ids = list(m["cat_terms"])
    if not cat_ids:
        return 0
    try:
        search = " ".join(f"-tag:{cid}" for cid in cat_ids)
        return len(col.find_notes(search))
    except Exception:
        return 0


def group_notes(mw: Any, use_ai: bool | None = None) -> dict[str, int]:
    """Tag untagged notes with their content category. ``use_ai`` defaults to the
    AI-coach toggle. Returns counts: scanned / tagged / ai / residual."""
    col = mw.col
    m = _model()
    cat_ids = list(m["cat_terms"])
    result = {"scanned": 0, "tagged": 0, "ai": 0, "residual": 0}
    if col is None or not cat_ids:
        return result

    # We tag by content-category id (1A..10E), so make the coverage map the 31
    # categories too -- otherwise (e.g. a coarse 10-FC map) the grouped cards
    # attribute to no topic and the dashboard shows 0 cards everywhere.
    library.ensure_content_topic_map(col)

    search = " ".join(f"-tag:{cid}" for cid in cat_ids)
    try:
        nids = list(col.find_notes(search))[:_SCAN_LIMIT]
    except Exception:
        return result
    result["scanned"] = len(nids)

    assign: dict[str, list] = {}
    residual: list[tuple[Any, str]] = []
    for nid in nids:
        try:
            note = col.get_note(nid)
        except Exception:
            continue
        text = " ".join(note.fields)
        cid, score = classify_text(text, m)
        if cid and score >= _MIN_SCORE:
            assign.setdefault(cid, []).append(nid)
        else:
            residual.append((nid, text))

    want_ai = speedrun_ai.enabled(col) if use_ai is None else use_ai
    if residual and want_ai and speedrun_ai.available():
        topics = m["topics"]
        ai_map = (
            speedrun_ai.classify_categories(
                [
                    {"id": str(nid), "text": _plain(text)[:600]}
                    for nid, text in residual
                ],
                [
                    {
                        "id": cid,
                        "name": str(topics[cid].get("name", "")),
                        "concept": str(topics[cid].get("concept", "")),
                    }
                    for cid in cat_ids
                ],
            )
            or {}
        )
        placed = set()
        for nid, _ in residual:
            cid = ai_map.get(str(nid))
            if cid in m["cat_terms"]:
                assign.setdefault(cid, []).append(nid)
                placed.add(nid)
        result["ai"] = len(placed)

    for cid, ns in assign.items():
        try:
            col.tags.bulk_add(ns, cid)
            result["tagged"] += len(ns)
        except Exception:
            pass
    result["residual"] = result["scanned"] - result["tagged"]
    return result


def group_and_report(mw: Any) -> None:
    """Run grouping with a busy cursor and surface a one-line result."""
    if mw.col is None:
        return
    mw.progress.start(label="Grouping cards into MCAT topics…")
    try:
        r = group_notes(mw)
    finally:
        mw.progress.finish()
    if r["scanned"] == 0:
        tooltip("All cards are already grouped into MCAT topics.")
    else:
        extra = f" ({r['ai']} by AI)" if r["ai"] else ""
        left = f", {r['residual']} left unplaced" if r["residual"] else ""
        tooltip(
            f"Grouped {r['tagged']} of {r['scanned']} cards into topics{extra}{left}."
        )
