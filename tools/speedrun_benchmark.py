#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""One-command Speedrun evaluation harness (AI-off).

Reports the Speedrun signals over a collection:
  - readiness (memory / performance / readiness on the MCAT scale + give-up state)
  - recall-vs-performance gap (the "bridge")
  - topic coverage (% of the outline the deck covers)
  - diagnosis distribution (how misses were classified)

Usage:
    python tools/speedrun_benchmark.py [collection.anki2]

With no path it builds a small synthetic collection, runs the report, and
asserts a few invariants (a re-runnable self-test). Everything goes through the
SpeedrunService backend (the same Rust engine the apps use); no AI is involved.

Run it via the wrapper so the built pylib + bridge are on the path:
    ./tools/speedrun_benchmark.sh
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

from anki.collection import Collection
from anki import speedrun_pb2

DIAGNOSIS_LABELS = {
    0: "none",
    1: "memory",
    2: "reasoning",
    3: "passage",
    4: "test_taking",
    5: "correct",
}


def build_report(col: Collection) -> dict:
    backend = col._backend
    readiness = backend.compute_readiness()
    perf = backend.get_performance_report()
    cov = backend.get_coverage_report()
    cal = backend.get_calibration_report()
    leak = backend.get_leakage_report()

    rows = col.db.all(
        "select diagnosis_kind, count(*) from sr_attempts group by diagnosis_kind"
    )
    distribution = {DIAGNOSIS_LABELS.get(kind, str(kind)): n for kind, n in rows}
    total_attempts = col.db.scalar("select count(*) from sr_attempts") or 0

    return {
        "attempts": {
            "total": total_attempts,
            "diagnosis_distribution": distribution,
        },
        "readiness": {
            "memory": round(readiness.memory, 4),
            "performance": round(readiness.performance, 4),
            "readiness_scaled": readiness.readiness_scaled,
            "range": [readiness.low_scaled, readiness.high_scaled],
            "coverage": round(readiness.coverage, 4),
            "sufficient": readiness.sufficient,
            "reason": readiness.reason,
        },
        "performance": {
            "cards_evaluated": perf.cards_evaluated,
            "exam_attempts": perf.exam_attempts,
            "recall_rate": round(perf.recall_rate, 4),
            "performance_rate": round(perf.performance_rate, 4),
            "recall_perf_gap": round(perf.recall_perf_gap, 4),
            "sufficient": perf.sufficient,
            "note": perf.note,
        },
        "coverage": {
            "topics_total": cov.topics_total,
            "topics_covered": cov.topics_covered,
            "coverage": round(cov.coverage, 4),
            "weighted_coverage": round(cov.weighted_coverage, 4),
        },
        "calibration": {
            "n": cal.n,
            "brier": round(cal.brier, 4),
            "log_loss": round(cal.log_loss, 4),
            "sufficient": cal.sufficient,
            "note": cal.note,
        },
        "leakage": {
            "total_items": leak.total_items,
            "flagged": leak.flagged,
            "clean": leak.clean,
        },
    }


def _seed_synthetic(col: Collection) -> None:
    """Populate a fresh collection with a little of everything to exercise the report."""
    backend = col._backend
    backend.seed_mcat_topic_outline()

    model = col.models.by_name("Basic")
    did = col.decks.id("Default")
    note = col.new_note(model)
    note["Front"] = "what is the pKa of an amino acid"
    note.tags = ["fc1"]
    col.add_note(note, did)
    source_cid = note.cards()[0].id

    # a held-out paraphrase question for the card
    backend.add_question_item(
        speedrun_pb2.QuestionItem(
            card_id=source_cid, topic="fc1", provenance=0, payload='{"stem": "reworded"}'
        )
    )

    # SRS recall failure -> memory diagnosis (with a low predicted probability)
    backend.record_attempt(
        speedrun_pb2.RecordAttemptRequest(
            card_id=source_cid,
            note_id=note.id,
            session_id="bench",
            answered_at_ms=1,
            took_ms=4000,
            question_type=0,
            correct=False,
            predicted=0.3,
            signals=speedrun_pb2.ClassifyAttemptRequest(
                correct=False, took_ms=4000, recall_failed=True, question_type=0
            ),
        )
    )
    # exam-style attempts: one correct, one wrong (slow -> reasoning)
    for correct, took in [(True, 9000), (False, 9000)]:
        backend.record_attempt(
            speedrun_pb2.RecordAttemptRequest(
                card_id=source_cid,
                note_id=note.id,
                session_id="bench",
                answered_at_ms=2,
                took_ms=took,
                question_type=1,
                correct=correct,
                predicted=0.6,
                signals=speedrun_pb2.ClassifyAttemptRequest(
                    correct=correct, took_ms=took, question_type=1
                ),
            )
        )


def main() -> int:
    if len(sys.argv) > 1:
        col = Collection(sys.argv[1])
        try:
            print(json.dumps(build_report(col), indent=2))
        finally:
            col.close()
        return 0

    # self-test on synthetic data
    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(path)
    col = Collection(path)
    try:
        _seed_synthetic(col)
        report = build_report(col)
        print(json.dumps(report, indent=2))

        # invariants
        assert report["attempts"]["total"] == 3, report
        dist = report["attempts"]["diagnosis_distribution"]
        assert dist.get("memory", 0) == 1, dist
        assert dist.get("correct", 0) == 1, dist
        assert dist.get("reasoning", 0) == 1, dist
        assert report["coverage"]["topics_total"] == 10
        assert report["coverage"]["topics_covered"] == 1
        # one exam-style card, half correct
        assert report["performance"]["cards_evaluated"] == 1
        assert abs(report["performance"]["performance_rate"] - 0.5) < 1e-6
        # not enough evidence yet -> readiness abstains
        assert report["readiness"]["sufficient"] is False
        # all three attempts captured a prediction
        assert report["calibration"]["n"] == 3, report["calibration"]
        # the reworded held-out item is not a verbatim copy of the card
        assert report["leakage"]["clean"], report["leakage"]
        print("\nself-test: PASS")
    finally:
        col.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
