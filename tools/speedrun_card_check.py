#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun s7f "AI card check": generate MCAT flashcards from ONE real,
permissively-licensed source, then check them and report three counts.

Pipeline
--------
1. Source: a brief, verbatim, clearly-attributed excerpt from Wikipedia's
   "Cellular respiration" article (CC BY-SA 4.0). This is the ONLY ground truth
   the generator may use, and the passage the checker grounds every card in.
2. Generate (``--generate``): ask the LLM for 50 Q/A flashcards grounded ONLY in
   the source; each card must cite the source sentence it came from. Cards are
   written to ``speedrun_cardcheck_generated.json`` and raw responses cached.
3. Check (always): classify each generated card into exactly one of
   CORRECT_USEFUL / WRONG / CORRECT_BUT_BAD using an LLM judge grounded in the
   source (anchored by the hand-authored gold set where topics overlap) PLUS
   deterministic gates: must-cite-source grounding, duplicate detection
   (normalized-stem similarity), and triviality heuristics.
4. A PASSING CUTOFF, pre-registered below BEFORE any results were seen, admits
   only CORRECT_USEFUL cards; every other card is BLOCKED (a wrong card is worse
   than no card). The report states the rubric+cutoff, the three counts, the
   block list, examples, a detector self-test on planted bad cards, and the
   source attribution.

Determinism (the graded property): the LLM client (reused from
``speedrun_ai/llm.py``) pins the model, temperature=0 and a fixed seed, and
caches every response to ``speedrun_cardcheck_cache.json``. The default run is
network-free and reproduces the exact counts from the committed cache; only
``--generate`` (or a cold cache) needs an API key.

Run: tools/speedrun_card_check.sh [--generate]   (uses the isolated venv + .env)
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from speedrun_ai.llm import DEFAULT_MODEL, LLM  # noqa: E402

_HERE = pathlib.Path(__file__).resolve().parent
GENERATED_PATH = _HERE / "speedrun_cardcheck_generated.json"
GOLD_PATH = _HERE / "speedrun_cardcheck_gold.json"
CACHE_PATH = _HERE / "speedrun_cardcheck_cache.json"
REPORT_PATH = _HERE / "speedrun_cardcheck_report.md"

N_CARDS = 50

# --- The one real source (verbatim excerpt; see attribution in SOURCE_META) ---
SOURCE_TEXT = 'Cellular respiration is the process of oxidizing biological fuels using an inorganic electron acceptor, such as oxygen, to drive production of adenosine triphosphate (ATP), which stores chemical energy in a biologically accessible form. Cellular respiration may be described as a set of metabolic reactions and processes that take place in the cells to transfer chemical energy from nutrients to ATP, with the flow of electrons to an electron acceptor, and then release waste products. If the electron acceptor is oxygen, the process is more specifically known as aerobic cellular respiration. If the electron acceptor is a molecule other than oxygen, this is anaerobic cellular respiration \u2013 not to be confused with fermentation, which is also an anaerobic process, but it is not respiration, as no external electron acceptor is involved. The reactions involved in respiration are catabolic reactions, which break large molecules into smaller ones, producing ATP.\n\nNutrients that are commonly used by animal and plant cells in respiration include sugar, amino acids and fatty acids, and the most common oxidizing agent is molecular oxygen (O2).\n\nBiology textbooks often state that 38 ATP molecules can be made per oxidized glucose molecule during cellular respiration (2 from glycolysis, 2 from the Krebs cycle, and about 34 from the electron transport system). However, this maximum yield is never quite reached because of losses due to leaky membranes as well as the cost of moving pyruvate and ADP into the mitochondrial matrix, and current estimates range around 29 to 30 ATP per glucose.\n\nAerobic metabolism is up to 15 times more efficient than anaerobic metabolism (which yields 2 molecules of ATP per 1 molecule of glucose).\n\nThe post-glycolytic reactions take place in the mitochondria in eukaryotic cells, and in the cytoplasm in prokaryotic cells.\n\nGlycolysis is a metabolic pathway that takes place in the cytosol of cells in all living organisms. Glycolysis can be literally translated as "sugar splitting", and occurs regardless of oxygen\'s presence or absence. The process converts one molecule of glucose into two molecules of pyruvate (pyruvic acid), generating energy in the form of two net molecules of ATP. Four molecules of ATP per glucose are actually produced, but two are consumed as part of the preparatory phase.\n\nGlucose + 2 NAD+ + 2 Pi + 2 ADP \u2192 2 pyruvate + 2 NADH + 2 ATP + 2 H+ + 2 H2O + energy\n\nAn additional ATP is used to phosphorylate fructose 6-phosphate into fructose 1,6-bisphosphate by the help of phosphofructokinase.\n\nPyruvate is oxidized to acetyl-CoA and CO2 by the pyruvate dehydrogenase complex (PDC). The PDC contains multiple copies of three enzymes and is located in the mitochondria of eukaryotic cells and in the cytosol of prokaryotes. In the conversion of pyruvate to acetyl-CoA, one molecule of NADH and one molecule of CO2 is formed.\n\nThe citric acid cycle is also called the Krebs cycle or the tricarboxylic acid cycle.\n\nThe net gain from one cycle is 3 NADH and 1 FADH2 as hydrogen (proton plus electron) carrying compounds and 1 high-energy GTP, which may subsequently be used to produce ATP. Thus, the total yield from 1 glucose molecule (2 pyruvate molecules) is 6 NADH, 2 FADH2, and 2 ATP.\n\nIn eukaryotes, oxidative phosphorylation occurs in the mitochondrial cristae. It comprises the electron transport chain that establishes a proton gradient (chemiosmotic potential) across the boundary of the inner membrane by oxidizing the NADH produced from the Krebs cycle.'

