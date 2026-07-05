// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

use fsrs::FSRS;
use fsrs::FSRS5_DEFAULT_DECAY;

use crate::collection::Collection;
use crate::error;
use crate::prelude::*;
use crate::speedrun::calibration::compute_calibration;
use crate::speedrun::calibration::CalibrationPair;
use crate::speedrun::classify;
use crate::speedrun::coverage::summarize_coverage;
use crate::speedrun::coverage::TopicCoverageRow;
use crate::speedrun::coverage::MCAT_FOUNDATIONAL_CONCEPTS;
use crate::speedrun::exam::compute_exam_plan;
use crate::speedrun::exam::ExamPlanInputs;
use crate::speedrun::performance::summarize_performance;
use crate::speedrun::performance::PerfCardRow;
use crate::speedrun::readiness::compute_readiness;
use crate::speedrun::readiness::ReadinessInputs;
use crate::speedrun::readiness::ReadinessReport;
use crate::speedrun::AttemptSignals;
use crate::speedrun::Diagnosis;
use crate::storage::card::data::CardData;
use crate::storage::SrAttempt;
use crate::storage::SrProfile;
use crate::storage::SrQuestionItem;
use crate::storage::SrReadiness;
use crate::storage::SrTopicMapEntry;

const CFG_TOPIC_MAP: &str = "speedrunTopicMap";
const CFG_QUESTION_ITEMS: &str = "speedrunQuestionItems";
const CFG_EXAM_PROFILE: &str = "speedrunExamProfile";

impl crate::services::SpeedrunService for Collection {
    fn classify_attempt(
        &mut self,
        input: anki_proto::speedrun::ClassifyAttemptRequest,
    ) -> error::Result<anki_proto::speedrun::Diagnosis> {
        let diagnosis = classify(&AttemptSignals {
            correct: input.correct,
            took_ms: input.took_ms,
            recall_failed: input.recall_failed,
            passage_evidence_missed: input.passage_evidence_missed,
            question_type: input.question_type as u8,
            confidence: 0.0,
        });
        Ok(to_proto_diagnosis(diagnosis))
    }

    fn record_attempt(
        &mut self,
        input: anki_proto::speedrun::RecordAttemptRequest,
    ) -> error::Result<anki_proto::speedrun::RecordAttemptResponse> {
        let signals = input.signals.unwrap_or_default();
        let diagnosis = classify(&AttemptSignals {
            correct: input.correct,
            took_ms: input.took_ms,
            recall_failed: signals.recall_failed,
            passage_evidence_missed: signals.passage_evidence_missed,
            question_type: input.question_type as u8,
            // The student's pre-answer confidence (0 when they skipped it), used
            // to tell a confident careless miss (test-taking) from a reasoning gap.
            confidence: input.predicted.unwrap_or(0.0),
        });

        // For SRS reviews, auto-capture the model's predicted recall from the
        // card's FSRS state when the caller didn't supply one, so calibration
        // has real data without any UI work.
        let predicted = match input.predicted {
            Some(p) => Some(p),
            None if input.question_type == 0 => {
                self.predicted_recall_for_card(CardId(input.card_id))?
            }
            None => None,
        };

        let attempt = SrAttempt {
            // 0 => storage assigns a unique id (avoids same-millisecond collisions)
            id: 0,
            cid: CardId(input.card_id),
            nid: NoteId(input.note_id),
            session_id: input.session_id,
            answered_at_ms: input.answered_at_ms,
            took_ms: input.took_ms as i64,
            question_type: input.question_type as u8,
            selected: input.selected.map(|v| v as i64),
            correct: input.correct,
            diagnosis_kind: diagnosis.kind,
            diagnosis_confidence: diagnosis.confidence,
            routed_action: diagnosis.routed_action,
            action_status: 0,
            usn: Usn(-1),
            data: input.data,
            predicted,
        };
        let id = self.storage.add_sr_attempt(&attempt)?;

        Ok(anki_proto::speedrun::RecordAttemptResponse {
            id,
            diagnosis: Some(to_proto_diagnosis(diagnosis)),
        })
    }

    fn get_attempts_for_card(
        &mut self,
        input: anki_proto::speedrun::GetAttemptsForCardRequest,
    ) -> error::Result<anki_proto::speedrun::SrAttempts> {
        let attempts = self.storage.sr_attempts_for_card(CardId(input.card_id))?;
        Ok(anki_proto::speedrun::SrAttempts {
            attempts: attempts.into_iter().map(to_proto_attempt).collect(),
        })
    }

    fn compute_readiness(&mut self) -> error::Result<anki_proto::speedrun::ReadinessSnapshot> {
        let report = self.readiness_report()?;
        let id = TimestampMillis::now().0;
        let snapshot = SrReadiness {
            id,
            computed_at_ms: id,
            memory: report.memory,
            performance: report.performance,
            recall_perf_gap: report.recall_perf_gap,
            coverage: report.coverage,
            readiness_scaled: report.readiness_scaled,
            low_scaled: report.low_scaled,
            high_scaled: report.high_scaled,
            sufficient: report.sufficient,
            reason: report.reason,
            memory_sufficient: report.memory_sufficient,
            performance_sufficient: report.performance_sufficient,
            blocking_dimension: report.blocking_dimension,
        };
        self.storage.add_sr_readiness(&snapshot)?;
        Ok(to_proto_readiness(&snapshot))
    }

    fn get_readiness_snapshot(&mut self) -> error::Result<anki_proto::speedrun::ReadinessSnapshot> {
        match self.storage.get_latest_sr_readiness()? {
            Some(snapshot) => Ok(to_proto_readiness(&snapshot)),
            None => self.compute_readiness(),
        }
    }

    fn add_question_item(
        &mut self,
        input: anki_proto::speedrun::QuestionItem,
    ) -> error::Result<anki_proto::speedrun::QuestionItemId> {
        self.apply_question_items_from_config()?;
        let item = SrQuestionItem {
            id: input.id,
            cid: (input.card_id != 0).then_some(input.card_id),
            topic: input.topic,
            provenance: input.provenance as u8,
            payload: input.payload,
        };
        let id = self.storage.add_sr_question_item(&item)?;
        self.persist_question_items_config()?;
        Ok(anki_proto::speedrun::QuestionItemId { id })
    }

    fn get_question_items_for_card(
        &mut self,
        input: anki_proto::speedrun::GetQuestionItemsForCardRequest,
    ) -> error::Result<anki_proto::speedrun::QuestionItems> {
        self.apply_question_items_from_config()?;
        let items = self
            .storage
            .get_sr_question_items_for_card(CardId(input.card_id))?;
        Ok(anki_proto::speedrun::QuestionItems {
            items: items.into_iter().map(to_proto_question_item).collect(),
        })
    }

