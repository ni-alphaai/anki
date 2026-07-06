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

    # A topic needs at least MIN_CARDS_PER_TOPIC (3) tagged cards before it counts
    # as covered, so a lone incidental card can't light up a whole topic. Two
    # cards stays below the bar; the third clears it.
    model = col.models.by_name("Basic")

    def _add_fc1_card(front: str) -> None:
        note = col.new_note(model)
        note["Front"] = front
        note.tags = ["fc1"]
        col.add_note(note, col.decks.id("Default"))

    _add_fc1_card("an amino acid fact")
    _add_fc1_card("another amino acid fact")
    assert col._backend.get_coverage_report().topics_covered == 0

    _add_fc1_card("a third amino acid fact")
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
            speedrun_pb2.QuestionItem(
                topic="biology", provenance=1, payload=f'{{"n":{i}}}'
            )
        )
    for i in range(3):
        col._backend.add_question_item(
            speedrun_pb2.QuestionItem(
                topic="physics", provenance=1, payload=f'{{"n":{i}}}'
            )
        )

    # any topic, generous limit -> the whole bank
    assert len(col._backend.get_practice_questions(limit=100, topic="")) == 8
    # limit caps the batch
    assert len(col._backend.get_practice_questions(limit=3, topic="")) == 3
    # topic filter
    bio = col._backend.get_practice_questions(limit=100, topic="biology")
    assert len(bio) == 5
    assert all(q.topic == "biology" for q in bio)


def test_session_reasoning_round_weaves_memory_and_reasoning():
    col = getEmptyCol()

    # a card in a deck whose name maps to the "biology" topic
    did = col.decks.id("Biology")
    model = col.models.by_name("Basic")
    note = col.new_note(model)
    note["Front"] = "a cell fact"
    col.add_note(note, did)
    card_id = note.cards()[0].id

    # tier 1: a held-out question linked to the exact card just reviewed
    col._backend.add_question_item(
        speedrun_pb2.QuestionItem(
            card_id=card_id, topic="biology", payload='{"k":"cl"}'
        )
    )
    # tier 2: a biology question reachable only via the deck-name -> topic map
    col._backend.add_question_item(
        speedrun_pb2.QuestionItem(topic="biology", payload='{"k":"bio"}')
    )
    # only reachable as the last-resort fallback
    col._backend.add_question_item(
        speedrun_pb2.QuestionItem(topic="physics", payload='{"k":"phys"}')
    )

    # full round: all three, with the card-linked question first
    full = col._backend.get_session_reasoning_round(
        reviewed_card_ids=[card_id], limit=5
    )
    payloads = [q.payload for q in full]
    assert len(full) == 3
    assert payloads[0] == '{"k":"cl"}'
    assert set(payloads) == {'{"k":"cl"}', '{"k":"bio"}', '{"k":"phys"}'}

    # capped at 2: card-linked + deck-topic-matched fill the round before the
    # physics fallback is ever consulted (proves the deck-name -> topic path)
    capped = col._backend.get_session_reasoning_round(
        reviewed_card_ids=[card_id], limit=2
    )
    assert [q.payload for q in capped] == ['{"k":"cl"}', '{"k":"bio"}']

    # an empty session still returns a round from the general bank (never errors)
    assert (
        len(col._backend.get_session_reasoning_round(reviewed_card_ids=[], limit=5))
        == 3
    )


def test_due_reasoning_and_feedback_report():
    col = getEmptyCol()

    # a card in a deck whose name maps to the "biology" topic
    did = col.decks.id("Biology")
    model = col.models.by_name("Basic")
    note = col.new_note(model)
    note["Front"] = "a cell fact"
    col.add_note(note, did)
    card_id = note.cards()[0].id

    # two held-out biology questions to schedule
    for i in range(2):
        col._backend.add_question_item(
            speedrun_pb2.QuestionItem(topic="biology", payload='{"k":"q%d"}' % i)
        )

    # two exam-style attempts on the card: one correct, one wrong (reasoning)
    for correct in (True, False):
        col._backend.record_attempt(
            speedrun_pb2.RecordAttemptRequest(
                card_id=card_id,
                note_id=note.id,
                question_type=1,
                took_ms=12000,
                correct=correct,
                data="{}",
            )
        )

    # feedback report: attributed to "biology" via the deck-name heuristic
    report = col._backend.get_feedback_report()
    assert report.total == 2
    assert report.correct == 1
    assert report.reasoning_misses == 1
    assert list(report.weak_topics) == ["biology"]

    # due-reasoning: "biology" is uncovered (not in the outline) so it is due,
    # returning its held-out questions
    due = col._backend.get_due_reasoning(limit=5)
    assert len(due) == 2
    assert all(q.topic == "biology" for q in due)


def test_d7_withhold_is_client_reveal_only_not_a_data_change():
    """D7 mechanism test: the delayed-feedback experiment withholds *only the
    immediate client reveal* of correctness. The attempt is still recorded with
    its true correctness and is still surfaced by get_feedback_report (the
    delayed surface), so no learning data is lost or altered.

    Honest framing: skill-gated feedback *timing* is NOT evidence-established
    (see feedback.rs). This test asserts the mechanism (reveal-only), not any
    learning-outcome benefit; establishing that would need a human A/B.
    """

    # The client's withhold gate (mirrors aqt.speedrun._should_withhold_feedback
    # and the engine's should_withhold_correctness): only when the experiment is
    # ON *and* the student is proficient (>= 0.8).
    def would_withhold(performance: float, enabled: bool) -> bool:
        return enabled and performance >= 0.8

    assert would_withhold(0.9, enabled=True) is True  # proficient + on -> withheld
    assert would_withhold(0.9, enabled=False) is False  # experiment off
    assert would_withhold(0.5, enabled=True) is False  # novice always gets it

    col = getEmptyCol()
    did = col.decks.id("Biology")
    model = col.models.by_name("Basic")
    note = col.new_note(model)
    note["Front"] = "a cell fact"
    col.add_note(note, did)
    card_id = note.cards()[0].id

    # A proficient student answers reasoning questions; the client would WITHHOLD
    # the immediate correctness reveal. The desktop records the attempt BEFORE it
    # mutes the reveal, so we record exactly what it records: the true outcomes.
    outcomes = [True, True, True, True, False]  # 80% -> proficient, one miss
    for i, correct in enumerate(outcomes):
        col._backend.record_attempt(
            speedrun_pb2.RecordAttemptRequest(
                card_id=card_id,
                note_id=note.id,
                session_id="d7",
                answered_at_ms=1_700_000_000_000 + i,
                question_type=1,
                took_ms=12000,
                correct=correct,
                data="{}",
            )
        )

    # 1) Every attempt persisted with its TRUE correctness, even the ones whose
    #    correctness was withheld from the immediate client display.
    attempts = col._backend.get_attempts_for_card(card_id=card_id)
    assert len(attempts) == len(outcomes)
    assert [a.correct for a in attempts] == outcomes

    # 2) The delayed surface (feedback report) reveals the withheld correctness:
    #    the miss is counted and the topic surfaces as weak. Nothing is lost.
    report = col._backend.get_feedback_report()
    assert report.total == len(outcomes)
    assert report.correct == 4
    assert report.reasoning_misses == 1
    assert list(report.weak_topics) == ["biology"]

    # 3) The recorded performance rate is the true 80% - the withhold decision is
    #    downstream of (and cannot alter) this recorded evidence.
    perf = col._backend.get_performance_report().performance_rate
    assert abs(perf - 0.8) < 1e-6
    assert would_withhold(perf, enabled=True) is True