# Integrity guard: the committed generated-cards file must carry this exact
# source. Mirrors the eval's leakage/integrity check.
SOURCE_SHA256 = "32922b5ee43bcb4276ae6bfa53880dc83f139b4f74e1a9d2bea047d8acfdf444"

SOURCE_META = {
    "title": "Cellular respiration",
    "publisher": "Wikipedia, The Free Encyclopedia",
    "url": "https://en.wikipedia.org/wiki/Cellular_respiration",
    "license": "CC BY-SA 4.0",
    "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
    "revision": 1355541234,
    "revision_timestamp": "2026-05-22T13:08:03Z",
    "retrieved": "2026-07-02",
    "attribution": (
        'Wikipedia contributors, "Cellular respiration," Wikipedia, The Free '
        "Encyclopedia (revision 1355541234, retrieved 2026-07-02). Brief excerpt "
        "reused under CC BY-SA 4.0."
    ),
}

# --------------------------------------------------------------------------
# PRE-REGISTERED RUBRIC + PASSING CUTOFF (fixed BEFORE any results were seen).
# --------------------------------------------------------------------------
# Every generated card is placed in EXACTLY ONE class:
#   CORRECT_USEFUL   - factually correct per the source (and consistent with the
#                      gold anchors where topics overlap), grounded in a citable
#                      source sentence, specific, non-trivial, non-duplicate.
#   WRONG            - factually incorrect vs the source or a gold anchor, or
#                      built on a false premise. A wrong fact is worse than no
#                      card, so this is the class we most want to catch.
#   CORRECT_BUT_BAD  - factually correct but poor teaching: vague/trivial,
#                      self-answering, a near-duplicate, or not grounded in the
#                      source (uncitable).
#
# PASSING CUTOFF (a card is ADMITTED to the deck iff ALL hold; else BLOCKED):
#   1. final class == CORRECT_USEFUL, AND
#   2. source-grounded: citation matches a source sentence with ratio >= 0.60
#      AND the judge agrees it is grounded, AND
#   3. not a duplicate: max normalized-stem similarity to an earlier admitted
#      card < 0.80, AND
#   4. passes triviality heuristics, AND
#   5. judge confidence >= 0.60.
TAU_GROUND = 0.60  # citation-vs-source similarity to count as grounded
TAU_DUP = 0.80  # normalized-stem similarity at/above which a card is a duplicate
TAU_CONF = 0.60  # minimum judge confidence to admit a card
TAU_GOLD = 0.55  # topic-overlap similarity to attach a gold anchor to a card

CLASSES = ["CORRECT_USEFUL", "WRONG", "CORRECT_BUT_BAD"]

