#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Study-feature ablation for the points-at-stake + interleave queue (AI-off).

The Speedrun spec (section 8) calls for a three-arm study-feature ablation:
compare, on the *same* cards and at an *equal study budget*, three builds that
differ only in how the review queue is ordered - each arm adding one study
feature on top of the previous:

    1. Plain Anki             - both features OFF: the unmodified default review
                                order, with no Speedrun weakness evidence at all.
    2. +points-at-stake       - points-at-stake ON (weak/high-value cards first),
                                driven by the recorded diagnostic (weakness)
                                evidence; interleave OFF.
    3. +points +interleave    - points-at-stake ON *and* topic interleave ON, so
                                the same value ranking is then round-robined
                                across confusable sibling topics within each
                                parent concept.

We cannot run real learners in a script, so this is an explicit, honest
*simulated-learner* experiment ("simulation": true in the output). Every card is
given a hidden "true mastery"; some topics are weak. The SAME miss evidence is
recorded for arms 2 and 3, so the arms differ only in queue *ordering*. We then
"study" the first K cards of each arm's order (equal budget) and score how much
exam value that surfaces. See tools/speedrun_ablation_report.md for the
pre-registered metric and the results.

How the engine actually ranks: with points-at-stake ON the queue stable-sorts
due reviews by (1 + per-card weakness), where weakness is the recorded miss
rate; with it OFF the points-at-stake path is skipped and Anki's default order
is used unchanged. With interleave ON, confusable sibling topics (children of a
shared `concept::topic` parent, resolved from each note's tag via a registered
topic map) are round-robined within their concept block, so arm 3 genuinely
diverges from arm 2. The live queue weights every topic equally (it ranks by
weakness, not yield), so the "yield weight" below lives only in our scoring
metric.

Usage:
    python tools/speedrun_ablation.py                  # self-test + experiment
    python tools/speedrun_ablation.py --experiment [n] # 3-arm experiment (JSON)
    python tools/speedrun_ablation.py collection.anki2 [deck_id]

Run via the wrapper so the built pylib bridge is on the path:
    ./tools/speedrun_ablation.sh [args]
"""

from __future__ import annotations

import json
import os
import random
import statistics
import sys
import tempfile

from anki import speedrun_pb2
from anki.collection import Collection

_FLAG = "speedrunPointsAtStake"
_INTERLEAVE_FLAG = "speedrunInterleaveTopics"

# card type / queue values for a mature review card
_CARD_TYPE_REVIEW = 2
_QUEUE_REVIEW = 2


def _order(
    col: Collection, deck_id: int, *, points_at_stake: bool, interleave: bool
) -> list[int]:
    """Return the deck's review-card study order under the two study-feature
    toggles. Both flags live in collection config (not the RPC message), so they
    are set before GetReviewOrder, matching the queue builder's gates."""
    col.set_config(_FLAG, points_at_stake)
    col.set_config(_INTERLEAVE_FLAG, interleave)
    return list(col._backend.get_review_order(deck_id=deck_id))


def ablation(col: Collection, deck_id: int) -> dict:
    off = _order(col, deck_id, points_at_stake=False, interleave=False)
    on = _order(col, deck_id, points_at_stake=True, interleave=False)
    positions_changed = sum(1 for a, b in zip(off, on) if a != b)
    return {
        "deck_id": deck_id,
        "order_off": off,
        "order_on": on,
        "positions_changed": positions_changed,
        "same_cards": sorted(off) == sorted(on),
    }


def _make_review_card(
    col: Collection, did: int, front: str, due: int = 0, tag: str | None = None
):
    """Add a single due review card and return its Card (callers read both its
    id and note id). `due` is in days; keep it <= 0 so the card is actually due.
    An optional `tag` (a hierarchical `concept::name` topic key) is written to
    the note so the interleave feature can group confusable siblings."""
    model = col.models.by_name("Basic")
    note = col.new_note(model)
    note["Front"] = front
    if tag is not None:
        note.tags = [tag]
    col.add_note(note, did)
    card = note.cards()[0]
    card.type = _CARD_TYPE_REVIEW
    card.queue = _QUEUE_REVIEW
    card.ivl = 10
    card.due = due
    col.update_card(card)
    return card