    fn get_performance_report(&mut self) -> error::Result<anki_proto::speedrun::PerformanceReport> {
        self.apply_question_items_from_config()?;
        let rows: Vec<PerfCardRow> = self
            .storage
            .sr_performance_rows()?
            .into_iter()
            .map(|(_cid, attempts, correct, ivl)| PerfCardRow {
                attempts,
                correct,
                interval_days: ivl,
            })
            .collect();
        let summary = summarize_performance(&rows);
        let question_items = self.storage.sr_question_item_count()?;
        Ok(anki_proto::speedrun::PerformanceReport {
            cards_evaluated: summary.cards_evaluated,
            exam_attempts: summary.exam_attempts,
            recall_rate: summary.recall_rate,
            performance_rate: summary.performance_rate,
            recall_perf_gap: summary.recall_perf_gap,
            sufficient: summary.sufficient,
            note: summary.note,
            question_items,
        })
    }

    fn set_topic_map(
        &mut self,
        input: anki_proto::speedrun::TopicMap,
    ) -> error::Result<anki_proto::speedrun::SetTopicMapResponse> {
        let entries: Vec<SrTopicMapEntry> = input
            .entries
            .into_iter()
            .map(|e| SrTopicMapEntry {
                topic: e.topic,
                label: e.label,
                weight: e.weight,
            })
            .collect();
        let topics = self.storage.replace_sr_topic_map(&entries)?;
        self.persist_topic_map_config(&entries)?;
        Ok(anki_proto::speedrun::SetTopicMapResponse { topics })
    }

    fn get_topic_map(&mut self) -> error::Result<anki_proto::speedrun::TopicMap> {
        let entries = self
            .storage
            .get_sr_topic_map()?
            .into_iter()
            .map(|e| anki_proto::speedrun::TopicMapEntry {
                topic: e.topic,
                label: e.label,
                weight: e.weight,
            })
            .collect();
        Ok(anki_proto::speedrun::TopicMap { entries })
    }

    fn seed_mcat_topic_outline(
        &mut self,
    ) -> error::Result<anki_proto::speedrun::SetTopicMapResponse> {
        let entries: Vec<SrTopicMapEntry> = MCAT_FOUNDATIONAL_CONCEPTS
            .iter()
            .map(|(topic, label, weight)| SrTopicMapEntry {
                topic: (*topic).to_string(),
                label: (*label).to_string(),
                weight: *weight,
            })
            .collect();
        let topics = self.storage.replace_sr_topic_map(&entries)?;
        self.persist_topic_map_config(&entries)?;
        Ok(anki_proto::speedrun::SetTopicMapResponse { topics })
    }

    fn get_coverage_report(&mut self) -> error::Result<anki_proto::speedrun::CoverageReport> {
        let detail = self.storage.sr_topic_coverage_detail()?;
        let rows: Vec<TopicCoverageRow> = detail
            .iter()
            .map(|(_, _, weight, cards)| TopicCoverageRow {
                weight: *weight,
                cards: *cards,
            })
            .collect();
        let summary = summarize_coverage(&rows);
        let topics = detail
            .into_iter()
            .map(
                |(topic, label, weight, cards)| anki_proto::speedrun::TopicCoverage {
                    topic,
                    label,
                    weight,
                    cards,
                    covered: cards > 0,
                },
            )
            .collect();
        Ok(anki_proto::speedrun::CoverageReport {
            topics_total: summary.topics_total,
            topics_covered: summary.topics_covered,
            coverage: summary.coverage,
            weighted_coverage: summary.weighted_coverage,
            topics,
        })
    }

    fn get_topic_signals(&mut self) -> error::Result<anki_proto::speedrun::TopicSignalsReport> {
        let topics = self
            .storage
            .sr_topic_signals()?
            .into_iter()
            .map(
                |(
                    topic,
                    label,
                    weight,
                    cards,
                    review_cards,
                    mature_cards,
                    exam_attempts,
                    exam_correct,
                )| anki_proto::speedrun::TopicSignal {
                    topic,
                    label,
                    weight,
                    cards,
                    covered: cards > 0,
                    review_cards,
                    mature_cards,
                    exam_attempts,
                    exam_correct,
                },
            )
            .collect();
        Ok(anki_proto::speedrun::TopicSignalsReport { topics })
    }

    fn get_review_order(
        &mut self,
        input: anki_proto::speedrun::GetReviewOrderRequest,
    ) -> error::Result<anki_proto::speedrun::ReviewOrder> {
        let queues = self.build_queues(DeckId(input.deck_id))?;
        Ok(anki_proto::speedrun::ReviewOrder {
            card_ids: queues.card_order().into_iter().map(|c| c.0).collect(),
        })
    }

    fn set_exam_profile(
        &mut self,
        input: anki_proto::speedrun::ExamProfile,
    ) -> error::Result<anki_proto::speedrun::ExamProfile> {
        let profile = SrProfile {
            exam_date_ms: (input.exam_date_ms != 0).then_some(input.exam_date_ms),
            target_score: input.target_score,
        };
        self.storage.set_sr_profile(&profile)?;
        self.persist_exam_profile_config(&profile)?;
        Ok(to_proto_profile(&self.storage.get_sr_profile()?))
    }

    fn get_exam_profile(&mut self) -> error::Result<anki_proto::speedrun::ExamProfile> {
        self.apply_exam_profile_from_config()?;
        Ok(to_proto_profile(&self.storage.get_sr_profile()?))
    }

    fn get_exam_plan(&mut self) -> error::Result<anki_proto::speedrun::ExamPlan> {
        self.apply_exam_profile_from_config()?;
        let profile = self.storage.get_sr_profile()?;
        let readiness = self.readiness_report()?;
        let has_profile = profile.target_score > 0 || profile.exam_date_ms.is_some();
        let days_left = match profile.exam_date_ms {
            Some(ms) => {
                let now = TimestampMillis::now().0;
                (ms - now).div_euclid(86_400_000)
            }
            None => -1,
        };
        let plan = compute_exam_plan(&ExamPlanInputs {
            has_profile,
            current_readiness: readiness.readiness_scaled,
            target_score: profile.target_score,
            days_left,
            readiness_sufficient: readiness.sufficient,
        });
        Ok(anki_proto::speedrun::ExamPlan {
            has_profile: plan.has_profile,
            days_left: plan.days_left,
            current_readiness: plan.current_readiness,
            target_score: plan.target_score,
            on_track: plan.on_track,
            needed_points: plan.needed_points,
            points_per_week_needed: plan.points_per_week_needed,
            study_mode: plan.study_mode,
            recommended_tier: plan.recommended_tier,
            readiness_sufficient: plan.readiness_sufficient,
            note: plan.note,
        })
    }

    fn get_routed_practice(
        &mut self,
        input: anki_proto::speedrun::GetRoutedPracticeRequest,
    ) -> error::Result<anki_proto::speedrun::QuestionItems> {
        self.apply_question_items_from_config()?;
        let items = self.storage.get_sr_question_items_for_topic(&input.topic)?;
        Ok(anki_proto::speedrun::QuestionItems {
            items: items.into_iter().map(to_proto_question_item).collect(),
        })
    }