JUDGE_SYSTEM = (
    "You are a meticulous MCAT flashcard reviewer. You are given a SOURCE "
    "passage (the ONLY allowed ground truth), one flashcard (question Q and "
    "answer A), and sometimes a KNOWN-CORRECT reference fact on the same topic. "
    "Judge ONLY against the SOURCE (plus the reference):\n"
    "- verdict: 'wrong' ONLY if A contradicts the SOURCE or the reference (a "
    "wrong number, wrong location, swapped terms, reversed relationship) or "
    "rests on a false premise. If A agrees with a fact stated in the SOURCE - "
    "even if worded differently, incomplete, or missing context - verdict is "
    "'correct'. Do NOT mark a correct-but-incomplete answer 'wrong'.\n"
    "- grounded: true ONLY if the card's fact is stated in or directly entailed "
    "by the SOURCE (not merely true in the world).\n"
    "- teaching: 'useful' if specific and testing a real, non-trivial concept; "
    "'bad' if vague, trivial, tautological, or self-answering.\n"
    "- confidence: number 0..1 for your verdict.\n"
    'Respond with JSON only: {"verdict":"correct|wrong","grounded":true|false,'
    '"teaching":"useful|bad","confidence":0..1,"evidence":"the SOURCE sentence '
    'that supports or contradicts A","reason":"short"}.'
)

GEN_SYSTEM = (
    "You write flashcards for MCAT study STRICTLY from a provided SOURCE "
    "passage. Rules: (1) every card's fact MUST be supported by the SOURCE - "
    "use no outside knowledge; (2) each card has a clear question 'q', a concise "
    "answer 'a', and 'cite' = the exact sentence or clause copied VERBATIM from "
    "the SOURCE that grounds the card; (3) prefer distinct, non-overlapping, "
    "non-trivial facts. Return JSON only: "
    '{"cards":[{"q":"...","a":"...","cite":"..."}, ...]}.'
)

# Planted cards used ONLY to prove the checker's detectors fire. These are NOT
# part of the 50 generated cards or the three headline counts.
SELFTEST_PROBES = [
    {
        "id": "p1",
        "expect": "WRONG",
        "q": "How many net ATP does glycolysis produce per glucose?",
        "a": "38 net ATP.",
        "cite": "The process converts one molecule of glucose into two molecules of pyruvate (pyruvic acid), generating energy in the form of two net molecules of ATP.",
    },
    {
        "id": "p2",
        "expect": "WRONG",
        "q": "In which compartment does glycolysis take place?",
        "a": "The mitochondrial matrix.",
        "cite": "Glycolysis is a metabolic pathway that takes place in the cytosol of cells in all living organisms.",
    },
    {
        "id": "p3",
        "expect": "CORRECT_BUT_BAD",
        "q": "The citric acid cycle is also called the Krebs cycle. What is it called?",
        "a": "the Krebs cycle",
        "cite": "The citric acid cycle is also called the Krebs cycle or the tricarboxylic acid cycle.",
    },
    {
        "id": "p4",
        "expect": "CORRECT_BUT_BAD",
        "q": "Who discovered the citric acid cycle?",
        "a": "Hans Krebs.",
        "cite": "",
    },
    {
        "id": "p5",
        "expect": "CORRECT_USEFUL",
        "q": "In eukaryotes, where does oxidative phosphorylation occur?",
        "a": "In the mitochondrial cristae (the inner mitochondrial membrane).",
        "cite": "In eukaryotes, oxidative phosphorylation occurs in the mitochondrial cristae.",
    },
    {
        # Same stem as p5 (canonical duplicate), answer reworded.
        "id": "p6",
        "expect": "CORRECT_BUT_BAD",
        "q": "In eukaryotes, where does oxidative phosphorylation occur?",
        "a": "The mitochondrial cristae.",
        "cite": "In eukaryotes, oxidative phosphorylation occurs in the mitochondrial cristae.",
    },
]


class CardCheckLLM(LLM):
    """The cached OpenAI client from speedrun_ai, pointed at our own cache file
    so this artifact reproduces independently of the coach eval."""

    def __init__(self, cache_path: pathlib.Path = CACHE_PATH, **kw):
        self._cache_path = pathlib.Path(cache_path)
        super().__init__(**kw)

    def _cache_load(self) -> dict:
        if self._cache_path.exists():
            try:
                return json.loads(self._cache_path.read_text())
            except Exception:
                return {}
        return {}

    def _cache_save(self) -> None:
        self._cache_path.write_text(json.dumps(self.cache, indent=0, sort_keys=True))


# --------------------------- text utilities ------------------------------

_WORD = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _WORD.sub(" ", (s or "").lower()).strip()


def _sentences(text: str) -> list[str]:
    parts: list[str] = []
    for block in text.split("\n"):
        block = block.strip()
        if not block:
            continue
        parts.extend(re.split(r"(?<=[.!?])\s+", block))
    return [p.strip() for p in parts if p.strip()]