def _record_attempt(col: Collection, cid: int, nid: int, correct: bool) -> None:
    """Record one SRS attempt (hit or miss) as weakness evidence for a card."""
    col._backend.record_attempt(
        speedrun_pb2.RecordAttemptRequest(
            card_id=cid,
            note_id=nid,
            question_type=0,
            correct=correct,
            signals=speedrun_pb2.ClassifyAttemptRequest(
                correct=correct, recall_failed=not correct, question_type=0
            ),
        )
    )


def _record_miss(col: Collection, cid: int) -> None:
    _record_attempt(col, cid, 1, correct=False)


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
        off = _order(col, did, points_at_stake=False, interleave=False)
        weak_id = off[-1]
        _record_miss(col, weak_id)
        _record_miss(col, weak_id)

        on = _order(col, did, points_at_stake=True, interleave=False)
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


# --- Three-arm study-feature experiment (simulated learner) ------------------

# (topic, yield weight, base true-mastery). Weight is deliberately INDEPENDENT
# of mastery - some weak topics are high-yield, some low - so the ranking is not
# handed a weight-aligned signal. The engine only ever sees per-card weakness
# (miss rate); topic weight lives solely in our scoring metric to model yield.
_TOPICS = [
    ("amino_acids", 2.0, 0.30),  # weak,   high-yield
    ("lipids", 0.7, 0.35),  # weak,   low-yield
    ("kinetics", 1.5, 0.55),  # medium, high-yield
    ("sociology", 0.8, 0.60),  # medium, low-yield
    ("cell_bio", 1.8, 0.82),  # strong, high-yield
    ("genetics", 0.9, 0.85),  # strong, low-yield
]

# Parent concept for each topic, forming a two-level hierarchy (`CONCEPT::topic`)
# the interleave feature can recognise. Each parent groups >=2 sibling topics so
# every concept block spans confusable siblings the round-robin can interleave;
# without this the interleave arm would be a no-op and collapse onto the
# points-at-stake arm. BIOCHEM = biomolecule/biology topics, PHYSSOC = the
# physical-science + behavioural-science remainder.
_TOPIC_PARENTS = {
    "amino_acids": "BIOCHEM",
    "lipids": "BIOCHEM",
    "cell_bio": "BIOCHEM",
    "kinetics": "PHYSSOC",
    "sociology": "PHYSSOC",
    "genetics": "PHYSSOC",
}


def _topic_key(name: str) -> str:
    """Hierarchical `concept::topic` key used as the note tag and topic-map key."""
    return f"{_TOPIC_PARENTS[name]}::{name}"


_CARDS_PER_TOPIC = 8
_ATTEMPTS_PER_CARD = 6  # coarse weakness evidence (miss rate in sixths)
_MASTERY_NOISE = 0.22  # per-card jitter around the topic's base mastery
_WEAK_THRESHOLD = 0.5  # a card is "weak" when its true mastery is below this
_BUDGET_FRACTION = 1 / 3  # equal study budget: only a third of the due pile
_SENSITIVITY_FRACTIONS = [0.25, 1 / 3, 0.5, 0.75, 1.0]
_DEFAULT_SEEDS = list(range(12))

_ARM_KEYS = ("plain_anki", "points_at_stake", "points_plus_interleave")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _register_topic_map(col: Collection) -> None:
    """Register the hierarchical `concept::topic` map once, so the interleave
    feature can resolve each card's topic (from its note tag) and the parent
    concept it shares with confusable siblings. Mirrors the SetTopicMap pattern
    in tools/speedrun_e2e_full.py (`_load_outline`)."""
    entries = [
        speedrun_pb2.TopicMapEntry(topic=_topic_key(name), label=name, weight=1.0)
        for (name, _weight, _base) in _TOPICS
    ]
    col._backend.set_topic_map(entries)