    fn get_practice_questions(
        &mut self,
        input: anki_proto::speedrun::GetPracticeQuestionsRequest,
    ) -> error::Result<anki_proto::speedrun::QuestionItems> {
        self.apply_question_items_from_config()?;
        let limit = if input.limit == 0 { 20 } else { input.limit };
        let topic = (!input.topic.is_empty()).then_some(input.topic.as_str());
        let items = self.storage.list_sr_question_items(limit, topic)?;
        Ok(anki_proto::speedrun::QuestionItems {
            items: items.into_iter().map(to_proto_question_item).collect(),
        })
    }

    fn get_practice_bank_summary(
        &mut self,
    ) -> error::Result<anki_proto::speedrun::PracticeBankSummary> {
        self.apply_question_items_from_config()?;
        let rows = self.storage.sr_question_item_counts_by_topic()?;
        let total = rows.iter().map(|(_, count)| count).sum();
        let topics = rows
            .into_iter()
            .map(|(topic, count)| anki_proto::speedrun::PracticeBankTopicCount { topic, count })
            .collect();
        Ok(anki_proto::speedrun::PracticeBankSummary { total, topics })
    }

    fn get_session_reasoning_round(
        &mut self,
        input: anki_proto::speedrun::SessionReasoningRoundRequest,
    ) -> error::Result<anki_proto::speedrun::QuestionItems> {
        self.apply_question_items_from_config()?;
        use std::collections::HashSet;

        use crate::speedrun::reasoning_round::deck_name_to_topic;
        use crate::speedrun::reasoning_round::select_round;
        use crate::speedrun::reasoning_round::DEFAULT_ROUND_SIZE;

        let limit = if input.limit == 0 {
            DEFAULT_ROUND_SIZE
        } else {
            input.limit
        };

        // Topics the student "touched" this session: note tags that are in the
        // topic map, plus a deck-name -> topic heuristic (so real decks whose
        // cards aren't outline-tagged still resolve to a relevant topic).
        let outline: HashSet<String> = self
            .storage
            .get_sr_topic_map()?
            .into_iter()
            .map(|e| e.topic)
            .collect();
        let mut session_topics: HashSet<String> = HashSet::new();
        for cid in &input.reviewed_card_ids {
            let Some((deck_id, note_id)) = self
                .storage
                .get_card(CardId(*cid))?
                .map(|c| (c.deck_id, c.note_id))
            else {
                continue;
            };
            if let Some(deck) = self.get_deck(deck_id)? {
                if let Some(topic) = deck_name_to_topic(&deck.human_name()) {
                    session_topics.insert(topic.to_string());
                }
            }
            if let Some(note) = self.storage.get_note(note_id)? {
                for tag in note.tags {
                    if outline.contains(&tag) {
                        session_topics.insert(tag);
                    }
                }
            }
        }

        // Tier 1: questions linked to the exact cards just reviewed.
        let mut card_linked = Vec::new();
        for cid in &input.reviewed_card_ids {
            card_linked.extend(self.storage.get_sr_question_items_for_card(CardId(*cid))?);
        }
        // Tier 2: questions on the session's topics.
        let mut topic_matched = Vec::new();
        for topic in &session_topics {
            topic_matched.extend(self.storage.get_sr_question_items_for_topic(topic)?);
        }
        // Tier 3: fallback to any held-out questions so the round always runs.
        let fallback = self.storage.list_sr_question_items(limit, None)?;

        let items = select_round(card_linked, topic_matched, fallback, limit as usize);
        Ok(anki_proto::speedrun::QuestionItems {
            items: items.into_iter().map(to_proto_question_item).collect(),
        })
    }

    fn get_due_reasoning(
        &mut self,
        input: anki_proto::speedrun::GetDueReasoningRequest,
    ) -> error::Result<anki_proto::speedrun::QuestionItems> {
        self.apply_question_items_from_config()?;
        let limit = if input.limit == 0 {
            crate::speedrun::reasoning_round::DEFAULT_ROUND_SIZE
        } else {
            input.limit
        };
        let outline = self.outline_topic_set()?;
        let mut cache: std::collections::HashMap<i64, Vec<String>> =
            std::collections::HashMap::new();

        // Per-topic performance rows, attributed to topics via the card->topic
        // heuristic, so the recall-vs-performance gap is measured per topic.
        let mut perf_by_topic: std::collections::HashMap<String, Vec<PerfCardRow>> =
            std::collections::HashMap::new();
        for (cid, attempts, correct, ivl) in self.storage.sr_performance_rows()? {
            for topic in self.topics_for_card_cached(CardId(cid), &outline, &mut cache)? {
                perf_by_topic.entry(topic).or_default().push(PerfCardRow {
                    attempts,
                    correct,
                    interval_days: ivl,
                });
            }
        }

        // Per-topic most-recent reasoning attempt (drives the recency term).
        let mut last_reasoning_ms: std::collections::HashMap<String, i64> =
            std::collections::HashMap::new();
        for (cid, _kind, _correct, ms) in self.storage.sr_exam_attempts_brief()? {
            for topic in self.topics_for_card_cached(CardId(cid), &outline, &mut cache)? {
                let entry = last_reasoning_ms.entry(topic).or_insert(ms);
                if ms > *entry {
                    *entry = ms;
                }
            }
        }

        // Per-topic coverage (binary: covered if any note is tagged with it).
        let coverage: std::collections::HashMap<String, u32> = self
            .storage
            .sr_topic_coverage_detail()?
            .into_iter()
            .map(|(topic, _label, _weight, cards)| (topic, cards))
            .collect();

        let now_ms = TimestampMillis::now().0;
        let states: Vec<crate::speedrun::reasoning_schedule::TopicReasoningState> = self
            .storage
            .sr_question_item_topic_counts()?
            .into_iter()
            .filter(|(_, count)| *count > 0)
            .map(|(topic, count)| {
                let recall_perf_gap = perf_by_topic
                    .get(&topic)
                    .map(|rows| summarize_performance(rows).recall_perf_gap)
                    .unwrap_or(0.0);
                let cov = if coverage.get(&topic).copied().unwrap_or(0) > 0 {
                    1.0
                } else {
                    0.0
                };
                let days_since_last_reasoning = match last_reasoning_ms.get(&topic) {
                    Some(ms) => ((now_ms - ms).max(0) as f32) / 86_400_000.0,
                    None => crate::speedrun::reasoning_schedule::RECENCY_SATURATION_DAYS,
                };
                crate::speedrun::reasoning_schedule::TopicReasoningState {
                    topic,
                    recall_perf_gap,
                    coverage: cov,
                    days_since_last_reasoning,
                    open_questions: count,
                }
            })
            .collect();

        let ranked = crate::speedrun::reasoning_schedule::rank_due_topics(
            &states,
            crate::speedrun::reasoning_schedule::MIN_REASONING_DEBT,
        );

        // Pull held-out questions for the most-due topics in order, deduped and
        // capped. Empty when nothing is due (honest abstain).
        let mut seen: std::collections::HashSet<i64> = std::collections::HashSet::new();
        let mut items: Vec<SrQuestionItem> = Vec::new();
        for due in ranked {
            if items.len() >= limit as usize {
                break;
            }
            for item in self.storage.get_sr_question_items_for_topic(&due.topic)? {
                if items.len() >= limit as usize {
                    break;
                }
                if seen.insert(item.id) {
                    items.push(item);
                }
            }
        }
        Ok(anki_proto::speedrun::QuestionItems {
            items: items.into_iter().map(to_proto_question_item).collect(),
        })
    }