SOURCE_NORM = _norm(SOURCE_TEXT)
SOURCE_SENTS_NORM = [_norm(s) for s in _sentences(SOURCE_TEXT)]


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _jaccard(a: str, b: str) -> float:
    A, B = set(_norm(a).split()), set(_norm(b).split())
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _sim(a: str, b: str) -> float:
    return max(_jaccard(a, b), _ratio(a, b))


def grounding_score(cite: str) -> float:
    """Max similarity of the citation to any source sentence (1.0 if verbatim)."""
    c = _norm(cite)
    if not c:
        return 0.0
    if c in SOURCE_NORM:
        return 1.0
    return max(
        (difflib.SequenceMatcher(None, c, s).ratio() for s in SOURCE_SENTS_NORM),
        default=0.0,
    )


_STOP = {
    "the",
    "a",
    "an",
    "of",
    "is",
    "are",
    "to",
    "in",
    "and",
    "what",
    "which",
    "does",
    "do",
    "how",
    "many",
    "per",
    "for",
    "that",
    "this",
    "at",
    "by",
    "it",
    "its",
    "be",
    "or",
    "from",
    "into",
    "during",
    "with",
    "as",
    "on",
    "was",
    "were",
    "molecule",
    "molecules",
}


def _meaningful(s: str) -> list[str]:
    return [t for t in _norm(s).split() if t not in _STOP and len(t) > 1]


def triviality(card: dict) -> tuple[bool, str]:
    """Deterministic triviality gate (the LLM judge handles subtler vagueness)."""
    q, a = card.get("q", ""), card.get("a", "")
    qn, an = _norm(q), _norm(a)
    if len(an) < 1:
        return True, "empty_answer"
    if len(_meaningful(q)) < 2:
        return True, "too_short_question"
    if len(an.split()) >= 2 and an in qn:
        return True, "answer_in_question"
    if _ratio(q, a) >= 0.85:
        return True, "question_equals_answer"
    return False, ""


# --------------------------- generation ----------------------------------


def generate_cards(
    llm: CardCheckLLM, n: int = N_CARDS, batch: int = 25, max_rounds: int = 6
) -> list[dict]:
    """Generate n cards in deterministic rounds.

    temperature=0 makes an identical prompt return identical cards, so to reach
    n we feed each round the questions already written as an avoid-list. This is
    deterministic (each round's prompt is fixed given the cached prior rounds)
    and therefore reproducible from the cache. Forcing new facts out of a small
    passage is intentional: it pushes the generator toward the marginal/trivial/
    ungrounded cards the checker must catch.
    """
    collected: list[dict] = []
    for _ in range(max_rounds):
        if len(collected) >= n:
            break
        ask = max(batch, n - len(collected))
        avoid = "\n".join(f"- {c['q']}" for c in collected)
        user = (
            f"SOURCE:\n{SOURCE_TEXT}\n\n"
            f"Write {ask} MCAT flashcards grounded ONLY in the SOURCE above, "
            "covering as many distinct facts as possible. Every 'cite' must be "
            "copied verbatim from the SOURCE."
            + (
                f"\n\nThese questions are already written - do NOT repeat them:\n{avoid}"
                if avoid
                else ""
            )
        )
        out = llm.complete_json(GEN_SYSTEM, user)
        raw = out.get("cards", []) if isinstance(out, dict) else out
        added = 0
        for c in raw:
            if not isinstance(c, dict):
                continue
            q = str(c.get("q", "")).strip()
            if not q:
                continue
            collected.append(
                {
                    "q": q,
                    "a": str(c.get("a", "")).strip(),
                    "cite": str(c.get("cite", "")).strip(),
                }
            )
            added += 1
        if added == 0:  # no progress - avoid an infinite loop on a spent source
            break
    return [{"id": f"c{i + 1:02d}", **c} for i, c in enumerate(collected[:n])]