def _build_cards(col: Collection, did: int, rng: random.Random) -> list[dict]:
    """Create the shared deck: N due review cards across several topics, each
    with a hidden true mastery. Creation/due order is shuffled independently of
    mastery so the default (plain-Anki) order is a fair, mastery-blind baseline.
    Each note is tagged with its hierarchical `concept::topic` key (and the topic
    map is registered first) so the interleave feature has confusable sibling
    groups to reorder."""
    _register_topic_map(col)
    specs = [
        (name, weight, base)
        for (name, weight, base) in _TOPICS
        for _ in range(_CARDS_PER_TOPIC)
    ]
    rng.shuffle(specs)
    n = len(specs)
    cards: list[dict] = []
    for i, (name, weight, base) in enumerate(specs):
        mastery = _clamp(
            base + rng.uniform(-_MASTERY_NOISE, _MASTERY_NOISE), 0.02, 0.98
        )
        # distinct negative due => all overdue, default order = by due (stable)
        card = _make_review_card(
            col, did, f"{name} #{i}", due=i - n, tag=_topic_key(name)
        )
        cards.append(
            {
                "cid": card.id,
                "nid": card.nid,
                "topic": name,
                "weight": weight,
                "mastery": mastery,
            }
        )
    return cards


def _seed_weakness_evidence(col: Collection, cards: list[dict]) -> None:
    """Record the SAME miss evidence every arm sees: round((1-mastery)*A) misses
    out of A attempts per card, so the engine's weakness signal tracks the hidden
    true mastery (weaker card -> more recorded misses -> higher weakness)."""
    for c in cards:
        misses = round((1.0 - c["mastery"]) * _ATTEMPTS_PER_CARD)
        for j in range(_ATTEMPTS_PER_CARD):
            _record_attempt(col, c["cid"], c["nid"], correct=(j >= misses))


def _oracle_order(cards: list[dict]) -> list[int]:
    """Best-possible order given the hidden truth: descending true value
    (1-mastery)*weight. A reference upper bound, NOT one of the three arms."""
    ranked = sorted(
        cards, key=lambda c: (-(1.0 - c["mastery"]) * c["weight"], c["cid"])
    )
    return [c["cid"] for c in ranked]


def _expected_gain(order: list[int], by_id: dict, k: int) -> float:
    """Pre-registered metric: expected exam-score gain from studying the first K
    cards = sum over those cards of (1 - true_mastery) * yield_weight."""
    return sum((1.0 - by_id[c]["mastery"]) * by_id[c]["weight"] for c in order[:k])


def _weak_covered(order: list[int], by_id: dict, k: int) -> int:
    """Supporting metric: how many genuinely weak cards land in the budget."""
    return sum(1 for c in order[:k] if by_id[c]["mastery"] < _WEAK_THRESHOLD)


