#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Full-pipeline end-to-end Speedrun test: abstain -> real projected score.

Where tools/speedrun_e2e.py is a small smoke test of the classifier and the
recall-vs-performance gap, this harness drives the *whole* readiness pipeline
through the SpeedrunService protobuf boundary (col._backend.*) the way the
desktop app and the phone do:

  * an empty collection abstains honestly ("not enough evidence"),
  * the real 31-category AAMC content outline is loaded via SetTopicMap and a
    realistic deck (all 31 topics, ~4 real cards each from the open-licensed
    content library) reaches full coverage on BOTH the raw and weighted metric,
  * at least 20 cards are matured through the REAL v3 scheduler with the
    collection clock advanced deterministically (no waiting real days),
  * at least 30 graded and at least 20 exam-style attempts are recorded, with
    per-attempt predictions feeding calibration,
  * readiness becomes `sufficient` with an in-range MCAT score and a likely
    range that brackets it,
  * calibration, the exam plan, and the performance gap all compute, and
  * the points-at-stake and topic-interleave study features each reorder the
    review queue versus the default order.

Run via tools/speedrun_e2e_full.sh (sets PYTHONPATH to the built bridge). Exits
non-zero on the first failed expectation.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile

from anki import speedrun_pb2
from anki.collection import Collection
from anki.media import media_paths_from_col_path

# Question types (mirror rslib/src/speedrun/mod.rs): 0=srs review, 1=passage
# mcq, 2=discrete mcq. Exam-style = anything != 0. (Discrete mcq is unused here.)
SRS, PASSAGE_MCQ = 0, 1

# Give-up-rule gate thresholds, mirrored so this harness stays in sync if the
# rslib gates move. MIN_REVIEW_CARDS / MIN_EXAM_ATTEMPTS / MIN_GRADED_ATTEMPTS
# mirror rslib/src/speedrun/readiness.rs; MIN_EVAL_CARDS mirrors MIN_CARDS_FOR_GAP
# in rslib/src/speedrun/performance.rs (cards needed before the recall-vs-
# performance gap is meaningful).
MIN_REVIEW_CARDS = 20
MIN_EXAM_ATTEMPTS = 20
MIN_GRADED_ATTEMPTS = 30
MIN_EVAL_CARDS = 5

# Maturation schedule for _mature_cards: after graduating new cards, advance the
# clock _DAYS_PER_ROUND days and re-review, repeated _MATURE_ROUNDS times.
_MATURE_ROUNDS = 5
_DAYS_PER_ROUND = 25

# Collection-config toggles for the two study features (rslib config/bool.rs,
# camelCase serialization of SpeedrunPointsAtStake / SpeedrunInterleaveTopics).
# The GetReviewOrder RPC carries only a deck id; these flags live in config.
_POINTS_AT_STAKE = "speedrunPointsAtStake"
_INTERLEAVE_TOPICS = "speedrunInterleaveTopics"

# The committed 31-category AAMC content outline (ids 1A..10A grouped into the
# 10 Foundational Concepts FC1..FC10). Same data the coverage harness loads.
_OUTLINE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "speedrun_mcat_outline.json"
)

# The open-licensed content library (real per-topic cards + questions), so the
# e2e deck is realistic MCAT content across every category rather than
# placeholder strings. Built by tools/build_content_library.py.
_CONTENT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "qt",
    "aqt",
    "data",
    "web",
    "imgs",
    "speedrun_content_library.json",
)

# Cover ALL 31 content categories with several real cards each, so coverage is
# full and every concept is exercised (up from a 20-topic placeholder deck).
_TOPICS_TO_COVER = 31
_CARDS_PER_TOPIC = 4


def _load_content() -> dict:
    """The per-topic content library keyed by content-category id (1A..10A), or
    {} if it isn't built yet (the harness then falls back to placeholder text)."""
    try:
        with open(_CONTENT_PATH, encoding="utf-8") as f:
            return json.load(f).get("topics", {})
    except OSError:
        return {}


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
    # Prefer the system temp dir; fall back to a workspace-local dir if the
    # sandbox blocks it.
    try:
        fd, path = tempfile.mkstemp(suffix=".anki2")
    except OSError:
        fd, path = tempfile.mkstemp(suffix=".anki2", dir="out")
    os.close(fd)
    os.unlink(path)  # Collection() creates it fresh
    return Collection(path)