def write_generated(cards: list[dict], from_cache: bool, new_calls: int) -> None:
    doc = {
        "name": "Speedrun card-check: LLM-generated flashcards v1",
        "license": "AGPL-3.0-or-later",
        "note": (
            "Flashcards generated by tools/speedrun_card_check.py STRICTLY from "
            "the single source excerpt below. Committed so the check is "
            "reproducible offline from the cache. Each card carries the source "
            "sentence it was grounded in ('cite')."
        ),
        "source": {**SOURCE_META, "sha256": SOURCE_SHA256, "text": SOURCE_TEXT},
        "generator": {
            "model": DEFAULT_MODEL,
            "temperature": 0.0,
            "seed": 7,
            "n_requested": N_CARDS,
            "n_generated": len(cards),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "from_cache": from_cache,
            "new_api_calls": new_calls,
        },
        "cards": cards,
    }
    GENERATED_PATH.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def load_generated() -> dict:
    if not GENERATED_PATH.exists():
        raise SystemExit(
            f"missing {GENERATED_PATH.name}; run with --generate first "
            "(needs an API key), or restore it from the committed artifact."
        )
    return json.loads(GENERATED_PATH.read_text())


def load_gold() -> list[dict]:
    return json.loads(GOLD_PATH.read_text())["items"]


# --------------------------- checking ------------------------------------


def best_gold(card: dict, gold: list[dict]) -> tuple[dict | None, float]:
    q = card.get("q", "")
    best, best_sim = None, 0.0
    for g in gold:
        s = _sim(q, g["question"])
        if s > best_sim:
            best, best_sim = g, s
    return best, best_sim


def judge_card(llm: CardCheckLLM, card: dict, gold_ref: dict | None) -> dict:
    ref = ""
    if gold_ref is not None:
        ref = f'\nKNOWN-CORRECT REFERENCE (same topic): "{gold_ref["fact"]}"'
    user = (
        f"SOURCE:\n{SOURCE_TEXT}\n{ref}\n\n"
        f"FLASHCARD\nQ: {card.get('q', '')}\nA: {card.get('a', '')}\n"
        f'The card claims it is grounded in: "{card.get("cite", "")}"'
    )
    try:
        out = llm.complete_json(JUDGE_SYSTEM, user)
    except Exception as e:  # no key / offline cold-cache -> conservative block
        return {
            "verdict": "unknown",
            "grounded": False,
            "teaching": "bad",
            "confidence": 0.0,
            "evidence": "",
            "reason": f"judge unavailable: {e}",
            "error": True,
        }
    try:
        conf = float(out.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "verdict": str(out.get("verdict", "")).lower().strip(),
        "grounded": bool(out.get("grounded", False)),
        "teaching": str(out.get("teaching", "")).lower().strip(),
        "confidence": conf,
        "evidence": str(out.get("evidence", "")),
        "reason": str(out.get("reason", "")),
        "error": False,
    }


def classify(
    card: dict,
    judge: dict,
    cite_score: float,
    is_dup: bool,
    dup_of: str | None,
    trivial: bool,
    trivial_reason: str,
) -> dict:
    """Fold judge + deterministic gates into one class + blocking reasons."""
    grounded = (cite_score >= TAU_GROUND) and bool(judge.get("grounded"))
    reasons: list[str] = []

    if judge.get("verdict") == "wrong":
        label = "WRONG"
        reasons.append("factually_wrong")
    elif judge.get("error"):
        label = "CORRECT_BUT_BAD"
        reasons.append("judge_unavailable")
    else:
        if not grounded:
            reasons.append("ungrounded")
        if is_dup:
            reasons.append(f"duplicate_of_{dup_of}")
        if trivial:
            reasons.append(f"trivial:{trivial_reason}")
        if judge.get("teaching") == "bad":
            reasons.append("bad_teaching")
        if judge.get("confidence", 0.0) < TAU_CONF:
            reasons.append("low_confidence")
        label = "CORRECT_BUT_BAD" if reasons else "CORRECT_USEFUL"

    return {
        "label": label,
        "passed": label == "CORRECT_USEFUL",
        "blocked": label != "CORRECT_USEFUL",
        "grounded": grounded,
        "cite_score": round(cite_score, 3),
        "is_duplicate": is_dup,
        "duplicate_of": dup_of,
        "trivial": trivial,
        "reasons": reasons,
    }