    fn get_feedback_report(&mut self) -> error::Result<anki_proto::speedrun::FeedbackReport> {
        let outline = self.outline_topic_set()?;
        let mut cache: std::collections::HashMap<i64, Vec<String>> =
            std::collections::HashMap::new();
        let mut rows: Vec<crate::speedrun::feedback::ReportRow> = Vec::new();
        for (cid, kind, correct, _ms) in self.storage.sr_exam_attempts_brief()? {
            // A single primary topic per attempt keeps the totals honest; the
            // per-topic weak list uses the same primary attribution.
            let topic = self
                .topics_for_card_cached(CardId(cid), &outline, &mut cache)?
                .into_iter()
                .next()
                .unwrap_or_default();
            rows.push(crate::speedrun::feedback::ReportRow {
                topic,
                diagnosis_kind: kind,
                correct,
            });
        }
        let report = crate::speedrun::feedback::aggregate_report(&rows);
        let weak_topics = report
            .weak_topics
            .into_iter()
            .filter(|t| !t.is_empty())
            .collect();
        Ok(anki_proto::speedrun::FeedbackReport {
            total: report.total,
            correct: report.correct,
            memory_misses: report.memory_misses,
            reasoning_misses: report.reasoning_misses,
            passage_misses: report.passage_misses,
            test_taking_misses: report.test_taking_misses,
            weak_topics,
        })
    }

    fn update_attempt_diagnosis(
        &mut self,
        input: anki_proto::speedrun::UpdateAttemptDiagnosisRequest,
    ) -> error::Result<()> {
        self.storage.update_sr_attempt_diagnosis(
            input.attempt_id,
            input.diagnosis_kind as u8,
            input.routed_action as u8,
        )
    }

    fn set_action_status(
        &mut self,
        input: anki_proto::speedrun::SetActionStatusRequest,
    ) -> error::Result<()> {
        self.storage
            .set_sr_attempt_action_status(input.attempt_id, input.action_status as u8)
    }

    fn get_leakage_report(&mut self) -> error::Result<anki_proto::speedrun::LeakageReport> {
        self.apply_question_items_from_config()?;
        let rows = self.storage.sr_question_items_with_note_text()?;
        let total_items = rows.len() as u32;
        let mut flagged_item_ids = Vec::new();
        for (id, payload, note_text) in rows {
            let Some(note_text) = note_text else {
                continue;
            };
            let stem = extract_stem(&payload);
            if crate::speedrun::leakage::is_leaked(&stem, &note_text) {
                flagged_item_ids.push(id);
            }
        }
        let flagged = flagged_item_ids.len() as u32;
        Ok(anki_proto::speedrun::LeakageReport {
            total_items,
            flagged,
            flagged_item_ids,
            clean: flagged == 0,
        })
    }

    fn get_calibration_report(&mut self) -> error::Result<anki_proto::speedrun::CalibrationReport> {
        let pairs: Vec<CalibrationPair> = self
            .storage
            .sr_calibration_pairs()?
            .into_iter()
            .map(|(predicted, outcome)| CalibrationPair { predicted, outcome })
            .collect();
        let report = compute_calibration(&pairs, 10);
        Ok(anki_proto::speedrun::CalibrationReport {
            n: report.n,
            brier: report.brier,
            log_loss: report.log_loss,
            sufficient: report.sufficient,
            note: report.note,
            bins: report
                .bins
                .into_iter()
                .map(|b| anki_proto::speedrun::CalibrationBin {
                    lo: b.lo,
                    hi: b.hi,
                    count: b.count,
                    mean_predicted: b.mean_predicted,
                    mean_outcome: b.mean_outcome,
                })
                .collect(),
        })
    }

    fn seed_sample_history(
        &mut self,
        input: anki_proto::speedrun::SeedSampleHistoryRequest,
    ) -> error::Result<anki_proto::speedrun::SeedSampleHistoryResponse> {
        use crate::card::CardQueue;
        use crate::card::CardType;
        use crate::search::SortMode;

        let mature_n = if input.mature_cards == 0 {
            30
        } else {
            input.mature_cards
        } as usize;
        let exam_n = if input.exam_attempts == 0 {
            24
        } else {
            input.exam_attempts
        } as usize;
        let srs_n = if input.srs_attempts == 0 {
            12
        } else {
            input.srs_attempts
        } as usize;

        // Mark up to mature_n existing cards as mature review cards (interval >=
        // 21 days) so the memory signal (mature/review) becomes meaningful
        // without waiting real calendar time. The undoable update path sets
        // mtime/usn so the change syncs to the phone. The readiness score is
        // still COMPUTED from this seeded state, never hand-set.
        let cids = self.search_cards("", SortMode::NoOrder)?;
        let mut updated = Vec::new();
        for cid in cids.iter().take(mature_n) {
            if let Some(mut card) = self.storage.get_card(*cid)? {
                card.ctype = CardType::Review;
                card.queue = CardQueue::Review;
                card.interval = 30;
                card.due = 0;
                updated.push(card);
            }
        }
        let targets: Vec<(i64, i64)> = updated.iter().map(|c| (c.id.0, c.note_id.0)).collect();
        let cards_matured = updated.len() as u32;
        if !updated.is_empty() {
            self.update_cards_maybe_undoable(updated, false)?;
        }

        // Record exam-style + SRS attempts (with predictions) on the matured
        // cards, mirroring the e2e proof harness, so performance, the recall->
        // performance gap and calibration all populate.
        let mut recorded = 0u32;
        if !targets.is_empty() {
            let base_ms = TimestampMillis::now().0;
            for i in 0..exam_n {
                let (cid, nid) = targets[i % targets.len()];
                let correct = i % 3 != 0; // ~2/3 correct, a repeatable weakness pattern
                let _ = self.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
                    card_id: cid,
                    note_id: nid,
                    session_id: "sample-history".to_string(),
                    answered_at_ms: base_ms + i as i64,
                    took_ms: 7000,
                    question_type: 1,
                    correct,
                    predicted: Some(if correct { 0.75 } else { 0.45 }),
                    data: "{}".to_string(),
                    ..Default::default()
                })?;
                recorded += 1;
            }
            for i in 0..srs_n {
                let (cid, nid) = targets[i % targets.len()];
                let _ = self.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
                    card_id: cid,
                    note_id: nid,
                    session_id: "sample-history".to_string(),
                    answered_at_ms: base_ms + (exam_n + i) as i64,
                    took_ms: 6000,
                    question_type: 0,
                    correct: true,
                    predicted: Some(0.8),
                    data: "{}".to_string(),
                    ..Default::default()
                })?;
                recorded += 1;
            }
        }

        // Refresh the cached readiness snapshot so the UI reflects the seed.
        let _ = self.compute_readiness()?;

        Ok(anki_proto::speedrun::SeedSampleHistoryResponse {
            cards_matured,
            attempts_recorded: recorded,
        })
    }
}

