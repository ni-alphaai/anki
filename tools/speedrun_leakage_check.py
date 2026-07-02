#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun projectspec s7e "leakage check" (AI-off).

The held-out MCAT question bank only measures *performance* if its items are
not copies of the flashcards the student already studies. If a held-out item is
a verbatim copy - or a lightly reworded near-copy - of a study card, then
"performance" is just recall in disguise and the performance model is worthless.
Likewise the diagnosis gold set is only a fair yardstick if its items do not
duplicate one another. This scanner flags any such leakage, and is meant to be
run to *prove the real packs are clean*.

Two layers, because a substring check alone is not enough:

  1. Engine verbatim layer (needs the built pylib bridge). For each pack it
     builds a fresh temp collection, materializes the study cards implied by the
     pack's ``card_tag``s (front text = a representative fact, standing in for
     what the student studied), links every question as an ``add_question_item``,
     and calls the Rust ``get_leakage_report()`` RPC. That RPC flags a question
     when its normalized stem is a substring of its linked source card's note
     text - the exact same check the desktop app and phone use.

  2. Python near-duplicate layer (always runs, no engine required). A substring
     check misses a *reworded* copy ("straight up" vs "straight upward"), so this
     layer measures token-set similarity - Jaccard over normalized word sets and
     over character n-grams - between every question stem and (a) its source
     card stand-in, (b) every other stem in the corpus, and (c) the gold items.
     Pairs at/above the stated threshold are flagged as near-duplicates.

The projectspec s7e leakage conditions that drive the verdict / exit code are
specifically:

  * L1  a held-out stem is a *verbatim* copy of its study card,
  * L2  a held-out stem is a *near* copy of its study card,
  * L3  a gold item duplicates another gold item.

Redundancy that is *not* s7e leakage - e.g. two near-identical items inside the
open-licensed MMLU pool (both held-out, neither is a study card), or the same
MCAT scenario deliberately reused across the performance pack and the diagnosis
gold set (they feed different models) - is reported separately as a data-hygiene
audit and does not fail the run.

Usage:
    python tools/speedrun_leakage_check.py                # scan the 3 real packs
    python tools/speedrun_leakage_check.py pack.json ...  # scan specific packs

Every run first self-tests the detectors on a planted verbatim copy and a
planted near-copy, so a "CLEAN" verdict is meaningful. Prints a JSON summary on
stdout; exits non-zero if real (s7e) leakage is found or the self-test fails.

Run via the wrapper so the built pylib + bridge are on the path (the near-dup
layer still runs without them):
    ./tools/speedrun_leakage_check.sh
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional

# --- thresholds (also restated in tools/speedrun_leakage_report.md) ---------

# Minimum normalized stem length for the verbatim substring check. Mirrors
# rslib/src/speedrun/leakage.rs::MIN_STEM_LEN; shorter stems are too generic.
MIN_STEM_LEN = 12
# A pair is a near-duplicate when EITHER similarity is at/above threshold.
WORD_JACCARD_THRESHOLD = 0.80
CHAR_JACCARD_THRESHOLD = 0.80
CHAR_NGRAM_N = 5
# Only bother computing the (pricier) char-n-gram score for pairs whose word
# score already clears this bar; a pair below it cannot reach CHAR threshold
# (sharing ~80% of char n-grams implies sharing far more than 40% of words).
CANDIDATE_WORD_JACCARD = 0.40

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PACKS = [
    os.path.join(TOOLS_DIR, "speedrun_question_pack.json"),
    os.path.join(TOOLS_DIR, "speedrun_mmlu_pack.json"),
    os.path.join(TOOLS_DIR, "speedrun_gold_set.json"),
]


# --- normalization + verbatim check (ports of rslib/src/speedrun/leakage.rs) -


def normalize(text: str) -> str:
    """Lowercase, keep alphanumerics, collapse everything else to single spaces.

    Byte-for-byte port of rslib/src/speedrun/leakage.rs::normalize so the Python
    layer and the Rust engine agree on what "the same text" means.
    """
    out: list[str] = []
    prev_space = False
    for ch in text:
        if ch.isalnum():
            out.append(ch.lower())
            prev_space = False
        elif out and not prev_space:
            out.append(" ")
            prev_space = True
    return "".join(out).strip()


def is_leaked_substring(stem: str, note_text: str) -> bool:
    """Pure-Python mirror of rslib is_leaked(): a normalized stem of at least
    MIN_STEM_LEN chars that is a substring of the normalized note text."""
    stem_n = normalize(stem)
    if len(stem_n) < MIN_STEM_LEN:
        return False
    return stem_n in normalize(note_text)