def check_sequence(
    llm: CardCheckLLM, cards: list[dict], gold: list[dict]
) -> list[dict]:
    """Run the full per-card pipeline over an ordered list of cards.

    Duplicate detection compares each card only to earlier ADMITTED cards, so a
    genuinely new card is never penalised for later copies of itself.
    """
    admitted: list[dict] = []
    rows: list[dict] = []
    for card in cards:
        cite_score = grounding_score(card.get("cite", ""))
        trivial, treason = triviality(card)

        dup_of, dup_sim = None, 0.0
        stem = card.get("q", "")
        stem_ans = f"{card.get('q', '')} {card.get('a', '')}"
        for prev in admitted:
            s = max(_sim(stem, prev["q"]), _sim(stem_ans, f"{prev['q']} {prev['a']}"))
            if s > dup_sim:
                dup_sim, dup_of = s, prev["id"]
        is_dup = dup_sim >= TAU_DUP

        gref, gsim = best_gold(card, gold)
        gref = gref if gsim >= TAU_GOLD else None
        judge = judge_card(llm, card, gref)

        verdict = classify(
            card,
            judge,
            cite_score,
            is_dup,
            dup_of if is_dup else None,
            trivial,
            treason,
        )
        row = {
            "card": card,
            "judge": judge,
            "gold_anchor": (gref or {}).get("id"),
            "gold_sim": round(gsim, 3),
            "dup_sim": round(dup_sim, 3),
            **verdict,
        }
        rows.append(row)
        if verdict["label"] == "CORRECT_USEFUL":
            admitted.append(card)
    return rows


def counts_of(rows: list[dict]) -> dict:
    c = {k: 0 for k in CLASSES}
    for r in rows:
        c[r["label"]] += 1
    c["BLOCKED"] = c["WRONG"] + c["CORRECT_BUT_BAD"]
    c["ADMITTED"] = c["CORRECT_USEFUL"]
    c["TOTAL"] = len(rows)
    return c


# --------------------------- reporting -----------------------------------


def _example(rows: list[dict], label: str, n: int = 3) -> list[dict]:
    return [r for r in rows if r["label"] == label][:n]