impl Collection {
    /// Predicted recall probability for a card from its FSRS memory state, or
    /// None when the card has no memory state / last-review time recorded.
    pub(crate) fn predicted_recall_for_card(&self, cid: CardId) -> error::Result<Option<f32>> {
        let Some(card) = self.storage.get_card(cid)? else {
            return Ok(None);
        };
        let data = CardData::from_card(&card);
        let (Some(state), Some(last)) = (data.memory_state(), data.last_review_time) else {
            return Ok(None);
        };
        let now = TimestampSecs::now().0;
        let seconds_elapsed = (now as u32).saturating_sub(last.0 as u32);
        let decay = data.decay.unwrap_or(FSRS5_DEFAULT_DECAY);
        let retrievability = FSRS::new(None).unwrap().current_retrievability_seconds(
            state.into(),
            seconds_elapsed,
            decay,
        );
        Ok(Some(retrievability))
    }

    /// The set of outline (topic-map) topics, used for tag-based attribution.
    fn outline_topic_set(&self) -> error::Result<std::collections::HashSet<String>> {
        Ok(self
            .storage
            .get_sr_topic_map()?
            .into_iter()
            .map(|e| e.topic)
            .collect())
    }

    /// Persist the topic map in collection config so it rides normal Anki
    /// config sync (``sr_topic_map`` itself has no USN and is not
    /// chunk-synced).
    fn persist_topic_map_config(&mut self, entries: &[SrTopicMapEntry]) -> error::Result<()> {
        let vec = entries.to_vec();
        self.set_config(CFG_TOPIC_MAP, &vec)?;
        Ok(())
    }

    /// Persist held-out questions in collection config so practice banks
    /// survive incremental sync (``sr_question_items`` itself has no USN
    /// and is not chunk-synced). Semantics: the whole set is written under
    /// one key, and ``apply_question_items_from_config`` reinserts by id,
    /// so this is an append/update-only merge (config keys are
    /// last-writer-wins by mtime). ``add_question_item`` applies the synced
    /// set *before* inserting, so a device never clobbers questions another
    /// device already uploaded. Items are never deleted at runtime, so no
    /// tombstones are needed.
    fn persist_question_items_config(&mut self) -> error::Result<()> {
        let items = self.storage.get_all_sr_question_items()?;
        self.set_config(CFG_QUESTION_ITEMS, &items)?;
        Ok(())
    }

    fn apply_question_items_from_config(&mut self) -> error::Result<()> {
        let Some(items) = self.get_config_optional::<Vec<SrQuestionItem>, _>(CFG_QUESTION_ITEMS)
        else {
            return Ok(());
        };
        for item in items {
            self.storage.add_or_update_sr_question_item(&item)?;
        }
        Ok(())
    }

    fn persist_exam_profile_config(&mut self, profile: &SrProfile) -> error::Result<()> {
        self.set_config(CFG_EXAM_PROFILE, profile)?;
        Ok(())
    }

    fn apply_exam_profile_from_config(&mut self) -> error::Result<()> {
        if let Some(profile) = self.get_config_optional::<SrProfile, _>(CFG_EXAM_PROFILE) {
            self.storage.set_sr_profile(&profile)?;
        }
        Ok(())
    }

    /// The topics a card belongs to, via the deck-name heuristic plus note tags
    /// that are in the outline (mirrors `get_session_reasoning_round`). Cached
    /// by card id because the reasoning-due / feedback aggregations revisit
    /// cards.
    fn topics_for_card_cached(
        &mut self,
        cid: CardId,
        outline: &std::collections::HashSet<String>,
        cache: &mut std::collections::HashMap<i64, Vec<String>>,
    ) -> error::Result<Vec<String>> {
        if let Some(hit) = cache.get(&cid.0) {
            return Ok(hit.clone());
        }
        let mut topics: Vec<String> = Vec::new();
        if let Some((deck_id, note_id)) =
            self.storage.get_card(cid)?.map(|c| (c.deck_id, c.note_id))
        {
            if let Some(deck) = self.get_deck(deck_id)? {
                if let Some(topic) =
                    crate::speedrun::reasoning_round::deck_name_to_topic(&deck.human_name())
                {
                    topics.push(topic.to_string());
                }
            }
            if let Some(note) = self.storage.get_note(note_id)? {
                for tag in note.tags {
                    if outline.contains(&tag) && !topics.contains(&tag) {
                        topics.push(tag);
                    }
                }
            }
        }
        cache.insert(cid.0, topics.clone());
        Ok(topics)
    }

    /// Gather raw evidence from the collection + Speedrun tables and compute
    /// the three scores. Pure scoring lives in `speedrun::readiness`.
    fn readiness_report(&self) -> error::Result<ReadinessReport> {
        let (review_cards, mature_cards) = self.storage.sr_card_counts()?;
        let (exam_attempts, exam_correct) = self.storage.sr_exam_attempt_stats()?;
        let graded_attempts = self.storage.sr_attempt_count()?;
        let (topics_total, topics_covered) = self.storage.sr_topic_coverage()?;
        // Weighted coverage lets readiness abstain when a high-weight section is
        // skipped even though raw topic count looks fine.
        let coverage_rows: Vec<TopicCoverageRow> = self
            .storage
            .sr_topic_coverage_detail()?
            .into_iter()
            .map(|(_, _, weight, cards)| TopicCoverageRow { weight, cards })
            .collect();
        let weighted_coverage = summarize_coverage(&coverage_rows).weighted_coverage;
        Ok(compute_readiness(&ReadinessInputs {
            review_cards,
            mature_cards,
            exam_attempts,
            exam_correct,
            graded_attempts,
            topics_total,
            topics_covered,
            weighted_coverage,
        }))
    }
}

fn to_proto_diagnosis(d: Diagnosis) -> anki_proto::speedrun::Diagnosis {
    anki_proto::speedrun::Diagnosis {
        kind: d.kind as u32,
        confidence: d.confidence,
        routed_action: d.routed_action as u32,
    }
}

fn to_proto_attempt(a: SrAttempt) -> anki_proto::speedrun::SrAttempt {
    anki_proto::speedrun::SrAttempt {
        id: a.id,
        card_id: a.cid.0,
        note_id: a.nid.0,
        correct: a.correct,
        diagnosis_kind: a.diagnosis_kind as u32,
        diagnosis_confidence: a.diagnosis_confidence,
        routed_action: a.routed_action as u32,
        answered_at_ms: a.answered_at_ms,
    }
}