def _stats(values: list[float]) -> dict:
    return {
        "mean": round(statistics.mean(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "std": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
    }


def run_experiment(
    seeds: list[int] | None = None, budget_fraction: float = _BUDGET_FRACTION
) -> dict:
    """Run the three-arm ablation across several seeds and return a summary dict.

    Each seed builds a fresh collection, captures the three arm orders on the
    same cards, and scores the pre-registered metric at an equal study budget."""
    seeds = list(_DEFAULT_SEEDS if seeds is None else seeds)
    records: list[dict] = []
    for seed in seeds:
        rng = random.Random(seed)
        fd, path = tempfile.mkstemp(suffix=".anki2")
        os.close(fd)
        os.unlink(path)
        col = Collection(path)
        try:
            did = col.decks.id("Default")
            cards = _build_cards(col, did, rng)
            by_id = {c["cid"]: c for c in cards}
            # Arm 1 (plain Anki): default order, both features OFF, BEFORE any
            # Speedrun evidence.
            plain = _order(col, did, points_at_stake=False, interleave=False)
            _seed_weakness_evidence(col, cards)
            # Arm 2 (+points-at-stake): weakest/highest-value cards first, using
            # the shared evidence.
            points_only = _order(col, did, points_at_stake=True, interleave=False)
            # Arm 3 (+points-at-stake +interleave): the same value ranking, then
            # confusable sibling topics round-robined within each concept block.
            points_plus_interleave = _order(
                col, did, points_at_stake=True, interleave=True
            )
            records.append(
                {
                    "seed": seed,
                    "by_id": by_id,
                    "n": len(cards),
                    "orders": {
                        "plain_anki": plain,
                        "points_at_stake": points_only,
                        "points_plus_interleave": points_plus_interleave,
                    },
                    "oracle": _oracle_order(cards),
                    "weakest_cid": min(cards, key=lambda c: c["mastery"])["cid"],
                    "total_gain": sum(
                        (1.0 - c["mastery"]) * c["weight"] for c in cards
                    ),
                }
            )
        finally:
            col.close()

    n = records[0]["n"]
    k = max(1, round(n * budget_fraction))

    gains = {key: [] for key in _ARM_KEYS}
    weak = {key: [] for key in _ARM_KEYS}
    frac_captured = {key: [] for key in _ARM_KEYS}
    oracle_gains: list[float] = []
    positions_changed_points: list[float] = []
    positions_changed_interleave: list[float] = []
    top_is_weakest: list[int] = []
    first_is_weak: list[int] = []
    interleave_differs: list[int] = []
    for r in records:
        by_id = r["by_id"]
        for key in _ARM_KEYS:
            g = _expected_gain(r["orders"][key], by_id, k)
            gains[key].append(g)
            weak[key].append(_weak_covered(r["orders"][key], by_id, k))
            frac_captured[key].append(g / r["total_gain"] if r["total_gain"] else 0.0)
        oracle_gains.append(_expected_gain(r["oracle"], by_id, k))
        plain = r["orders"]["plain_anki"]
        points = r["orders"]["points_at_stake"]
        interleaved = r["orders"]["points_plus_interleave"]
        # Arm 2 vs arm 1: points-at-stake reorders the plain queue.
        positions_changed_points.append(
            float(sum(1 for a, b in zip(plain, points) if a != b))
        )
        # Arm 3 vs arm 2: interleave measurably reorders the value ranking.
        positions_changed_interleave.append(
            float(sum(1 for a, b in zip(points, interleaved) if a != b))
        )
        top_is_weakest.append(1 if points and points[0] == r["weakest_cid"] else 0)
        first_is_weak.append(
            1 if points and by_id[points[0]]["mastery"] < _WEAK_THRESHOLD else 0
        )
        interleave_differs.append(1 if interleaved != points else 0)

    plain_mean = statistics.mean(gains["plain_anki"])
    vs_plain = {}
    for key in _ARM_KEYS:
        delta = statistics.mean(gains[key]) - plain_mean
        pct = (delta / plain_mean * 100.0) if plain_mean else 0.0
        vs_plain[key] = {"delta_mean": round(delta, 4), "pct": round(pct, 1)}

    sensitivity = []
    for fr in _SENSITIVITY_FRACTIONS:
        kk = max(1, round(n * fr))
        row = {"budget_fraction": round(fr, 3), "k": kk}
        for key in _ARM_KEYS:
            row[key] = round(
                statistics.mean(
                    _expected_gain(r["orders"][key], r["by_id"], kk) for r in records
                ),
                4,
            )
        row["oracle"] = round(
            statistics.mean(
                _expected_gain(r["oracle"], r["by_id"], kk) for r in records
            ),
            4,
        )
        sensitivity.append(row)

    return {
        "experiment": "study_feature_three_arm_ablation",
        "simulation": True,
        "seeds": seeds,
        "params": {
            "topics": len(_TOPICS),
            "parent_concepts": len(set(_TOPIC_PARENTS.values())),
            "cards_per_topic": _CARDS_PER_TOPIC,
            "cards_total_n": n,
            "attempts_per_card": _ATTEMPTS_PER_CARD,
            "mastery_noise": _MASTERY_NOISE,
            "weak_threshold": _WEAK_THRESHOLD,
            "budget_fraction": round(budget_fraction, 4),
            "budget_k": k,
        },
        "primary_metric": (
            "expected exam-score gain within the first K reviews = sum over "
            "studied cards of (1 - true_mastery) * yield_weight"
        ),
        "arms": {
            key: {
                "expected_gain": _stats(gains[key]),
                "fraction_of_total_value": _stats(frac_captured[key]),
                "weak_cards_in_budget": _stats([float(x) for x in weak[key]]),
            }
            for key in _ARM_KEYS
        },
        "reference_oracle_value_sorted": {"expected_gain": _stats(oracle_gains)},
        "vs_plain_anki": vs_plain,
        "ordering_diagnostic": {
            "positions_changed_points_vs_plain": _stats(positions_changed_points),
            "positions_changed_interleave_vs_points": _stats(
                positions_changed_interleave
            ),
            "weakest_card_surfaced_first_frac": round(
                statistics.mean(top_is_weakest), 3
            ),
            "first_card_is_weak_frac": round(statistics.mean(first_is_weak), 3),
            "interleave_differs_from_points_frac": round(
                statistics.mean(interleave_differs), 3
            ),
        },
        "budget_sensitivity": sensitivity,
        "per_seed": [
            {
                "seed": r["seed"],
                "plain": round(
                    _expected_gain(r["orders"]["plain_anki"], r["by_id"], k), 4
                ),
                "points_at_stake": round(
                    _expected_gain(r["orders"]["points_at_stake"], r["by_id"], k), 4
                ),
                "points_plus_interleave": round(
                    _expected_gain(
                        r["orders"]["points_plus_interleave"], r["by_id"], k
                    ),
                    4,
                ),
                "oracle": round(_expected_gain(r["oracle"], r["by_id"], k), 4),
                "total": round(r["total_gain"], 4),
            }
            for r in records
        ],
    }


def _assert_experiment_sane(result: dict) -> None:
    """Sanity checks that hold by construction; keep the experiment honest."""
    arms = result["arms"]
    plain = arms["plain_anki"]["expected_gain"]["mean"]
    points = arms["points_at_stake"]["expected_gain"]["mean"]
    interleave = arms["points_plus_interleave"]["expected_gain"]["mean"]
    oracle = result["reference_oracle_value_sorted"]["expected_gain"]["mean"]
    diag = result["ordering_diagnostic"]

    # The three arms must be genuinely distinct, not two aliases of one order.
    # Arm 2 (+points-at-stake) must actually reorder the plain queue and lead
    # with a weak card.
    assert diag["positions_changed_points_vs_plain"]["mean"] > 0, result
    assert diag["first_card_is_weak_frac"] == 1.0, result
    # Arm 3 (+interleave) must measurably reorder the points-at-stake ranking on
    # the aggregate and on at least some seeds - otherwise it would collapse onto
    # arm 2 (the original soft spot this experiment closes).
    assert diag["positions_changed_interleave_vs_points"]["mean"] > 0, result
    assert diag["interleave_differs_from_points_frac"] > 0, result
    # Points-at-stake should surface at least as much value as plain within the
    # budget; no arm may beat the oracle upper bound. Interleave optimises for
    # spacing, not within-budget value, so it is only bounded above by the oracle.
    assert points >= plain, (points, plain)
    assert points <= oracle + 1e-9, (points, oracle)
    assert interleave <= oracle + 1e-9, (interleave, oracle)


def _experiment_summary(result: dict) -> dict:
    """Console view: everything except the verbose per-seed table."""
    return {k: v for k, v in result.items() if k != "per_seed"}


def main() -> int:
    args = sys.argv[1:]

    if args and args[0] == "--experiment":
        seeds = list(range(int(args[1]))) if len(args) > 1 else None
        result = run_experiment(seeds=seeds)
        print(json.dumps(result, indent=2))
        _assert_experiment_sane(result)
        return 0

    if not args:
        rc = _self_test()
        if rc != 0:
            return rc
        print("\n[3-arm study-feature experiment]")
        result = run_experiment()
        print(json.dumps(_experiment_summary(result), indent=2))
        _assert_experiment_sane(result)
        print("\nexperiment self-test: PASS")
        return 0

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