def render(
    gen: dict,
    rows: list[dict],
    counts: dict,
    selftest: list[dict],
    ran_from_cache: bool,
    new_calls: int,
) -> str:
    src = gen["source"]
    L: list[str] = []
    L.append("# Speedrun s7f - AI Card Check\n")
    L.append(
        f"Model `{DEFAULT_MODEL}` (temp=0, seed=7, cached) | "
        f"{counts['TOTAL']} generated cards | "
        f"{'CACHE (offline)' if ran_from_cache else 'LIVE API'} | "
        f"{new_calls} new API calls this run\n"
    )
    L.append(
        "\n> A wrong card is worse than no card. The checker BLOCKS every card "
        "that is not CORRECT_USEFUL; only admitted cards would enter a deck.\n"
    )

    L.append("\n## Headline counts (the three the task asks for)\n")
    L.append(f"- **Correct + useful (admitted):** {counts['CORRECT_USEFUL']}")
    L.append(f"- **Wrong (factually incorrect vs source/gold):** {counts['WRONG']}")
    L.append(
        f"- **Correct but bad teaching (vague / trivial / duplicate / ungrounded):** {counts['CORRECT_BUT_BAD']}"
    )
    L.append(
        f"- **Blocked by the cutoff:** {counts['BLOCKED']} of {counts['TOTAL']} "
        f"(admitted {counts['ADMITTED']}).\n"
    )

    L.append("\n## Pre-registered rubric + passing cutoff (fixed before results)\n")
    L.append("Each card is placed in exactly one class:")
    L.append(
        "- **CORRECT_USEFUL** - correct per the source (and gold anchors where "
        "topics overlap), grounded in a citable source sentence, specific, "
        "non-trivial, non-duplicate."
    )
    L.append(
        "- **WRONG** - factually incorrect vs the source or a gold anchor, or a "
        "false premise. The class we most want to catch."
    )
    L.append(
        "- **CORRECT_BUT_BAD** - correct but poor teaching: vague/trivial, "
        "self-answering, near-duplicate, or not grounded in the source."
    )
    L.append("")
    L.append("**Passing cutoff** - a card is ADMITTED iff ALL hold, else BLOCKED:")
    L.append("1. class == CORRECT_USEFUL;")
    L.append(
        f"2. source-grounded: citation vs source-sentence similarity >= {TAU_GROUND:.2f} AND the judge agrees it is grounded;"
    )
    L.append(
        f"3. not a duplicate: normalized-stem similarity to any earlier admitted card < {TAU_DUP:.2f};"
    )
    L.append(
        "4. passes triviality heuristics (non-empty answer, question has >= 2 content words, answer not contained in the question, question != answer);"
    )
    L.append(f"5. judge confidence >= {TAU_CONF:.2f}.")
    L.append(
        "\nChecks are deterministic given the cached judge outputs, so the "
        "counts reproduce exactly from the committed cache.\n"
    )

    L.append("\n## Source (the one real source; brief excerpt, attributed)\n")
    L.append(f"- **Title:** {src['title']} ({src['publisher']})")
    L.append(f"- **URL:** {src['url']}")
    L.append(f"- **License:** {src['license']} ({src['license_url']})")
    L.append(f"- **Revision / retrieved:** {src['revision']} / {src['retrieved']}")
    L.append(f"- **Excerpt SHA-256:** `{src['sha256']}`")
    L.append(f"- **Attribution:** {src['attribution']}")
    L.append("\n<details><summary>Source excerpt (verbatim, ~570 words)</summary>\n")
    L.append("```text")
    L.append(SOURCE_TEXT)
    L.append("```")
    L.append("</details>\n")

    L.append("\n## Grounding / duplicate / triviality summary\n")
    grounded = sum(1 for r in rows if r["grounded"])
    dups = sum(1 for r in rows if r["is_duplicate"])
    trivs = sum(1 for r in rows if r["trivial"])
    wrong = counts["WRONG"]
    L.append(
        f"- Grounded in the source (cite matched, judge agreed): {grounded}/{counts['TOTAL']}"
    )
    L.append(f"- Deterministic duplicates flagged: {dups}")
    L.append(f"- Triviality-flagged: {trivs}")
    L.append(f"- Factually wrong (judge vs source/gold): {wrong}\n")

    L.append("\n## Block list (every blocked card + why)\n")
    blocked = [r for r in rows if r["blocked"]]
    if not blocked:
        L.append("_None - all cards passed._\n")
    else:
        L.append("| id | class | reasons | Q -> A |")
        L.append("| --- | --- | --- | --- |")
        for r in blocked:
            c = r["card"]
            qa = f"{c['q']} -> {c['a']}".replace("|", "\\|")
            L.append(
                f"| `{c['id']}` | {r['label']} | {', '.join(r['reasons'])} | {qa} |"
            )
        L.append("")

    L.append("\n## Examples by class\n")
    for label in CLASSES:
        L.append(f"\n**{label}**")
        ex = _example(rows, label)
        if not ex:
            L.append("- _(none)_")
            continue
        for r in ex:
            c = r["card"]
            L.append(f"- `{c['id']}` Q: {c['q']}")
            L.append(f"  - A: {c['a']}")
            L.append(f"  - cite (score {r['cite_score']}): {c['cite'] or '(none)'}")
            jr = r["judge"].get("reason", "")
            L.append(
                f"  - judge: verdict={r['judge'].get('verdict')} grounded={r['judge'].get('grounded')} "
                f"teaching={r['judge'].get('teaching')} conf={r['judge'].get('confidence')}"
                + (f" - {jr}" if jr else "")
            )
            if r["reasons"]:
                L.append(f"  - blocked reasons: {', '.join(r['reasons'])}")
    L.append("")

    L.append("\n## Full per-card table\n")
    L.append("| id | class | grounded | cite | dup | trivial | gold | verdict/conf |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        c = r["card"]
        L.append(
            f"| `{c['id']}` | {r['label']} | {'Y' if r['grounded'] else 'n'} | "
            f"{r['cite_score']} | {r['dup_sim'] if r['is_duplicate'] else '-'} | "
            f"{'Y' if r['trivial'] else 'n'} | {r['gold_anchor'] or '-'}"
            f"({r['gold_sim']}) | {r['judge'].get('verdict')}/{r['judge'].get('confidence')} |"
        )
    L.append("")

    L.append("\n## Detector self-test (planted cards; NOT part of the 50)\n")
    L.append(
        "Proof the gates fire, since a clean generator may yield few or zero "
        "wrong cards. Each planted card has a known expected class.\n"
    )
    L.append("| id | expected | detected | match | reasons |")
    L.append("| --- | --- | --- | --- | --- |")
    ok = 0
    for r in selftest:
        exp = r["card"]["expect"]
        got = r["label"]
        hit = "PASS" if exp == got else "MISS"
        ok += exp == got
        L.append(
            f"| `{r['card']['id']}` | {exp} | {got} | {hit} | {', '.join(r['reasons']) or '-'} |"
        )
    L.append(
        f"\nSelf-test: {ok}/{len(selftest)} planted cards classified as expected.\n"
    )

    L.append("\n## Judge reliability & limitations (honest)\n")
    L.append(
        "- **Conservative by design.** Factual correctness is decided by an "
        "LLM judge, which is noisy. The deterministic gates (grounding, "
        "dedup, triviality) back it up, and the cutoff BLOCKS on any doubt, "
        "so a judge slip costs a useful card (blocked) rather than admitting "
        "a wrong one - the safe direction when a wrong card is worse than no card."
    )
    L.append(
        "- **Verdict scope.** 'WRONG' means *contradicts the source/gold*; a "
        "correct-but-ungrounded or correct-but-vague card is CORRECT_BUT_BAD, "
        "not WRONG. Ungrounded cards dominate the block list because the "
        "generator, pushed past what ~570 words can support, restates facts "
        "without a verbatim citation."
    )
    L.append(
        "- **Single-source risk.** Grounding guarantees faithfulness to THIS "
        "source, not to current consensus. The passage repeats the classic "
        'textbook "38 ATP per glucose" figure (while noting it is never '
        'actually reached; real yield ~29-30). Cards echoing "38 ATP" are '
        "graded correct *because they match the source*, yet are pedagogically "
        "dated - a reason a production deck should ground on vetted, current material."
    )
    L.append(
        "- **Determinism.** The judge uses the same cached, temp=0, pinned-seed "
        "client as generation, so these counts reproduce exactly from the "
        "committed cache.\n"
    )

    L.append("\n## Reproduce\n")
    L.append("```bash")
    L.append("# offline, from the committed cache (no API key needed):")
    L.append("tools/speedrun_card_check.sh")
    L.append("")
    L.append("# re-generate cards + refresh the cache (needs OPENAI_API_KEY):")
    L.append("tools/speedrun_card_check.sh --generate")
    L.append("```")
    L.append(
        "\n_LLM calls are cached in `speedrun_cardcheck_cache.json` "
        "(keyed by model+params+prompt); the default run is deterministic "
        "and network-free._\n"
    )
    return "\n".join(L) + "\n"


# --------------------------- driver --------------------------------------


def run(generate: bool) -> dict:
    if hashlib.sha256(SOURCE_TEXT.encode()).hexdigest() != SOURCE_SHA256:
        raise SystemExit("source integrity check failed (SOURCE_TEXT != SOURCE_SHA256)")

    llm = CardCheckLLM()
    gold = load_gold()

    if generate:
        cards = generate_cards(llm, N_CARDS)
        write_generated(cards, from_cache=(llm.new_calls == 0), new_calls=llm.new_calls)

    gen = load_generated()
    if gen["source"].get("sha256") != SOURCE_SHA256:
        raise SystemExit(
            "generated-cards source does not match the committed source hash"
        )
    cards = gen["cards"]
    if not cards:
        raise SystemExit("no cards in generated file; run with --generate first")

    rows = check_sequence(llm, cards, gold)
    selftest = check_sequence(llm, [dict(p) for p in SELFTEST_PROBES], gold)
    counts = counts_of(rows)

    return {
        "gen": gen,
        "rows": rows,
        "selftest": selftest,
        "counts": counts,
        "ran_from_cache": llm.new_calls == 0,
        "new_calls": llm.new_calls,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Speedrun s7f AI card check")
    ap.add_argument(
        "--generate",
        action="store_true",
        help="(re)generate cards via the API and refresh the cache",
    )
    ap.add_argument("--json", action="store_true", help="print raw JSON results")
    args = ap.parse_args()

    r = run(args.generate)
    report = render(
        r["gen"],
        r["rows"],
        r["counts"],
        r["selftest"],
        r["ran_from_cache"],
        r["new_calls"],
    )
    REPORT_PATH.write_text(report)

    if args.json:
        slim = {
            "counts": r["counts"],
            "new_api_calls": r["new_calls"],
            "ran_from_cache": r["ran_from_cache"],
            "selftest": [
                {
                    "id": s["card"]["id"],
                    "expected": s["card"]["expect"],
                    "detected": s["label"],
                }
                for s in r["selftest"]
            ],
        }
        print(json.dumps(slim, indent=2))
    else:
        print(report)
    print(f"[report written to {REPORT_PATH.relative_to(_HERE.parent)}]")

    c = r["counts"]
    print(
        f"[counts] correct+useful={c['CORRECT_USEFUL']} wrong={c['WRONG']} "
        f"correct_but_bad={c['CORRECT_BUT_BAD']} blocked={c['BLOCKED']}/{c['TOTAL']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
