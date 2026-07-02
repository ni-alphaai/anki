# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Integration test: call Speedrun's Rust backend service from Python.

This exercises the new SpeedrunService end to end through the protobuf bridge
(record an attempt, have it classified deterministically, persist it, and read
it back), proving the Rust change is reachable from Python.
"""

from anki import speedrun_pb2
from tests.shared import getEmptyCol


def test_record_attempt_classifies_and_persists():
    col = getEmptyCol()

    req = speedrun_pb2.RecordAttemptRequest(
        card_id=111,
        note_id=222,
        session_id="s1",
        answered_at_ms=1_700_000_000_000,
        took_ms=3000,
        question_type=1,
        selected=1,
        correct=False,
        signals=speedrun_pb2.ClassifyAttemptRequest(
            correct=False,
            took_ms=3000,
            recall_failed=True,
            passage_evidence_missed=False,
            question_type=1,
        ),
        data="{}",
    )
    resp = col._backend.record_attempt(req)

    # recall_failed -> memory gap (1), routed to resurface (1)
    assert resp.diagnosis.kind == 1
    assert resp.diagnosis.routed_action == 1

    # the generated API flattens the single-field request/response: pass the
    # card id directly and receive the attempts list
    attempts = col._backend.get_attempts_for_card(card_id=111)
    assert len(attempts) == 1
    assert attempts[0].diagnosis_kind == 1
    assert attempts[0].correct is False


def test_classify_attempt_deterministic():
    col = getEmptyCol()
    # a correct answer classifies as "correct" (5) regardless of other signals
    diagnosis = col._backend.classify_attempt(
        correct=True,
        took_ms=1000,
        recall_failed=False,
        passage_evidence_missed=False,
        question_type=1,
    )
    assert diagnosis.kind == 5


def test_readiness_abstains_on_empty_collection():
    col = getEmptyCol()
    # With no evidence, readiness must abstain rather than invent a number.
    snapshot = col._backend.compute_readiness()
    assert snapshot.sufficient is False
    assert "not enough evidence" in snapshot.reason
    # readiness stays on the MCAT scale even while abstaining
    assert 472 <= snapshot.readiness_scaled <= 528
    # per-dimension abstain: empty collection blocks on the memory dimension
    assert snapshot.memory_sufficient is False
    assert snapshot.blocking_dimension == "memory"

    # the snapshot is cached and returned by GetReadinessSnapshot
    cached = col._backend.get_readiness_snapshot()
    assert cached.computed_at_ms == snapshot.computed_at_ms
    assert cached.sufficient is False


def test_question_items_and_performance_report():
    col = getEmptyCol()

    # register a held-out paraphrase question for card 500
    item_id = col._backend.add_question_item(
        speedrun_pb2.QuestionItem(
            card_id=500,
            topic="amino acids",
            provenance=0,
            payload='{"stem": "reworded"}',
        )
    )
    assert item_id > 0

    items = col._backend.get_question_items_for_card(card_id=500)
    assert len(items) == 1
    assert items[0].topic == "amino acids"

    # answer the paraphrased question twice: one correct, one wrong
    for i, correct in [(1, True), (2, False)]:
        col._backend.record_attempt(
            speedrun_pb2.RecordAttemptRequest(
                card_id=500,
                note_id=1,
                session_id="s",
                answered_at_ms=i,
                took_ms=5000,
                question_type=1,
                correct=correct,
            )
        )

    report = col._backend.get_performance_report()
    assert report.cards_evaluated == 1
    assert report.exam_attempts == 2
    assert abs(report.performance_rate - 0.5) < 1e-6
    assert report.question_items == 1
    # only one card -> below the gap threshold, so it abstains
    assert report.sufficient is False


def test_topic_outline_and_coverage():
    col = getEmptyCol()

    # load the built-in starter outline
    assert col._backend.seed_mcat_topic_outline() == 10
    assert len(col._backend.get_topic_map()) == 10

    report = col._backend.get_coverage_report()
    assert report.topics_total == 10
    assert report.topics_covered == 0

    # add a note tagged with topic "fc1"
    model = col.models.by_name("Basic")
    note = col.new_note(model)
    note["Front"] = "an amino acid fact"
    note.tags = ["fc1"]
    col.add_note(note, col.decks.id("Default"))

    report = col._backend.get_coverage_report()
    assert report.topics_covered == 1
    assert abs(report.coverage - 0.1) < 1e-6


def test_calibration_over_predictions():
    col = getEmptyCol()
    # two perfectly-calibrated predictions
    for predicted, correct in [(1.0, True), (0.0, False)]:
        col._backend.record_attempt(
            speedrun_pb2.RecordAttemptRequest(
                card_id=7,
                note_id=1,
                question_type=0,
                correct=correct,
                predicted=predicted,
            )
        )
    report = col._backend.get_calibration_report()
    assert report.n == 2
    assert abs(report.brier) < 1e-6
    assert report.sufficient is False  # below the minimum-predictions threshold


def test_exam_profile_and_plan():
    import time

    col = getEmptyCol()
    # no profile yet -> long-term default
    plan = col._backend.get_exam_plan()
    assert plan.has_profile is False
    assert plan.study_mode == "long_term"

    # set an exam ~14 days out with a top-third target
    exam_ms = int(time.time() * 1000) + 14 * 86_400_000
    stored = col._backend.set_exam_profile(
        speedrun_pb2.ExamProfile(exam_date_ms=exam_ms, target_score=510)
    )
    assert stored.target_score == 510
    assert col._backend.get_exam_profile().target_score == 510

    plan = col._backend.get_exam_plan()
    assert plan.has_profile is True
    assert plan.target_score == 510
    assert plan.needed_points > 0
    assert plan.study_mode == "consolidation"


def test_routed_practice_and_diagnosis_correction():
    col = getEmptyCol()

    # concept-linked practice returns held-out items for a topic
    col._backend.add_question_item(speedrun_pb2.QuestionItem(topic="fc1", payload="{}"))
    col._backend.add_question_item(speedrun_pb2.QuestionItem(topic="fc2", payload="{}"))
    practice = col._backend.get_routed_practice(topic="fc1")
    assert len(practice) == 1
    assert practice[0].topic == "fc1"

    # record a miss, then correct the diagnosis + advance the action status
    resp = col._backend.record_attempt(
        speedrun_pb2.RecordAttemptRequest(
            card_id=7, note_id=1, question_type=1, correct=False
        )
    )
    col._backend.update_attempt_diagnosis(
        attempt_id=resp.id, diagnosis_kind=3, routed_action=2
    )
    col._backend.set_action_status(attempt_id=resp.id, action_status=1)

    attempts = col._backend.get_attempts_for_card(card_id=7)
    assert attempts[0].diagnosis_kind == 3
    assert attempts[0].routed_action == 2


def test_get_practice_questions_batch():
    col = getEmptyCol()

    for i in range(5):
        col._backend.add_question_item(
            speedrun_pb2.QuestionItem(topic="biology", provenance=1, payload=f'{{"n":{i}}}')
        )
    for i in range(3):
        col._backend.add_question_item(
            speedrun_pb2.QuestionItem(topic="physics", provenance=1, payload=f'{{"n":{i}}}')
        )

    # any topic, generous limit -> the whole bank
    assert len(col._backend.get_practice_questions(limit=100, topic="")) == 8
    # limit caps the batch
    assert len(col._backend.get_practice_questions(limit=3, topic="")) == 3
    # topic filter
    bio = col._backend.get_practice_questions(limit=100, topic="biology")
    assert len(bio) == 5
    assert all(q.topic == "biology" for q in bio)