def _advance_days(col: Collection, days: int) -> None:
    """Move the collection's day counter forward by `days` without waiting real
    time, by backdating its creation stamp. `col.sched.today` is derived from
    the backend's sched_timing_today (crt vs now), so an earlier crt means more
    elapsed days.

    The backend caches the day count in `state.scheduler_info` (keyed on the
    next-rollover time), and a raw `crt` write does not invalidate that cache.
    Closing and reopening the collection rebuilds the in-memory state from the
    persisted (now-backdated) crt, so the recomputed `today` reflects the shift.
    All data (cards, attempts, topic map, config) lives in the .anki2 file and
    survives the round-trip."""
    col.crt = col.crt - days * 86400
    col.close()
    col.reopen()


def _lift_daily_limits(col: Collection, did: int) -> None:
    """Raise the deck's new/review per-day caps so the maturation loop can flow
    every card through the real scheduler in a single day."""
    conf = col.decks.config_dict_for_deck_id(did)  # type: ignore[arg-type]
    conf["new"]["perDay"] = 9999
    conf["rev"]["perDay"] = 9999
    col.decks.update_config(conf)


def _load_outline(col: Collection) -> tuple[list[dict], dict[str, str], int]:
    """Load the real 31-category outline into the engine via SetTopicMap.

    The topic keys are made hierarchical (`<concept>::<id>`, e.g. `FC1::1A`) so
    the interleave feature can recognise confusable siblings under a shared
    concept, while still uniquely identifying every AAMC content category. The
    weights come straight from the outline, so weighted coverage is unchanged.
    Returns the raw topics, the id->key map, and the number of topics stored."""
    with open(_OUTLINE_PATH, encoding="utf-8") as f:
        outline = json.load(f)
    topics = outline["topics"]
    key_by_id = {t["id"]: f"{t['concept']}::{t['id']}" for t in topics}
    entries = [
        speedrun_pb2.TopicMapEntry(
            topic=key_by_id[t["id"]], label=t["name"], weight=float(t["weight"])
        )
        for t in topics
    ]
    loaded = col._backend.set_topic_map(entries)
    return topics, key_by_id, loaded


def _build_deck(
    col: Collection,
    topics: list[dict],
    key_by_id: dict[str, str],
    did: int,
    content: dict,
) -> dict[str, list[tuple[int, int]]]:
    """Create a realistic deck: `_CARDS_PER_TOPIC` cards for each of the
    `_TOPICS_TO_COVER` highest-weight outline topics (ties broken by id). With
    all 31 covered, raw and weighted coverage both reach 100%, and covering
    several sibling topics per concept gives the interleave feature confusable
    groups to reorder. Card text is pulled from the open-licensed content library
    when available (real MCAT content per category), falling back to placeholder
    text otherwise. Each note is tagged with its hierarchical topic key so
    coverage and topic-grouping both see it."""
    chosen = sorted(topics, key=lambda t: (-float(t["weight"]), t["id"]))[
        :_TOPICS_TO_COVER
    ]
    model = col.models.by_name("Basic")
    by_topic: dict[str, list[tuple[int, int]]] = {}
    for t in chosen:
        key = key_by_id[t["id"]]
        by_topic[key] = []
        cards = content.get(t["id"], {}).get("cards", [])
        for i in range(_CARDS_PER_TOPIC):
            note = col.new_note(model)
            if i < len(cards):
                note["Front"] = cards[i].get("front", f"{t['id']} fact {i}")
                note["Back"] = cards[i].get("back", "the answer")
            else:
                note["Front"] = f"{t['id']} fact {i}"
                note["Back"] = "the answer"
            note.tags = [key]
            col.add_note(note, did)  # type: ignore[arg-type]
            card = note.cards()[0]
            by_topic[key].append((card.id, note.id))
    return by_topic