/// Pull the question stem out of a payload JSON ({"stem": ...}); fall back to
/// the whole payload when it isn't the expected shape.
fn extract_stem(payload: &str) -> String {
    serde_json::from_str::<serde_json::Value>(payload)
        .ok()
        .and_then(|v| v.get("stem").and_then(|s| s.as_str()).map(str::to_string))
        .unwrap_or_else(|| payload.to_string())
}

fn to_proto_question_item(item: SrQuestionItem) -> anki_proto::speedrun::QuestionItem {
    anki_proto::speedrun::QuestionItem {
        id: item.id,
        card_id: item.cid.unwrap_or(0),
        topic: item.topic,
        provenance: item.provenance as u32,
        payload: item.payload,
    }
}

fn to_proto_profile(p: &SrProfile) -> anki_proto::speedrun::ExamProfile {
    anki_proto::speedrun::ExamProfile {
        exam_date_ms: p.exam_date_ms.unwrap_or(0),
        target_score: p.target_score,
    }
}

fn to_proto_readiness(s: &SrReadiness) -> anki_proto::speedrun::ReadinessSnapshot {
    anki_proto::speedrun::ReadinessSnapshot {
        memory: s.memory,
        performance: s.performance,
        recall_perf_gap: s.recall_perf_gap,
        coverage: s.coverage,
        readiness_scaled: s.readiness_scaled,
        low_scaled: s.low_scaled,
        high_scaled: s.high_scaled,
        sufficient: s.sufficient,
        reason: s.reason.clone(),
        computed_at_ms: s.computed_at_ms,
        memory_sufficient: s.memory_sufficient,
        performance_sufficient: s.performance_sufficient,
        blocking_dimension: s.blocking_dimension.clone(),
    }
}

#[cfg(test)]
mod test {
    use anki_io::new_tempfile;

    use crate::collection::CollectionBuilder;
    use crate::error::Result;
    use crate::services::SpeedrunService;
    use crate::speedrun::ACTION_RESURFACE;
    use crate::speedrun::DIAGNOSIS_MEMORY;

