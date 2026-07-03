#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun paraphrase test (section 7d): does performance just copy memory?

Speedrun scores two different things: **recall** (can you remember a fact - read
off the SRS substrate) and **performance** (can you apply it on an exam-style
question). If those two numbers always agreed, the performance model would be
nothing but a relabeled copy of the memory model. This harness proves they can
diverge, and that the divergence is driven by application evidence rather than
by the memory signal.

It loads a pack of source cards (each a memory fact plus two exam-style questions
that reword the SAME idea in NEW words), then, through the SpeedrunService - the
same Rust engine the apps use, AI-off:

  * builds a fresh temp collection,
  * turns every source card into a MATURE review card (interval >= 21d) so the
    binary recall proxy is 1.0 for all of them,
  * registers each card's two reworded questions as held-out question items
    (they never become cards, so answering them can't leak into scheduling),
  * simulates a student answering those held-out questions, and
  * reads back get_performance_report() to compare recall vs. performance.

Two arms are run on identical memory substrates (all cards mature, recall = 1.0):

  main arm    - a realistic student who recalls everything but applies it only
                ~60% of the time  -> a large positive recall-vs-performance gap.
  control arm - the same cards with every reworded question answered correctly
                -> performance rises to ~1.0 and the gap collapses to ~0.

Holding memory fixed while performance moves is the proof that performance is
measured separately from memory. A leakage check confirms the reworded questions
are not verbatim copies of the cards (rslib/src/speedrun/leakage.rs).

Usage:
    python tools/speedrun_paraphrase.py [pack.json]   # default: bundled pack

With no argument it runs the full 30-card demo, asserts the invariants, and
regenerates tools/speedrun_paraphrase_report.md. Run it through the wrapper so
the built pylib bridge is on the path:
    ./tools/speedrun_paraphrase.sh
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

from anki import speedrun_pb2
from anki.collection import Collection

_HERE = os.path.dirname(os.path.abspath(__file__))
PACK_PATH = os.path.join(_HERE, "speedrun_paraphrase_pack.json")
REPORT_PATH = os.path.join(_HERE, "speedrun_paraphrase_report.md")

# Mature review-card values (mirrors tools/speedrun_ablation.py and the engine's
# MATURE_INTERVAL_DAYS = 21 recall proxy). ivl of 30 days > 21 -> recall = 1.0.
CARD_TYPE_REVIEW = 2
QUEUE_REVIEW = 2
MATURE_IVL = 30

# Question types (mirror rslib/src/speedrun/mod.rs): 0=SRS, 1=PASSAGE_MCQ,
# 2=DISCRETE. The reworded items are standalone exam-style questions -> DISCRETE.
QUESTION_TYPE_DISCRETE = 2
PROVENANCE_HAND_AUTHORED = 0

# The simulated student's per-card mastery on the two reworded questions, as a
# fixed, reproducible pattern over the card index. Repeats every 5 cards:
#   2 -> both reworded questions correct   (per-card performance 1.0)
#   1 -> one correct                       (per-card performance 0.5)
#   0 -> neither correct                   (per-card performance 0.0)
# Over 30 cards this is 12x{2} + 12x{1} + 6x{0}, i.e. 36/60 correct, so the
# per-card mean performance is exactly 0.6 while recall is 1.0 -> a 0.4 gap.
_MASTERY_TEMPLATE = (2, 1, 2, 1, 0)


def _mastery(card_index: int) -> int:
    return _MASTERY_TEMPLATE[card_index % len(_MASTERY_TEMPLATE)]


def main_student_correct(card_index: int, q_index: int) -> bool:
    """Main arm: the first `mastery` of a card's two questions are answered right."""
    return q_index < _mastery(card_index)


def all_correct(card_index: int, q_index: int) -> bool:
    """Control arm: every reworded question is answered correctly."""
    return True


class Checker:
    """Tiny assertion helper that logs each check (borrowed from speedrun_e2e)."""

    def __init__(self) -> None:
        self.n = 0

    def ok(self, label: str, cond: bool, detail: str = "") -> None:
        self.n += 1
        if not cond:
            raise AssertionError(f"FAIL: {label}" + (f" ({detail})" if detail else ""))
        print(f"  \u2713 {label}" + (f"  \u2014 {detail}" if detail else ""))


def load_pack(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _new_collection() -> Collection:
    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(path)  # Collection() creates it fresh
    return Collection(path)


def build_collection(col: Collection, cards: list[dict]) -> list[dict]:
    """Add each pack card as a mature Basic note and register its reworded,
    held-out questions. Returns per-card build info (card_id, interval, ...)."""
    col._backend.seed_mcat_topic_outline()
    model = col.models.by_name("Basic")
    did = col.decks.id("Default")

    built: list[dict] = []
    for idx, card in enumerate(cards):
        note = col.new_note(model)
        note["Front"] = card["front"]
        note["Back"] = card["back"]
        note.tags = [card["topic"], card["tag"]]
        col.add_note(note, did)

        c = note.cards()[0]
        c.type = CARD_TYPE_REVIEW
        c.queue = QUEUE_REVIEW
        c.ivl = MATURE_IVL
        c.due = 0
        col.update_card(c)

        q_item_ids: list[int] = []
        for q in card["questions"]:
            payload = json.dumps(
                {
                    "stem": q["stem"],
                    "options": q["options"],
                    "correct_index": q["correct_index"],
                    "explanation": q["explanation"],
                }
            )
            # The Python backend unwraps a single-field response (QuestionItemId)
            # to the scalar id itself.
            item_id = col._backend.add_question_item(
                speedrun_pb2.QuestionItem(
                    card_id=c.id,
                    topic=card["topic"],
                    provenance=PROVENANCE_HAND_AUTHORED,
                    payload=payload,
                )
            )
            q_item_ids.append(item_id)

        built.append(
            {
                "index": idx,
                "tag": card["tag"],
                "topic": card["topic"],
                "card_id": c.id,
                "note_id": note.id,
                "ivl": c.ivl,
                "questions": card["questions"],
                "q_item_ids": q_item_ids,
            }
        )
    return built


def simulate(col: Collection, built: list[dict], correct_fn) -> None:
    """Answer each held-out reworded question, recording an exam-style attempt.

    `correct_fn(card_index, q_index) -> bool` decides correctness; the selected
    option is the key when right and a deliberately wrong option when wrong."""
    for card in built:
        for j, q in enumerate(card["questions"]):
            correct = correct_fn(card["index"], j)
            n_opts = len(q["options"])
            selected = (
                q["correct_index"] if correct else (q["correct_index"] + 1) % n_opts
            )
            col._backend.record_attempt(
                speedrun_pb2.RecordAttemptRequest(
                    card_id=card["card_id"],
                    note_id=card["note_id"],
                    session_id="paraphrase",
                    answered_at_ms=1_700_000_000_000 + card["index"] * 10 + j,
                    took_ms=9000 if correct else 7000,
                    question_type=QUESTION_TYPE_DISCRETE,
                    correct=correct,
                    selected=selected,
                    signals=speedrun_pb2.ClassifyAttemptRequest(
                        correct=correct,
                        took_ms=9000 if correct else 7000,
                        question_type=QUESTION_TYPE_DISCRETE,
                    ),
                )
            )


def per_card_rows(col: Collection, built: list[dict]) -> list[dict]:
    """Recall proxy vs. reworded-question accuracy per source card, read back
    from the engine's own stored attempts."""
    rows = []
    for card in built:
        # A single repeated-field response (SrAttempts) unwraps to a plain list.
        attempts = col._backend.get_attempts_for_card(card_id=card["card_id"])
        total = len(attempts)
        correct = sum(1 for a in attempts if a.correct)
        mature = card["ivl"] >= 21
        rows.append(
            {
                "tag": card["tag"],
                "topic": card["topic"],
                "ivl": card["ivl"],
                "mature": mature,
                "recall": 1.0 if mature else 0.0,
                "exam_total": total,
                "exam_correct": correct,
                "performance": (correct / total) if total else 0.0,
            }
        )
    return rows


def report_dict(perf) -> dict:
    return {
        "cards_evaluated": perf.cards_evaluated,
        "exam_attempts": perf.exam_attempts,
        "recall_rate": round(perf.recall_rate, 4),
        "performance_rate": round(perf.performance_rate, 4),
        "recall_perf_gap": round(perf.recall_perf_gap, 4),
        "sufficient": perf.sufficient,
        "note": perf.note,
        "question_items": perf.question_items,
    }


def expected_performance(built: list[dict], correct_fn) -> float:
    """Independently computed per-card mean accuracy, to cross-check the engine."""
    per_card = []
    for card in built:
        n = len(card["questions"])
        c = sum(1 for j in range(n) if correct_fn(card["index"], j))
        per_card.append(c / n if n else 0.0)
    return sum(per_card) / len(per_card) if per_card else 0.0


def run_arm(pack: dict, correct_fn) -> dict:
    """Build a fresh collection, simulate one student, and gather the evidence."""
    cards = pack["cards"]
    col = _new_collection()
    try:
        built = build_collection(col, cards)
        simulate(col, built, correct_fn)
        perf = col._backend.get_performance_report()
        leak = col._backend.get_leakage_report()
        cov = col._backend.get_coverage_report()
        return {
            "report": report_dict(perf),
            "rows": per_card_rows(col, built),
            "leakage": {
                "total_items": leak.total_items,
                "flagged": leak.flagged,
                "clean": leak.clean,
            },
            "coverage": {
                "topics_total": cov.topics_total,
                "topics_covered": cov.topics_covered,
            },
            "expected_performance": expected_performance(built, correct_fn),
            "n_cards": len(built),
        }
    finally:
        col.close()


def _fmt(x: float) -> str:
    return f"{x:.3f}"


def render_report(pack: dict, main: dict, control: dict) -> str:
    mr = main["report"]
    cr = control["report"]
    lk = main["leakage"]
    n = main["n_cards"]
    gap = mr["recall_perf_gap"]

    L: list[str] = []
    L.append("# Speedrun \u00a77d \u2014 Paraphrase Test (recall vs. performance)\n")
    L.append(
        "Speedrun reports **recall** (memory: can you remember the fact, read off "
        "the SRS substrate) and **performance** (application: can you answer an "
        "exam-style question that rewords the same idea) as two separate numbers. "
        "This artifact tests whether performance is genuinely measured separately "
        "from memory, or just a relabeled copy of it. If the two numbers always "
        "matched, the performance model would carry no independent information.\n"
    )
    L.append(
        f"\n**Pack:** `{pack['name']}` (license {pack['license']}) \u2014 {n} source "
        f"cards \u00d7 2 reworded exam-style questions = {lk['total_items']} held-out "
        "items.\n"
    )
    L.append(
        "**Engine:** `SpeedrunService.get_performance_report()` over a fresh temp "
        "collection, AI-off. Recall proxy = source card is mature (SRS interval "
        "\u2265 21 days); performance = correct/attempts on the held-out reworded "
        "questions, averaged per card.\n"
    )

    L.append("\n## Headline \u2014 main arm (a realistic student)\n")
    L.append(
        f"All {n} source cards were made mature review cards (interval "
        f"{MATURE_IVL}d \u2265 21d), so the recall proxy is 1.0 for every card. The "
        "student then answered the reworded, held-out questions with graded "
        "mastery.\n"
    )
    L.append("\n| metric | value |")
    L.append("| --- | --- |")
    L.append(f"| cards evaluated | {mr['cards_evaluated']} |")
    L.append(f"| exam-style attempts | {mr['exam_attempts']} |")
    L.append(f"| recall_rate (memory) | {_fmt(mr['recall_rate'])} |")
    L.append(f"| performance_rate (application) | {_fmt(mr['performance_rate'])} |")
    L.append(f"| **recall_perf_gap** | **{_fmt(gap)}** |")
    L.append(f"| sufficient (\u22655 cards) | {mr['sufficient']} |")
    L.append(f"| engine note | {mr['note']} |")
    L.append(
        f"| leakage check | {'CLEAN' if lk['clean'] else 'REVIEW'} "
        f"({lk['flagged']} / {lk['total_items']} flagged) |"
    )
    L.append(
        f"\n**The gap is {_fmt(gap)}** \u2014 recall {_fmt(mr['recall_rate'])} vs. "
        f"performance {_fmt(mr['performance_rate'])}. The student reliably "
        "remembers every fact but applies it correctly only about "
        f"{round(100 * mr['performance_rate'])}% of the time on reworded items. "
        "The two numbers are far apart, so performance is not an echo of memory.\n"
    )

    L.append("\n## Control arm \u2014 same memory, perfect application\n")
    L.append(
        "The same "
        f"{n} mature cards (recall still 1.0) with **every** reworded question "
        "answered correctly:\n"
    )
    L.append("\n| metric | main arm | control arm |")
    L.append("| --- | --- | --- |")
    L.append(f"| recall_rate | {_fmt(mr['recall_rate'])} | {_fmt(cr['recall_rate'])} |")
    L.append(
        f"| performance_rate | {_fmt(mr['performance_rate'])} | "
        f"{_fmt(cr['performance_rate'])} |"
    )
    L.append(
        f"| recall_perf_gap | {_fmt(mr['recall_perf_gap'])} | "
        f"{_fmt(cr['recall_perf_gap'])} |"
    )
    L.append(f"| engine note | {mr['note']} | {cr['note']} |")
    L.append(
        "\nMemory was held fixed at "
        f"{_fmt(mr['recall_rate'])} across both arms, yet performance moved from "
        f"{_fmt(mr['performance_rate'])} to {_fmt(cr['performance_rate'])} and the "
        f"gap from {_fmt(mr['recall_perf_gap'])} to {_fmt(cr['recall_perf_gap'])}. "
        "Only the held-out application evidence changed, so the performance signal "
        "is computed from that evidence \u2014 it is not derived from, or copied "
        "out of, the memory signal.\n"
    )

    L.append("\n## Is the bridge real? (interpretation)\n")
    L.append(
        "**Yes \u2014 performance is measured separately from memory, and here the "
        "memory\u2192application bridge is weak.**\n"
    )
    L.append(
        "\n- **Independent sources.** Recall is read from each card's SRS interval; "
        "performance is read from held-out reworded questions that never enter the "
        "collection as cards, so answering them cannot change scheduling. The two "
        "signals come from different evidence.\n"
        "- **They diverge.** A "
        f'{_fmt(gap)} gap (well above the engine\'s 0.10 "aligned" band) shows the '
        "numbers can pull apart. If performance were just copying memory it would "
        f"read \u2248{_fmt(mr['recall_rate'])} like recall and the gap would be "
        "\u22480.\n"
        "- **The gap tracks application, not memory.** The control arm holds memory "
        "constant and lifts only the reworded-answer accuracy; performance and the "
        'gap move accordingly. That is the manipulation that rules out "copying."\n'
        f"- **The rewording is real.** All {lk['total_items']} questions passed the "
        "engine's leakage check (none is a verbatim substring of its source card), "
        "so the divergence reflects genuine application, not the student re-reading "
        "the card.\n"
    )
    L.append(
        "\n**Honest caveats.** Recall here is a binary maturity proxy (interval "
        "\u2265 21d), not a graded retrieval probability, so the recall arm is "
        'saturated at 1.0 by construction. The "student" is simulated with a '
        "fixed, documented answer pattern to produce a controlled, reproducible "
        "gap. And a *zero* gap on its own would be ambiguous \u2014 it could mean a "
        "genuinely strong student *or* a metric that merely echoes memory \u2014 "
        "which is exactly why the test's power comes from its ability to reveal "
        "divergence (the main arm), not agreement. The headline claim is about "
        "measurement separability; the *sign* of the gap (recall outruns "
        "application) is the actionable, student-specific finding.\n"
    )

    L.append("\n## Per-card breakdown \u2014 main arm\n")
    L.append(
        "Recall proxy vs. reworded-question accuracy per source card, read back "
        "from the engine's stored attempts.\n"
    )
    L.append(
        "\n| tag | topic | ivl (d) | mature | recall | reworded correct | reworded accuracy |"
    )
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for r in main["rows"]:
        L.append(
            f"| {r['tag']} | {r['topic']} | {r['ivl']} | "
            f"{'yes' if r['mature'] else 'no'} | {_fmt(r['recall'])} | "
            f"{r['exam_correct']}/{r['exam_total']} | {_fmt(r['performance'])} |"
        )
    L.append(
        f"\nEvery card: recall = 1.00 (mature). Reworded accuracy varies across "
        f"cards and averages {_fmt(mr['performance_rate'])} \u2014 that spread, "
        "against a flat recall of 1.00, is the recall-vs-performance gap.\n"
    )

    cov = main["coverage"]
    L.append(
        f"\n*Coverage note:* the {n} cards span all "
        f"{cov['topics_covered']}/{cov['topics_total']} MCAT foundational-concept "
        "topics (fc1\u2013fc10).\n"
    )

    L.append("\n## Reproduce\n")
    L.append("```bash")
    L.append("./tools/speedrun_paraphrase.sh")
    L.append("# or point it at any pack in the same format:")
    L.append("./tools/speedrun_paraphrase.sh tools/speedrun_paraphrase_pack.json")
    L.append("```")
    L.append(
        "\nThe harness builds a fresh temp collection each run and writes this "
        "report; the numbers above are produced by the Rust engine, not "
        "hand-entered.\n"
    )
    return "\n".join(L) + "\n"


def main() -> int:
    pack_path = sys.argv[1] if len(sys.argv) > 1 else PACK_PATH
    pack = load_pack(pack_path)
    n = len(pack["cards"])
    check = Checker()

    print(f"\n[paraphrase] pack: {pack['name']}  ({n} cards, {pack_path})")

    print("\n[1] Main arm \u2014 mature cards, ~60% application accuracy")
    main_arm = run_arm(pack, main_student_correct)
    mr = main_arm["report"]
    exp = main_arm["expected_performance"]
    print("  " + json.dumps(mr))
    check.ok(
        "every source card is evaluated",
        mr["cards_evaluated"] == n,
        f"{mr['cards_evaluated']}/{n}",
    )
    check.ok(
        "two exam-style attempts per card",
        mr["exam_attempts"] == 2 * n,
        f"{mr['exam_attempts']}",
    )
    check.ok(
        "recall proxy is saturated (all mature)", abs(mr["recall_rate"] - 1.0) < 1e-6
    )
    check.ok(
        "engine performance matches the hand-computed per-card mean",
        abs(mr["performance_rate"] - exp) < 1e-3,
        f"engine={mr['performance_rate']:.4f} expected={exp:.4f}",
    )
    check.ok(
        "recall_perf_gap = recall - performance",
        abs(mr["recall_perf_gap"] - (mr["recall_rate"] - mr["performance_rate"]))
        < 1e-4,
        f"gap={mr['recall_perf_gap']:.4f}",
    )
    check.ok("a meaningful positive gap exists (> 0.1)", mr["recall_perf_gap"] > 0.1)
    check.ok("the gap is trusted with >=5 evaluated cards", mr["sufficient"] is True)
    check.ok("engine flags the weak bridge", "bridge is weak" in mr["note"], mr["note"])
    check.ok(
        "reworded questions survive the leakage check",
        main_arm["leakage"]["clean"] and main_arm["leakage"]["total_items"] == 2 * n,
        f"{main_arm['leakage']['flagged']}/{main_arm['leakage']['total_items']} flagged",
    )

    print("\n[2] Control arm \u2014 same mature cards, perfect application")
    control_arm = run_arm(pack, all_correct)
    cr = control_arm["report"]
    print("  " + json.dumps(cr))
    check.ok("control recall is still saturated", abs(cr["recall_rate"] - 1.0) < 1e-6)
    check.ok(
        "control performance reaches ~1.0", abs(cr["performance_rate"] - 1.0) < 1e-6
    )
    check.ok(
        "control gap collapses to ~0",
        abs(cr["recall_perf_gap"]) < 1e-3,
        f"{cr['recall_perf_gap']:.4f}",
    )
    check.ok(
        "engine calls the control arm aligned", "aligned" in cr["note"], cr["note"]
    )
    check.ok(
        "same memory, different performance across arms",
        abs(mr["recall_rate"] - cr["recall_rate"]) < 1e-6
        and abs(mr["performance_rate"] - cr["performance_rate"]) > 0.1,
        f"perf {mr['performance_rate']:.3f} vs {cr['performance_rate']:.3f}",
    )

    report_md = render_report(pack, main_arm, control_arm)
    with open(REPORT_PATH, "w") as f:
        f.write(report_md)

    print(
        f"\n[paraphrase] recall={mr['recall_rate']:.3f}  "
        f"performance={mr['performance_rate']:.3f}  gap={mr['recall_perf_gap']:.3f}  "
        f"(control gap={cr['recall_perf_gap']:.3f})"
    )
    print(
        f"[paraphrase] PASS ({check.n} checks) \u2014 report written to {os.path.relpath(REPORT_PATH, _HERE)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
