#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""End-to-end Speedrun engine workflow test.

Builds a small suite of memory flashcards and reasoning passages, then drives a
full study session through the SpeedrunService - the same protobuf boundary the
desktop app and the phone use - and asserts the engine behaves as designed:

  * it abstains honestly when there is no evidence,
  * coverage tracks tagged concepts,
  * every failure mode (memory / passage / test-taking / reasoning / correct)
    is classified and routed deterministically,
  * held-out questions feed a performance signal measured separately from recall
    (the recall-vs-performance gap), and
  * attempts persist with their diagnosis.

Run via tools/speedrun_e2e.sh (sets PYTHONPATH to the built bridge). Exits
non-zero on the first failed expectation.
"""

from __future__ import annotations

import os
import tempfile

from anki import speedrun_pb2
from anki.collection import Collection

# Diagnosis kinds (mirror rslib/src/speedrun/mod.rs).
MEMORY, REASONING, PASSAGE, TEST_TAKING, CORRECT = 1, 2, 3, 4, 5
# Routed actions.
ACTION_RESURFACE, ACTION_PASSAGE, ACTION_STRATEGY, ACTION_ADVANCE = 1, 2, 3, 4
# Question types.
SRS, PASSAGE_MCQ, DISCRETE = 0, 1, 2


class Checker:
    """Tiny assertion helper that logs each step like a workflow narrative."""

    def __init__(self) -> None:
        self.n = 0

    def ok(self, label: str, cond: bool, detail: str = "") -> None:
        self.n += 1
        if not cond:
            raise AssertionError(f"FAIL: {label}" + (f" ({detail})" if detail else ""))
        print(f"  \u2713 {label}" + (f"  \u2014 {detail}" if detail else ""))


def _new_collection() -> Collection:
    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(path)  # Collection() creates it fresh
    return Collection(path)


def _attempt(
    col: Collection,
    *,
    card_id: int,
    note_id: int,
    correct: bool,
    question_type: int,
    took_ms: int = 6000,
    recall_failed: bool = False,
    passage_evidence_missed: bool = False,
    predicted: float = 0.0,
):
    signals = speedrun_pb2.ClassifyAttemptRequest(
        correct=correct,
        took_ms=took_ms,
        recall_failed=recall_failed,
        passage_evidence_missed=passage_evidence_missed,
        question_type=question_type,
    )
    req = speedrun_pb2.RecordAttemptRequest(
        card_id=card_id,
        note_id=note_id,
        session_id="e2e",
        answered_at_ms=1_700_000_000_000,
        took_ms=took_ms,
        question_type=question_type,
        correct=correct,
        signals=signals,
        predicted=predicted,
        data="{}",
    )
    return col._backend.record_attempt(req)


def main() -> int:
    check = Checker()
    col = _new_collection()
    try:
        print("\n[1] Honest abstention on an empty collection")
        snap = col._backend.compute_readiness()
        check.ok("empty collection does not fabricate a score", not snap.sufficient)
        check.ok("abstention explains itself", "not enough evidence" in snap.reason)
        check.ok(
            "readiness stays on the MCAT scale while abstaining",
            472 <= snap.readiness_scaled <= 528,
        )
        check.ok("names the blocking dimension", snap.blocking_dimension == "memory")

        print("\n[2] A small suite of memory cards -> coverage")
        assert col._backend.seed_mcat_topic_outline() == 10
        model = col.models.by_name("Basic")
        did = col.decks.id("Default")
        card_of: dict[str, int] = {}
        note_of: dict[str, int] = {}
        for tag in ("fc1", "fc2", "fc3"):
            note = col.new_note(model)
            note["Front"] = f"memory fact for {tag}"
            note["Back"] = "the answer"
            note.tags = [tag]
            col.add_note(note, did)
            card_of[tag] = note.cards()[0].id
            note_of[tag] = note.id
        cov = col._backend.get_coverage_report()
        check.ok("coverage counts the 3 tagged concepts", cov.topics_covered == 3, f"of {cov.topics_total}")

        print("\n[3] Reasoning passages (held-out questions) linked to concepts")
        for tag in ("fc1", "fc2", "fc3"):
            col._backend.add_question_item(
                speedrun_pb2.QuestionItem(
                    card_id=card_of[tag],
                    topic=tag,
                    provenance=0,
                    payload='{"stem":"passage-style question","options":["a","b","c","d"],"correct_index":0}',
                )
            )
        report = col._backend.get_performance_report()
        check.ok("held-out bank holds the 3 questions", report.question_items == 3)
        routed = col._backend.get_routed_practice(topic="fc1")
        check.ok("routed practice returns the fc1 question", len(routed) == 1 and routed[0].topic == "fc1")

        print("\n[4] The study workflow classifies every failure mode")
        exam_total = 0
        exam_correct = 0

        # A recall miss on a plain review -> memory gap, resurface sooner.
        d = _attempt(col, card_id=card_of["fc1"], note_id=note_of["fc1"], correct=False,
                     question_type=SRS, recall_failed=True)
        check.ok("recall miss -> memory gap", d.diagnosis.kind == MEMORY)
        check.ok("memory gap routes to resurface", d.diagnosis.routed_action == ACTION_RESURFACE)

        # Missed the passage evidence -> passage-comprehension gap.
        d = _attempt(col, card_id=card_of["fc1"], note_id=note_of["fc1"], correct=False,
                     question_type=PASSAGE_MCQ, passage_evidence_missed=True)
        exam_total += 1
        check.ok("missed passage evidence -> passage gap", d.diagnosis.kind == PASSAGE)
        check.ok("passage gap routes to passage practice", d.diagnosis.routed_action == ACTION_PASSAGE)

        # Rushed a question and got it wrong -> test-taking gap.
        d = _attempt(col, card_id=card_of["fc2"], note_id=note_of["fc2"], correct=False,
                     question_type=PASSAGE_MCQ, took_ms=3000)
        exam_total += 1
        check.ok("rushed miss -> test-taking gap", d.diagnosis.kind == TEST_TAKING)
        check.ok("test-taking gap routes to strategy", d.diagnosis.routed_action == ACTION_STRATEGY)

        # Deliberated, knew the fact, still wrong -> reasoning gap.
        d = _attempt(col, card_id=card_of["fc2"], note_id=note_of["fc2"], correct=False,
                     question_type=PASSAGE_MCQ, took_ms=22000)
        exam_total += 1
        check.ok("slow deliberate miss -> reasoning gap", d.diagnosis.kind == REASONING)

        # A correct application advances.
        d = _attempt(col, card_id=card_of["fc3"], note_id=note_of["fc3"], correct=True,
                     question_type=DISCRETE, took_ms=9000)
        exam_total += 1
        exam_correct += 1
        check.ok("correct application -> advance", d.diagnosis.kind == CORRECT)
        check.ok("correct advances the action", d.diagnosis.routed_action == ACTION_ADVANCE)

        print("\n[5] Performance is measured separately from recall (held out from SRS)")
        perf = col._backend.get_performance_report()
        check.ok("exam-style attempts counted", perf.exam_attempts == exam_total, f"{perf.exam_attempts}")
        check.ok("performance is evaluated on the 3 linked concept cards", perf.cards_evaluated == 3)
        # Per-card mean, not per-attempt: fc1 0/1, fc2 0/2, fc3 1/1 -> (0 + 0 + 1) / 3.
        check.ok(
            "performance rate is the per-card mean",
            abs(perf.performance_rate - (1.0 / 3.0)) < 1e-6,
            f"{perf.performance_rate:.3f}",
        )
        check.ok(
            "the recall-vs-performance gap abstains until >=5 cards are evaluated",
            not perf.sufficient and "not enough evidence" in perf.note,
        )
        print("      held-out questions never touch SRS scheduling; a trustworthy "
              "gap needs mature cards + \u22655 evaluated, so it abstains here")

        print("\n[6] Evidence persists with its diagnosis")
        fc1_attempts = col._backend.get_attempts_for_card(card_id=card_of["fc1"])
        check.ok("fc1 kept both of its recorded attempts", len(fc1_attempts) == 2)
        kinds = sorted(a.diagnosis_kind for a in fc1_attempts)
        check.ok("fc1 attempts carry memory + passage diagnoses", kinds == [MEMORY, PASSAGE])

        print("\n[7] Readiness recomputes coherently after the session")
        snap = col._backend.compute_readiness()
        check.ok("readiness is still on the MCAT scale", 472 <= snap.readiness_scaled <= 528)
        check.ok("readiness reports either a score or a reason",
                 snap.sufficient or bool(snap.reason))

        print(f"\nspeedrun e2e: PASS ({check.n} checks)")
        return 0
    finally:
        col.close()


if __name__ == "__main__":
    raise SystemExit(main())