    #[test]
    fn record_classifies_and_persists_attempt() -> Result<()> {
        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        let resp = col.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
            card_id: 111,
            note_id: 222,
            session_id: "s1".to_string(),
            answered_at_ms: 1_700_000_000_000,
            took_ms: 3000,
            question_type: 1,
            selected: Some(1),
            correct: false,
            signals: Some(anki_proto::speedrun::ClassifyAttemptRequest {
                correct: false,
                took_ms: 3000,
                recall_failed: true,
                passage_evidence_missed: false,
                question_type: 1,
            }),
            data: "{}".to_string(),
            predicted: None,
        })?;

        // recall_failed -> memory gap, routed to resurface
        let diagnosis = resp.diagnosis.expect("diagnosis present");
        assert_eq!(diagnosis.kind, DIAGNOSIS_MEMORY as u32);
        assert_eq!(diagnosis.routed_action, ACTION_RESURFACE as u32);

        let fetched =
            col.get_attempts_for_card(anki_proto::speedrun::GetAttemptsForCardRequest {
                card_id: 111,
            })?;
        assert_eq!(fetched.attempts.len(), 1);
        assert_eq!(fetched.attempts[0].diagnosis_kind, DIAGNOSIS_MEMORY as u32);
        assert!(!fetched.attempts[0].correct);

        Ok(())
    }

    #[test]
    fn readiness_abstains_on_empty_collection_and_caches() -> Result<()> {
        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        // No evidence yet: must abstain rather than invent a number.
        let snapshot = col.compute_readiness()?;
        assert!(!snapshot.sufficient);
        assert!(snapshot.reason.contains("not enough evidence"));
        // empty collection has no memory substrate -> memory dimension blocks first
        assert!(!snapshot.memory_sufficient);
        assert!(!snapshot.performance_sufficient);
        assert_eq!(snapshot.blocking_dimension, "memory");

        // The snapshot was cached and is returned by GetReadinessSnapshot.
        let cached = col.get_readiness_snapshot()?;
        assert_eq!(cached.computed_at_ms, snapshot.computed_at_ms);
        assert!(!cached.sufficient);
        Ok(())
    }

    #[test]
    fn question_items_and_performance_report() -> Result<()> {
        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        // register a held-out question item for card 500
        let id = col
            .add_question_item(anki_proto::speedrun::QuestionItem {
                id: 0,
                card_id: 500,
                topic: "amino acids".to_string(),
                provenance: 0,
                payload: "{\"stem\":\"reworded\"}".to_string(),
            })?
            .id;
        assert!(id > 0);

        let items = col.get_question_items_for_card(
            anki_proto::speedrun::GetQuestionItemsForCardRequest { card_id: 500 },
        )?;
        assert_eq!(items.items.len(), 1);
        assert_eq!(items.items[0].topic, "amino acids");

        // record two exam-style attempts on card 500: one correct, one wrong
        for (i, correct) in [(1, true), (2, false)] {
            let _ = col.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
                card_id: 500,
                note_id: 1,
                session_id: "s".to_string(),
                answered_at_ms: i,
                took_ms: 5000,
                question_type: 1,
                correct,
                ..Default::default()
            })?;
        }

        let report = col.get_performance_report()?;
        assert_eq!(report.cards_evaluated, 1);
        assert_eq!(report.exam_attempts, 2);
        assert!((report.performance_rate - 0.5).abs() < 1e-6);
        assert_eq!(report.question_items, 1);
        // only one card -> below the gap threshold, so it abstains
        assert!(!report.sufficient);
        Ok(())
    }

    #[test]
    fn topic_map_seed_and_coverage() -> Result<()> {
        use crate::prelude::DeckId;

        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        // seed the built-in starter outline
        let seeded = col.seed_mcat_topic_outline()?;
        assert_eq!(seeded.topics, 10);
        assert_eq!(col.get_topic_map()?.entries.len(), 10);

        // nothing tagged yet -> nothing covered
        let report = col.get_coverage_report()?;
        assert_eq!(report.topics_total, 10);
        assert_eq!(report.topics_covered, 0);

        // add a note tagged with topic "fc1"
        let nt = col.get_notetype_by_name("Basic")?.unwrap();
        let mut note = nt.new_note();
        note.set_field(0, "an amino acid fact")?;
        note.tags = vec!["fc1".to_string()];
        col.add_note(&mut note, DeckId(1))?;

        let report = col.get_coverage_report()?;
        assert_eq!(report.topics_covered, 1);
        assert!((report.coverage - 0.1).abs() < 1e-6);
        let fc1 = report.topics.iter().find(|t| t.topic == "fc1").unwrap();
        assert!(fc1.covered);
        assert_eq!(fc1.cards, 1);
        Ok(())
    }

    #[test]
    fn set_topic_map_replaces_entries() -> Result<()> {
        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        let _ = col.seed_mcat_topic_outline()?;
        let resp = col.set_topic_map(anki_proto::speedrun::TopicMap {
            entries: vec![anki_proto::speedrun::TopicMapEntry {
                topic: "bio".to_string(),
                label: "Biology".to_string(),
                weight: 2.0,
            }],
        })?;
        assert_eq!(resp.topics, 1);
        let map = col.get_topic_map()?;
        assert_eq!(map.entries.len(), 1);
        assert_eq!(map.entries[0].topic, "bio");
        Ok(())
    }

    #[test]
    fn calibration_over_predicted_attempts() -> Result<()> {
        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        // two perfectly-calibrated predictions: 1.0/correct and 0.0/wrong
        for (predicted, correct) in [(1.0f32, true), (0.0f32, false)] {
            let _ = col.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
                card_id: 7,
                note_id: 1,
                question_type: 0,
                correct,
                predicted: Some(predicted),
                ..Default::default()
            })?;
        }
        // an attempt without a prediction is excluded from calibration
        let _ = col.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
            card_id: 7,
            note_id: 1,
            question_type: 0,
            correct: true,
            ..Default::default()
        })?;

        let report = col.get_calibration_report()?;
        assert_eq!(report.n, 2);
        assert!(report.brier.abs() < 1e-6, "brier {}", report.brier);
        // below the threshold, so it flags insufficiency but still reports
        assert!(!report.sufficient);
        Ok(())
    }

    #[test]
    fn exam_profile_and_plan() -> Result<()> {
        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        // default: no profile
        let plan = col.get_exam_plan()?;
        assert!(!plan.has_profile);
        assert_eq!(plan.study_mode, "long_term");

        // set an exam ~14 days out with a top-third target
        let exam_ms = crate::prelude::TimestampMillis::now().0 + 14 * 86_400_000;
        let stored = col.set_exam_profile(anki_proto::speedrun::ExamProfile {
            exam_date_ms: exam_ms,
            target_score: 510,
        })?;
        assert_eq!(stored.target_score, 510);
        assert_eq!(col.get_exam_profile()?.target_score, 510);

        let plan = col.get_exam_plan()?;
        assert!(plan.has_profile);
        assert_eq!(plan.target_score, 510);
        // ~14 days left (allow for clock granularity)
        assert!(
            plan.days_left >= 13 && plan.days_left <= 14,
            "days_left {}",
            plan.days_left
        );
        // empty collection -> readiness ~472, below target -> needs points,
        // consolidates
        assert!(plan.needed_points > 0);
        assert_eq!(plan.study_mode, "consolidation");
        Ok(())
    }

    #[test]
    fn srs_attempt_auto_captures_predicted_recall() -> Result<()> {
        use crate::card::CardQueue;
        use crate::card::CardType;
        use crate::card::FsrsMemoryState;
        use crate::prelude::DeckId;
        use crate::prelude::TimestampSecs;

        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;
        let nt = col.get_notetype_by_name("Basic")?.unwrap();
        let mut note = nt.new_note();
        note.set_field(0, "x")?;
        col.add_note(&mut note, DeckId(1))?;

        let mut card = col.storage.get_card_by_ordinal(note.id, 0)?.unwrap();
        card.memory_state = Some(FsrsMemoryState {
            stability: 10.0,
            difficulty: 5.0,
        });
        card.last_review_time = Some(TimestampSecs::now().adding_secs(-86_400));
        card.ctype = CardType::Review;
        card.queue = CardQueue::Review;
        card.interval = 10;
        card.due = 0;
        col.update_cards_maybe_undoable(vec![card.clone()], false)?;

        // SRS attempt with no supplied prediction -> engine fills it from FSRS
        let _ = col.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
            card_id: card.id.0,
            note_id: note.id.0,
            question_type: 0,
            correct: true,
            ..Default::default()
        })?;

        let attempts = col.storage.sr_attempts_for_card(card.id)?;
        assert_eq!(attempts.len(), 1);
        let predicted = attempts[0].predicted.expect("predicted captured from FSRS");
        assert!(predicted > 0.0 && predicted <= 1.0, "predicted {predicted}");
        Ok(())
    }

    #[test]
    fn leakage_flags_verbatim_question_items() -> Result<()> {
        use crate::prelude::DeckId;

        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;
        let nt = col.get_notetype_by_name("Basic")?.unwrap();
        let mut note = nt.new_note();
        note.set_field(0, "The peptide bond is an amide bond between residues")?;
        col.add_note(&mut note, DeckId(1))?;
        let cid = col.storage.get_card_by_ordinal(note.id, 0)?.unwrap().id.0;

        // a verbatim copy of the card -> leak
        let leaked = col
            .add_question_item(anki_proto::speedrun::QuestionItem {
                card_id: cid,
                payload: "{\"stem\": \"the peptide bond is an amide bond\"}".to_string(),
                ..Default::default()
            })?
            .id;
        // a reworded item -> clean
        let _ = col.add_question_item(anki_proto::speedrun::QuestionItem {
            card_id: cid,
            payload: "{\"stem\": \"Which functional group links adjacent amino acids?\"}"
                .to_string(),
            ..Default::default()
        })?;

        let report = col.get_leakage_report()?;
        assert_eq!(report.total_items, 2);
        assert_eq!(report.flagged, 1);
        assert_eq!(report.flagged_item_ids, vec![leaked]);
        assert!(!report.clean);
        Ok(())
    }

    #[test]
    fn due_reasoning_and_feedback_report() -> Result<()> {
        use crate::card::CardQueue;
        use crate::card::CardType;
        use crate::prelude::DeckId;

        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        // Outline contains "biology" so a note tagged "biology" attributes to it.
        let _ = col.set_topic_map(anki_proto::speedrun::TopicMap {
            entries: vec![anki_proto::speedrun::TopicMapEntry {
                topic: "biology".to_string(),
                label: "Biology".to_string(),
                weight: 1.0,
            }],
        })?;

        // A mature card tagged "biology" (recall proxy = 1.0).
        let nt = col.get_notetype_by_name("Basic")?.unwrap();
        let mut note = nt.new_note();
        note.set_field(0, "photosynthesis fact")?;
        note.tags = vec!["biology".to_string()];
        col.add_note(&mut note, DeckId(1))?;
        let mut card = col.storage.get_card_by_ordinal(note.id, 0)?.unwrap();
        card.ctype = CardType::Review;
        card.queue = CardQueue::Review;
        card.interval = 30;
        col.update_cards_maybe_undoable(vec![card.clone()], false)?;

        // Two held-out biology questions to schedule.
        for n in 0..2 {
            let _ = col.add_question_item(anki_proto::speedrun::QuestionItem {
                card_id: card.id.0,
                topic: "biology".to_string(),
                payload: format!("{{\"stem\":\"q{n}\"}}"),
                ..Default::default()
            })?;
        }

        // Two exam-style attempts on the card: one correct, one wrong (reasoning).
        for (i, correct) in [(1i64, true), (2i64, false)] {
            let _ = col.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
                card_id: card.id.0,
                note_id: note.id.0,
                answered_at_ms: 1_700_000_000_000 + i,
                took_ms: 12000,
                question_type: 1,
                correct,
                ..Default::default()
            })?;
        }

        // Feedback report: mature recall (1.0) vs 0.5 performance -> a gap; the
        // wrong exam attempt is a reasoning miss attributed to "biology".
        let report = col.get_feedback_report()?;
        assert_eq!(report.total, 2);
        assert_eq!(report.correct, 1);
        assert_eq!(report.reasoning_misses, 1);
        assert_eq!(report.weak_topics, vec!["biology".to_string()]);

        // Due-reasoning: the positive recall-vs-performance gap makes "biology"
        // due, so its held-out questions are returned.
        let due =
            col.get_due_reasoning(anki_proto::speedrun::GetDueReasoningRequest { limit: 5 })?;
        assert_eq!(due.items.len(), 2);
        assert!(due.items.iter().all(|q| q.topic == "biology"));

        Ok(())
    }

    #[test]
    fn seed_sample_history_populates_three_scores() -> Result<()> {
        use crate::prelude::DeckId;

        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        // 10-FC outline + 30 cards spread across topics so coverage clears 50%.
        let _ = col.seed_mcat_topic_outline()?;
        let nt = col.get_notetype_by_name("Basic")?.unwrap();
        for i in 0..30 {
            let mut note = nt.new_note();
            note.set_field(0, &format!("fact {i}"))?;
            note.tags = vec![format!("fc{}", i % 10 + 1)];
            col.add_note(&mut note, DeckId(1))?;
        }

        // Before seeding: all new cards -> readiness abstains honestly.
        assert!(!col.compute_readiness()?.sufficient);

        let resp =
            col.seed_sample_history(anki_proto::speedrun::SeedSampleHistoryRequest::default())?;
        assert_eq!(resp.cards_matured, 30);
        assert_eq!(resp.attempts_recorded, 36);

        // After seeding: an in-range, sufficient score with all three signals,
        // COMPUTED from the seeded mature cards + attempts (never hand-set).
        let snap = col.compute_readiness()?;
        assert!(snap.sufficient, "reason: {}", snap.reason);
        assert!(snap.memory > 0.0 && snap.performance > 0.0 && snap.coverage > 0.0);
        assert!(snap.readiness_scaled >= 472 && snap.readiness_scaled <= 528);
        assert!(snap.low_scaled <= snap.readiness_scaled);
        assert!(snap.high_scaled >= snap.readiness_scaled);
        // Predictions were recorded, so calibration has data.
        assert_eq!(col.get_calibration_report()?.n, 36);
        Ok(())
    }

    #[test]
    fn topic_signals_attribute_by_topic() -> Result<()> {
        use crate::card::CardQueue;
        use crate::card::CardType;
        use crate::prelude::DeckId;
        use crate::search::SortMode;

        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        let _ = col.seed_mcat_topic_outline()?;
        let nt = col.get_notetype_by_name("Basic")?.unwrap();
        // three notes tagged fc1, two tagged fc2
        for (field, tag) in [
            ("a", "fc1"),
            ("b", "fc1"),
            ("c", "fc1"),
            ("d", "fc2"),
            ("e", "fc2"),
        ] {
            let mut note = nt.new_note();
            note.set_field(0, field)?;
            note.tags = vec![tag.to_string()];
            col.add_note(&mut note, DeckId(1))?;
        }

        // Mature two of the fc1 cards (review queue, interval >= 21).
        let fc1_cards = col.search_cards("tag:fc1", SortMode::NoOrder)?;
        assert_eq!(fc1_cards.len(), 3);
        let mut updated = Vec::new();
        for cid in fc1_cards.iter().take(2) {
            let mut card = col.storage.get_card(*cid)?.unwrap();
            card.ctype = CardType::Review;
            card.queue = CardQueue::Review;
            card.interval = 30;
            updated.push(card);
        }
        col.update_cards_maybe_undoable(updated, false)?;

        // Exam-style attempts: 3 on an fc1 card (2 correct), 1 on an fc2 card (0).
        let fc2_cards = col.search_cards("tag:fc2", SortMode::NoOrder)?;
        for correct in [true, true, false] {
            let _ = col.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
                card_id: fc1_cards[0].0,
                question_type: 2,
                correct,
                ..Default::default()
            })?;
        }
        let _ = col.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
            card_id: fc2_cards[0].0,
            question_type: 2,
            correct: false,
            ..Default::default()
        })?;

        let report = col.get_topic_signals()?;
        let sig = |t: &str| {
            report
                .topics
                .iter()
                .find(|s| s.topic == t)
                .unwrap_or_else(|| panic!("missing topic {t}"))
        };

        let fc1 = sig("fc1");
        assert_eq!(fc1.cards, 3);
        assert!(fc1.covered);
        assert_eq!(fc1.review_cards, 2);
        assert_eq!(fc1.mature_cards, 2);
        assert_eq!(fc1.exam_attempts, 3);
        assert_eq!(fc1.exam_correct, 2);

        let fc2 = sig("fc2");
        assert_eq!(fc2.cards, 2);
        assert!(fc2.covered);
        assert_eq!(fc2.review_cards, 0);
        assert_eq!(fc2.mature_cards, 0);
        assert_eq!(fc2.exam_attempts, 1);
        assert_eq!(fc2.exam_correct, 0);

        // An untouched topic reports zeros and is not covered.
        let fc3 = sig("fc3");
        assert_eq!(fc3.cards, 0);
        assert!(!fc3.covered);
        assert_eq!(fc3.exam_attempts, 0);
        Ok(())
    }

    #[test]
    fn routed_practice_and_diagnosis_correction() -> Result<()> {
        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        // concept-linked practice: items are returned per topic
        let _ = col.add_question_item(anki_proto::speedrun::QuestionItem {
            topic: "fc1".to_string(),
            payload: "{}".to_string(),
            ..Default::default()
        })?;
        let _ = col.add_question_item(anki_proto::speedrun::QuestionItem {
            topic: "fc2".to_string(),
            payload: "{}".to_string(),
            ..Default::default()
        })?;
        let practice = col.get_routed_practice(anki_proto::speedrun::GetRoutedPracticeRequest {
            topic: "fc1".to_string(),
        })?;
        assert_eq!(practice.items.len(), 1);
        assert_eq!(practice.items[0].topic, "fc1");

        // record a miss, then correct its diagnosis and advance the action status
        let resp = col.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
            card_id: 7,
            note_id: 1,
            question_type: 1,
            correct: false,
            ..Default::default()
        })?;
        col.update_attempt_diagnosis(anki_proto::speedrun::UpdateAttemptDiagnosisRequest {
            attempt_id: resp.id,
            diagnosis_kind: 3,
            routed_action: 2,
        })?;
        col.set_action_status(anki_proto::speedrun::SetActionStatusRequest {
            attempt_id: resp.id,
            action_status: 1,
        })?;

        let attempts = col
            .storage
            .sr_attempts_for_card(crate::prelude::CardId(7))?;
        assert_eq!(attempts.len(), 1);
        assert_eq!(attempts[0].diagnosis_kind, 3);
        assert_eq!(attempts[0].routed_action, 2);
        assert_eq!(attempts[0].action_status, 1);
        Ok(())
    }
}