def _mature_cards(col: Collection) -> None:
    """Drive every card through the REAL v3 scheduler until it is a mature review
    card (interval >= 21 days). New cards are graduated with Easy, then the clock
    is advanced and the due reviews answered Good so their intervals compound
    past the mature line - exactly what a studying human would produce, just
    without waiting the calendar days."""
    # Graduate all new cards (Easy jumps straight to a review card).
    while True:
        card = col.sched.getCard()
        if card is None:
            break
        col.sched.answerCard(card, 4)
    # Age forward and re-review, letting Good grow each interval each pass.
    # _MATURE_ROUNDS * _DAYS_PER_ROUND days of compounding reviews comfortably clears the 21-day mature line.
    for _ in range(_MATURE_ROUNDS):
        _advance_days(col, _DAYS_PER_ROUND)
        while True:
            card = col.sched.getCard()
            if card is None:
                break
            col.sched.answerCard(card, 3)


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
    """Record one classified attempt through the SpeedrunService, mirroring the
    helper in tools/speedrun_e2e.py."""
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
        session_id="e2e-full",
        answered_at_ms=1_700_000_000_000,
        took_ms=took_ms,
        question_type=question_type,
        correct=correct,
        signals=signals,
        predicted=predicted,
        data="{}",
    )
    return col._backend.record_attempt(req)