# --- token-set similarity (the near-duplicate layer) ------------------------


def word_set(norm: str) -> set[str]:
    return set(norm.split())


def char_ngrams(norm: str, n: int = CHAR_NGRAM_N) -> set[str]:
    """Set of character n-grams over the spaceless normalized text. Robust to
    word reordering and small edits that fool a plain substring check."""
    s = norm.replace(" ", "")
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a) + len(b) - inter
    return inter / union if union else 0.0


def near_dup_scores(a: Item, b: Item) -> tuple[float, float]:
    """(word-set Jaccard, char-n-gram Jaccard). The char score is only computed
    when the word score is a plausible candidate, else returned as 0.0."""
    wj = jaccard(a.words, b.words)
    if wj < CANDIDATE_WORD_JACCARD:
        return wj, 0.0
    return wj, jaccard(a.ngrams, b.ngrams)


def is_near_dup(word_j: float, char_j: float) -> bool:
    return word_j >= WORD_JACCARD_THRESHOLD or char_j >= CHAR_JACCARD_THRESHOLD


# --- pack model -------------------------------------------------------------

# Role controls which s7e condition an item participates in.
HELD_OUT = "held_out"  # performance pack with card_tag -> linked to a study card
HELD_OUT_GLOBAL = "held_out_global"  # performance pack, no card_tag (unlinked)
GOLD = "gold"  # diagnosis gold set


@dataclass
class Item:
    pack: str
    role: str
    idx: int
    ident: str
    topic: str
    card_tag: Optional[str]
    stem: str
    explanation: str
    norm: str = ""
    words: set[str] = field(default_factory=set)
    ngrams: set[str] = field(default_factory=set)

    def finalize(self) -> "Item":
        self.norm = normalize(self.stem)
        self.words = word_set(self.norm)
        self.ngrams = char_ngrams(self.norm)
        return self


def pack_label(path: str) -> str:
    base = os.path.basename(path).removeprefix("speedrun_")
    for suf in ("_pack.json", ".json"):
        if base.endswith(suf):
            return base[: -len(suf)]
    return base


def load_pack(path: str) -> tuple[dict, list[Item]]:
    """Load a pack into (metadata, items). Handles all three shapes:

      * question pack: {questions:[{topic, card_tag, stem, ...}]}
      * mmlu pack:     {questions:[{topic, stem, ...}]}  (no card_tag)
      * gold set:      {items:[{id, topic, stem, gold_kind, ...}]}
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    label = pack_label(path)
    raw = data.get("questions")
    is_gold = raw is None
    if is_gold:
        raw = data.get("items", [])
    has_card_tag = any(isinstance(q, dict) and q.get("card_tag") for q in raw)

    if is_gold:
        role = GOLD
    elif has_card_tag:
        role = HELD_OUT
    else:
        role = HELD_OUT_GLOBAL

    items: list[Item] = []
    for i, q in enumerate(raw):
        stem = (q.get("stem") or "").strip()
        if not stem:
            continue
        items.append(
            Item(
                pack=label,
                role=role,
                idx=i,
                ident=str(q.get("id") or f"{label}#{i}"),
                topic=q.get("topic") or "",
                card_tag=q.get("card_tag"),
                stem=stem,
                explanation=(q.get("explanation") or "").strip(),
            ).finalize()
        )
    meta = {
        "name": data.get("name", label),
        "path": path,
        "label": label,
        "role": role,
        "items": len(items),
        "license": data.get("license"),
    }
    return meta, items


# --- engine verbatim layer (needs the built pylib bridge) -------------------


def engine_scan_pack(Collection, speedrun_pb2, label: str, items: list[Item]) -> dict:
    """Build a fresh temp collection for one pack, materialize its study cards,
    link every question, and call the Rust get_leakage_report() RPC."""
    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(path)  # Collection() creates it fresh
    col = Collection(path)
    try:
        model = col.models.by_name("Basic")
        did = col.decks.id("Default")

        # One study-card note per distinct card_tag. Its front text stands in for
        # what the student actually studied; we use the questions' own answer
        # explanations for that tag - the closest faithful proxy for the taught
        # fact, and non-trivial (a copy-pasted stem would be caught).
        tag_expl: dict[str, list[str]] = {}
        for it in items:
            if it.card_tag:
                tag_expl.setdefault(it.card_tag, []).append(it.explanation)
        tag_card: dict[str, int] = {}
        for tag, expls in tag_expl.items():
            note = col.new_note(model)
            note["Front"] = " ".join(e for e in expls if e) or f"study material for {tag}"
            note.tags = [tag]
            col.add_note(note, did)
            tag_card[tag] = note.cards()[0].id

        for it in items:
            cid = tag_card.get(it.card_tag, 0) if it.card_tag else 0
            col._backend.add_question_item(
                speedrun_pb2.QuestionItem(
                    card_id=cid,
                    topic=it.topic,
                    provenance=0,
                    payload=json.dumps({"stem": it.stem}),
                )
            )
        rep = col._backend.get_leakage_report()
        return {
            "total_items": rep.total_items,
            "linked_study_cards": len(tag_card),
            "flagged": rep.flagged,
            "clean": rep.clean,
        }
    finally:
        col.close()


def run_engine_layer(packs: list[tuple[dict, list[Item]]]) -> dict:
    try:
        from anki import speedrun_pb2
        from anki.collection import Collection
    except Exception as exc:  # pylib not built / not on path
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    results = {}
    for meta, items in packs:
        results[meta["label"]] = engine_scan_pack(
            Collection, speedrun_pb2, meta["label"], items
        )
    return {"available": True, "packs": results}


# --- near-duplicate layer (pure Python) -------------------------------------


def _pair(a: Item, b: Item, wj: float, cj: float) -> dict:
    return {
        "word_jaccard": round(wj, 3),
        "char_jaccard": round(cj, 3),
        "a": {"pack": a.pack, "id": a.ident, "stem": a.stem},
        "b": {"pack": b.pack, "id": b.ident, "stem": b.stem},
    }


def run_neardup_layer(items: list[Item]) -> dict:
    # (L1/L2) each linked held-out stem vs its study-card stand-in (its own
    # answer explanation): verbatim substring + token-set near-duplicate.
    study_verbatim: list[dict] = []
    study_neardup: list[dict] = []
    for it in items:
        if it.role != HELD_OUT or not it.explanation:
            continue
        if is_leaked_substring(it.stem, it.explanation):
            study_verbatim.append(
                {"pack": it.pack, "id": it.ident, "stem": it.stem}
            )
        exp_norm = normalize(it.explanation)
        wj = jaccard(it.words, word_set(exp_norm))
        cj = jaccard(it.ngrams, char_ngrams(exp_norm))
        if is_near_dup(wj, cj):
            study_neardup.append(
                {
                    "pack": it.pack,
                    "id": it.ident,
                    "stem": it.stem,
                    "word_jaccard": round(wj, 3),
                    "char_jaccard": round(cj, 3),
                }
            )

    # (L3 + audit) every stem vs every other stem in the corpus.
    n = len(items)
    gold_gold: list[dict] = []
    cross_pack: list[dict] = []
    intra_pack: list[dict] = []
    for i in range(n):
        a = items[i]
        for j in range(i + 1, n):
            b = items[j]
            wj, cj = near_dup_scores(a, b)
            if not is_near_dup(wj, cj):
                continue
            rec = _pair(a, b, wj, cj)
            if a.role == GOLD and b.role == GOLD:
                gold_gold.append(rec)
            elif a.pack == b.pack:
                intra_pack.append(rec)
            else:
                cross_pack.append(rec)

    # Split intra-pack redundancy into near-exact vs "structural twins" (same
    # bag of words but a lower char score - usually a genuinely different
    # question that merely reuses sentence scaffolding).
    def by_pack(recs: list[dict]) -> dict:
        out: dict[str, int] = {}
        for r in recs:
            out[r["a"]["pack"]] = out.get(r["a"]["pack"], 0) + 1
        return out

    near_exact = [r for r in intra_pack if r["char_jaccard"] >= 0.90]
    twins = [r for r in intra_pack if r["char_jaccard"] < 0.90]

    return {
        "held_out_vs_study_verbatim": study_verbatim,
        "held_out_vs_study_neardup": study_neardup,
        "gold_vs_gold": gold_gold,
        "audit": {
            "intra_pack_redundancy": {
                "total": len(intra_pack),
                "near_exact": len(near_exact),
                "structural_twins": len(twins),
                "by_pack": by_pack(intra_pack),
                "examples": (near_exact[:3] + twins[:2]),
            },
            "cross_pack_reuse": {
                "total": len(cross_pack),
                "pairs": cross_pack,
            },
        },
    }


# --- self-test: prove the detectors actually fire ---------------------------


def _mk(stem: str, pack: str = "synthetic", role: str = HELD_OUT) -> Item:
    return Item(
        pack=pack, role=role, idx=0, ident=pack, topic="", card_tag=None,
        stem=stem, explanation="",
    ).finalize()


def self_test(*, use_engine: bool) -> dict:
    """Feed the detectors a deliberately-leaked verbatim copy and a reworded
    near-copy and assert they fire; assert genuinely-distinct text does not.
    Without this, a "CLEAN" verdict would be unfalsifiable."""
    results: dict = {"checks": []}

    def check(label: str, cond: bool) -> None:
        results["checks"].append({"label": label, "pass": bool(cond)})
        if not cond:
            raise AssertionError(f"self-test FAILED: {label}")

    # --- verbatim substring detector (mirrors the Rust engine) ---
    note = "The peptide bond is an amide bond between residues."
    check("verbatim copy is flagged",
          is_leaked_substring("the peptide bond is an amide bond", note))
    check("reworded stem is NOT flagged as verbatim",
          not is_leaked_substring(
              "Which functional group links adjacent amino acids in a protein?", note))
    check("too-short stem is ignored", not is_leaked_substring("amino", note))

    # --- near-duplicate detector ---
    orig = _mk("A ball is thrown straight upward. At its highest point, its acceleration is:")
    near = _mk("A ball is thrown straight up. At its highest point, its acceleration is:")
    far = _mk("Which cofactor is required by transaminase enzymes?")
    wj, cj = near_dup_scores(orig, near)
    check("reworded near-copy is flagged", is_near_dup(wj, cj))
    wj2, cj2 = near_dup_scores(orig, far)
    check("unrelated stem is NOT flagged", not is_near_dup(wj2, cj2))
    results["planted_near_copy"] = {"word_jaccard": round(wj, 3), "char_jaccard": round(cj, 3)}

    # --- engine detector (only when the pylib bridge is available) ---
    engine: dict = {"ran": False}
    if use_engine:
        try:
            from anki import speedrun_pb2
            from anki.collection import Collection
        except Exception as exc:
            engine = {"ran": False, "reason": f"{type(exc).__name__}: {exc}"}
        else:
            fd, path = tempfile.mkstemp(suffix=".anki2")
            os.close(fd)
            os.unlink(path)
            col = Collection(path)
            try:
                model = col.models.by_name("Basic")
                did = col.decks.id("Default")
                # A study card whose front contains a stem verbatim -> must flag.
                n1 = col.new_note(model)
                n1["Front"] = note
                n1.tags = ["leak"]
                col.add_note(n1, did)
                col._backend.add_question_item(speedrun_pb2.QuestionItem(
                    card_id=n1.cards()[0].id, topic="leak", provenance=0,
                    payload=json.dumps({"stem": "the peptide bond is an amide bond"})))
                # A reworded item on the same card -> must NOT flag.
                col._backend.add_question_item(speedrun_pb2.QuestionItem(
                    card_id=n1.cards()[0].id, topic="leak", provenance=0,
                    payload=json.dumps({"stem":
                        "Which functional group links adjacent amino acids in a protein?"})))
                rep = col._backend.get_leakage_report()
                check("engine flags the planted verbatim item", rep.flagged == 1)
                check("engine leaves the reworded item clean",
                      rep.total_items == 2 and not rep.clean)
                engine = {"ran": True, "total_items": rep.total_items,
                          "flagged": rep.flagged, "clean": rep.clean}
            finally:
                col.close()
    results["engine"] = engine
    results["pass"] = True
    return results


# --- orchestration ----------------------------------------------------------


def scan(paths: list[str], *, use_engine: bool) -> dict:
    packs = [load_pack(p) for p in paths]
    corpus: list[Item] = [it for _, items in packs for it in items]

    near = run_neardup_layer(corpus)
    engine = run_engine_layer(packs) if use_engine else {"available": False,
                                                          "reason": "disabled"}

    verbatim_flagged = len(near["held_out_vs_study_verbatim"])
    if engine.get("available"):
        verbatim_flagged += sum(p["flagged"] for p in engine["packs"].values())
    study_neardup_flagged = len(near["held_out_vs_study_neardup"])
    gold_gold_flagged = len(near["gold_vs_gold"])
    clean = (verbatim_flagged == 0 and study_neardup_flagged == 0
             and gold_gold_flagged == 0)

    per_pack = []
    for meta, items in packs:
        eng = engine.get("packs", {}).get(meta["label"]) if engine.get("available") else None
        per_pack.append({
            "pack": meta["label"],
            "name": meta["name"],
            "role": meta["role"],
            "items": meta["items"],
            "engine_verbatim_flagged": (eng or {}).get("flagged"),
            "engine_linked_study_cards": (eng or {}).get("linked_study_cards"),
        })

    return {
        "thresholds": {
            "min_stem_len": MIN_STEM_LEN,
            "word_jaccard": WORD_JACCARD_THRESHOLD,
            "char_jaccard": CHAR_JACCARD_THRESHOLD,
            "char_ngram_n": CHAR_NGRAM_N,
            "near_dup_rule": "word_jaccard >= 0.80 OR char_jaccard >= 0.80",
        },
        "packs": per_pack,
        "corpus_items": len(corpus),
        "engine_layer": engine,
        "near_dup_layer": near,
        "leakage_verdict": {
            "held_out_vs_study_verbatim": verbatim_flagged,
            "held_out_vs_study_neardup": study_neardup_flagged,
            "gold_vs_gold": gold_gold_flagged,
            "clean": clean,
        },
    }


def _banner(summary: dict, st: dict) -> str:
    v = summary["leakage_verdict"]
    audit = summary["near_dup_layer"]["audit"]
    eng = summary["engine_layer"]
    lines = ["", "=== Speedrun s7e leakage check ==="]
    lines.append(f"self-test: {'PASS' if st.get('pass') else 'FAIL'} "
                 f"({sum(c['pass'] for c in st['checks'])}/{len(st['checks'])} checks"
                 f"{', engine detector fired' if st.get('engine', {}).get('ran') else ''})")
    if eng.get("available"):
        lines.append("engine verbatim layer: RAN (get_leakage_report RPC)")
    else:
        lines.append(f"engine verbatim layer: SKIPPED ({eng.get('reason')})")
    lines.append("")
    lines.append(f"{'pack':<16}{'items':>7}{'role':>18}  engine verbatim-flagged")
    for p in summary["packs"]:
        ev = p["engine_verbatim_flagged"]
        ev = "n/a" if ev is None else str(ev)
        lines.append(f"{p['pack']:<16}{p['items']:>7}{p['role']:>18}  {ev}")
    lines.append("")
    lines.append("s7e leakage conditions (drive the verdict):")
    lines.append(f"  L1 held-out stem is a VERBATIM copy of its study card : {v['held_out_vs_study_verbatim']}")
    lines.append(f"  L2 held-out stem is a NEAR copy of its study card     : {v['held_out_vs_study_neardup']}")
    lines.append(f"  L3 gold item duplicates another gold item             : {v['gold_vs_gold']}")
    lines.append(f"  => VERDICT: {'CLEAN' if v['clean'] else 'LEAKAGE FOUND'}")
    lines.append("")
    ipr = audit["intra_pack_redundancy"]
    cpr = audit["cross_pack_reuse"]
    lines.append("data-hygiene audit (NOT s7e leakage; informational):")
    lines.append(f"  intra-pack redundancy pairs : {ipr['total']} "
                 f"(near-exact {ipr['near_exact']}, structural twins {ipr['structural_twins']}) "
                 f"by pack {ipr['by_pack']}")
    lines.append(f"  cross-pack scenario reuse   : {cpr['total']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Speedrun s7e leakage check")
    ap.add_argument("packs", nargs="*", help="pack JSON files (default: the 3 real packs)")
    ap.add_argument("--no-engine", action="store_true",
                    help="skip the engine layer even if the pylib bridge is available")
    ap.add_argument("--quiet", action="store_true",
                    help="print only the JSON summary (no human banner on stderr)")
    args = ap.parse_args()

    use_engine = not args.no_engine

    # Always self-test the detectors first: a CLEAN verdict is only meaningful
    # if we have just watched the detectors fire on a planted duplicate.
    st = self_test(use_engine=use_engine)

    paths = args.packs or DEFAULT_PACKS
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        print(f"error: pack(s) not found: {missing}", file=sys.stderr)
        return 3

    summary = scan(paths, use_engine=use_engine)
    summary["self_test"] = st

    print(json.dumps(summary, indent=2))
    if not args.quiet:
        print(_banner(summary, st), file=sys.stderr)

    if not st.get("pass"):
        return 2
    return 0 if summary["leakage_verdict"]["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