def _review_order(
    col: Collection, did: int, *, points_at_stake: bool, interleave: bool
) -> list[int]:
    """Return the deck's review-card study order under the given feature toggles.
    The toggles live in collection config (not the request message), so they are
    set before the GetReviewOrder RPC, matching the queue builder's gates."""
    col.set_config(_POINTS_AT_STAKE, points_at_stake)
    col.set_config(_INTERLEAVE_TOPICS, interleave)
    return list(col._backend.get_review_order(deck_id=did))


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
            str(snap.readiness_scaled),
        )

        print("\n[2] The real 31-category AAMC outline + a realistic deck -> coverage")
        did = col.decks.id("Default")
        _lift_daily_limits(col, did)
        topics, key_by_id, loaded = _load_outline(col)
        check.ok(
            "SetTopicMap loaded all 31 content categories",
            loaded == 31,
            f"{loaded} topics",
        )
        content = _load_content()
        by_topic = _build_deck(col, topics, key_by_id, did, content)
        cov = col._backend.get_coverage_report()
        check.ok(
            "every content category is covered",
            cov.coverage >= 0.99,
            f"{cov.topics_covered}/{cov.topics_total} = {cov.coverage:.0%}",
        )
        check.ok(
            "weighted coverage is full too",
            cov.weighted_coverage >= 0.99,
            f"{cov.weighted_coverage:.0%}",
        )

        print(
            "\n[3] Mature >= 20 cards through the real scheduler with the clock advanced"
        )
        before_snap = col._backend.compute_readiness()
        check.ok(
            "before study, readiness abstains on review cards",
            "review cards" in before_snap.reason,
            before_snap.reason,
        )
        _mature_cards(col)
        snap = col._backend.compute_readiness()
        check.ok(
            "after study, the abstain reason no longer lists review cards",
            "review cards" not in snap.reason,
            snap.reason,
        )

        print(
            "\n[4] Record >= 30 graded and >= 20 exam-style attempts (with predictions)"
        )
        pairs = [(cid, nid) for cards in by_topic.values() for (cid, nid) in cards]
        # Held-out questions for a dozen source cards (MIN_EVAL_CARDS needed for the
        # gap), drawn from the real content library when available.
        held_qs = [q for t in content.values() for q in t.get("questions", [])]
        fallback = {"stem": "q", "options": ["a", "b", "c", "d"], "correct_index": 0}
        for i, (cid, _nid) in enumerate(pairs[:12]):
            q = held_qs[i] if i < len(held_qs) else fallback
            col._backend.add_question_item(
                speedrun_pb2.QuestionItem(
                    card_id=cid,
                    topic="held-out",
                    provenance=1,
                    payload=json.dumps(
                        {
                            "stem": q.get("stem", "q"),
                            "options": q.get("options", ["a", "b", "c", "d"]),
                            "correct_index": q.get("correct_index", 0),
                        }
                    ),
                )
            )
        exam = 0
        for idx, (cid, nid) in enumerate(pairs[:24]):
            correct = idx % 3 != 0  # ~2/3 correct, and a repeatable weakness pattern
            _attempt(
                col,
                card_id=cid,
                note_id=nid,
                correct=correct,
                question_type=PASSAGE_MCQ,
                took_ms=7000,
                predicted=0.75 if correct else 0.45,
            )
            exam += 1
        graded = exam
        for cid, nid in pairs[:12]:
            _attempt(
                col,
                card_id=cid,
                note_id=nid,
                correct=True,
                question_type=SRS,
                predicted=0.8,
            )
            graded += 1
        perf = col._backend.get_performance_report()
        check.ok(
            f"exam-style attempts recorded (>= {MIN_EXAM_ATTEMPTS})",
            perf.exam_attempts >= MIN_EXAM_ATTEMPTS,
            str(perf.exam_attempts),
        )
        check.ok(
            f"graded attempts recorded (>= {MIN_GRADED_ATTEMPTS})",
            graded >= MIN_GRADED_ATTEMPTS,
            str(graded),
        )

        print("\n[5] Readiness now commits to a real, in-range score")
        snap = col._backend.compute_readiness()
        check.ok("readiness is now sufficient", snap.sufficient, snap.reason)
        check.ok(
            "score is on the MCAT scale",
            472 <= snap.readiness_scaled <= 528,
            str(snap.readiness_scaled),
        )
        check.ok(
            "likely range brackets the score",
            snap.low_scaled <= snap.readiness_scaled <= snap.high_scaled,
            f"{snap.low_scaled}-{snap.high_scaled}",
        )

        print("\n[6] Calibration + exam plan + performance gap all compute")
        cal = col._backend.get_calibration_report()
        check.ok("calibration computed over predictions", cal.n > 0, f"n={cal.n}")
        col._backend.set_exam_profile(
            speedrun_pb2.ExamProfile(exam_date_ms=1_800_000_000_000, target_score=508)
        )
        plan = col._backend.get_exam_plan()
        check.ok("exam plan has a profile", plan.has_profile)
        check.ok(
            "exam plan yields a study mode", bool(plan.study_mode), plan.study_mode
        )
        perf = col._backend.get_performance_report()
        check.ok(
            f"performance gap is sufficient (>= {MIN_EVAL_CARDS} cards, >= {MIN_EXAM_ATTEMPTS} attempts)",
            perf.sufficient,
            perf.note,
        )

        print("\n[7] The study features each reorder the review queue")
        # Make every matured review card due so the queue is fully populated.
        _advance_days(col, 400)
        default = _review_order(col, did, points_at_stake=False, interleave=False)
        check.ok(
            "the default review queue has the matured cards",
            len(default) >= MIN_REVIEW_CARDS,
            f"{len(default)} cards",
        )
        pas = _review_order(col, did, points_at_stake=True, interleave=False)
        check.ok(
            "points-at-stake reorders vs default",
            pas != default and sorted(pas) == sorted(default),
            f"{sum(1 for a, b in zip(default, pas) if a != b)} positions moved",
        )
        il = _review_order(col, did, points_at_stake=False, interleave=True)
        check.ok(
            "interleave reorders vs default",
            il != default and sorted(il) == sorted(default),
            f"{sum(1 for a, b in zip(default, il) if a != b)} positions moved",
        )

        print(f"\nspeedrun e2e full: PASS ({check.n} checks)")
        return 0
    finally:
        col_path = col.path
        if col.db is not None:
            col.close()
        # Delete the temp collection so no stray files are left behind (notably
        # for the in-repo out/ fallback path): the .anki2 file plus the sibling
        # media folder and media db Anki derives from it. Guarded by existence.
        media_dir, media_db = media_paths_from_col_path(col_path)
        for stray in (col_path, media_db):
            if os.path.exists(stray):
                os.unlink(stray)
        if os.path.isdir(media_dir):
            shutil.rmtree(media_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
