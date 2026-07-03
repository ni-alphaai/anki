// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Feedback timing + spaced re-test scheduling (D2).
//!
//! Feedback stays prompt, but its *richness* scales with per-topic proficiency:
//! novices (weak topics) get the full source-grounded mini-lesson, proficient
//! learners a terse cue (expertise reversal - heavy scaffolding can hurt
//! high-knowledge learners). A missed reasoning concept is scheduled for a
//! spaced paraphrase re-test, and the "delayed" surface is the
//! end-of-session/weekly report this module aggregates. Pure and DB-free; the
//! DB + proto wiring lives in `service.rs`. Applies to reasoning questions only
//! - FSRS card grades stay immediate.

use std::collections::BTreeMap;

use super::DIAGNOSIS_MEMORY;
use super::DIAGNOSIS_PASSAGE;
use super::DIAGNOSIS_REASONING;
use super::DIAGNOSIS_TEST_TAKING;

/// A topic at or above this exam-style accuracy is treated as proficient, so
/// feedback fades to a brief cue.
pub const PROFICIENT_THRESHOLD: f32 = 0.8;

/// Spaced re-test delays (days), indexed by how many re-tests the concept has
/// already had. Expanding schedule; the last value repeats once exhausted.
pub const RETEST_DELAYS_DAYS: [f32; 4] = [3.0, 7.0, 16.0, 35.0];

/// Milliseconds per day, for absolute due timestamps.
pub const MS_PER_DAY: i64 = 86_400_000;

/// How elaborate the immediate feedback should be.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FeedbackRichness {
    /// Full source-grounded mini-lesson (novice / weak topic).
    Full,
    /// Terse confirmation cue (proficient / strong topic).
    Brief,
}

/// Choose feedback richness from a topic's exam-style proficiency (0.0..=1.0).
pub fn feedback_richness(topic_proficiency: f32) -> FeedbackRichness {
    if topic_proficiency >= PROFICIENT_THRESHOLD {
        FeedbackRichness::Brief
    } else {
        FeedbackRichness::Full
    }
}

/// Spaced delay (days) before the next paraphrase re-test, given how many
/// re-tests the concept has already had. Clamps to the last schedule entry.
pub fn next_retest_delay_days(prior_retests: u32) -> f32 {
    let idx = (prior_retests as usize).min(RETEST_DELAYS_DAYS.len() - 1);
    RETEST_DELAYS_DAYS[idx]
}

/// Absolute due timestamp (ms since epoch) for the next re-test.
pub fn retest_due_at_ms(now_ms: i64, prior_retests: u32) -> i64 {
    now_ms + (next_retest_delay_days(prior_retests) * MS_PER_DAY as f32).round() as i64
}

/// One attempt row feeding the report.
#[derive(Debug, Clone)]
pub struct ReportRow {
    pub topic: String,
    pub diagnosis_kind: u8,
    pub correct: bool,
}

/// Aggregated end-of-session / periodic feedback report.
#[derive(Debug, Clone, PartialEq)]
pub struct FeedbackReport {
    pub total: u32,
    pub correct: u32,
    pub memory_misses: u32,
    pub reasoning_misses: u32,
    pub passage_misses: u32,
    pub test_taking_misses: u32,
    /// Topics ordered by miss count (desc), then by name, for "weakest first".
    pub weak_topics: Vec<String>,
}

/// Aggregate attempt rows into a feedback report (counts by diagnosis kind +
/// weakest topics by miss count).
pub fn aggregate_report(rows: &[ReportRow]) -> FeedbackReport {
    let mut report = FeedbackReport {
        total: rows.len() as u32,
        correct: 0,
        memory_misses: 0,
        reasoning_misses: 0,
        passage_misses: 0,
        test_taking_misses: 0,
        weak_topics: Vec::new(),
    };

    // BTreeMap keeps topics sorted by name ascending, so the later stable sort
    // by miss count only needs to reorder, preserving name order within ties.
    let mut topic_misses: BTreeMap<&str, u32> = BTreeMap::new();

    for row in rows {
        if row.correct {
            report.correct += 1;
            continue;
        }
        match row.diagnosis_kind {
            DIAGNOSIS_MEMORY => report.memory_misses += 1,
            DIAGNOSIS_REASONING => report.reasoning_misses += 1,
            DIAGNOSIS_PASSAGE => report.passage_misses += 1,
            DIAGNOSIS_TEST_TAKING => report.test_taking_misses += 1,
            _ => {}
        }
        *topic_misses.entry(row.topic.as_str()).or_insert(0) += 1;
    }

    let mut ranked: Vec<(&str, u32)> = topic_misses.into_iter().collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1));
    report.weak_topics = ranked
        .into_iter()
        .map(|(topic, _)| topic.to_string())
        .collect();

    report
}

#[cfg(test)]
mod test {
    use super::*;
    use crate::speedrun::DIAGNOSIS_CORRECT;

    fn row(topic: &str, diagnosis_kind: u8, correct: bool) -> ReportRow {
        ReportRow {
            topic: topic.to_string(),
            diagnosis_kind,
            correct,
        }
    }

    #[test]
    fn richness_below_threshold_is_full() {
        assert_eq!(feedback_richness(0.0), FeedbackRichness::Full);
        assert_eq!(
            feedback_richness(PROFICIENT_THRESHOLD - 0.01),
            FeedbackRichness::Full
        );
    }

    #[test]
    fn richness_at_threshold_is_brief() {
        assert_eq!(
            feedback_richness(PROFICIENT_THRESHOLD),
            FeedbackRichness::Brief
        );
    }

    #[test]
    fn richness_above_threshold_is_brief() {
        assert_eq!(
            feedback_richness(PROFICIENT_THRESHOLD + 0.01),
            FeedbackRichness::Brief
        );
        assert_eq!(feedback_richness(1.0), FeedbackRichness::Brief);
    }

    #[test]
    fn retest_delay_follows_expanding_schedule() {
        assert_eq!(next_retest_delay_days(0), 3.0);
        assert_eq!(next_retest_delay_days(1), 7.0);
        assert_eq!(next_retest_delay_days(2), 16.0);
        assert_eq!(next_retest_delay_days(3), 35.0);
    }

    #[test]
    fn retest_delay_clamps_to_last_entry() {
        assert_eq!(next_retest_delay_days(4), 35.0);
        assert_eq!(next_retest_delay_days(9), 35.0);
        assert_eq!(next_retest_delay_days(u32::MAX), 35.0);
    }

    #[test]
    fn retest_due_uses_rounded_delay_offset() {
        let now_ms = 1_700_000_000_000;
        for prior in [0u32, 1, 2, 3, 9] {
            let expected =
                now_ms + (next_retest_delay_days(prior) * MS_PER_DAY as f32).round() as i64;
            assert_eq!(retest_due_at_ms(now_ms, prior), expected);
        }
        // First re-test: 3 days out.
        assert_eq!(retest_due_at_ms(now_ms, 0), now_ms + 3 * MS_PER_DAY);
    }

    #[test]
    fn aggregate_counts_totals_and_per_kind_misses() {
        let rows = [
            row("Amino Acids", DIAGNOSIS_MEMORY, false),
            row("Amino Acids", DIAGNOSIS_REASONING, false),
            row("Thermodynamics", DIAGNOSIS_REASONING, true),
            row("Passage A", DIAGNOSIS_PASSAGE, false),
            row("Timing Drill", DIAGNOSIS_TEST_TAKING, false),
            row("Kinetics", DIAGNOSIS_REASONING, false),
        ];
        let report = aggregate_report(&rows);
        assert_eq!(report.total, 6);
        assert_eq!(report.correct, 1);
        assert_eq!(report.memory_misses, 1);
        assert_eq!(report.reasoning_misses, 2);
        assert_eq!(report.passage_misses, 1);
        assert_eq!(report.test_taking_misses, 1);
    }

    #[test]
    fn weak_topics_ordered_by_miss_count_then_name() {
        let rows = [
            row("Kinetics", DIAGNOSIS_REASONING, false),
            row("Kinetics", DIAGNOSIS_MEMORY, false),
            row("Kinetics", DIAGNOSIS_PASSAGE, false),
            // Tie on one miss each: "Acids" precedes "Zwitterions" by name.
            row("Zwitterions", DIAGNOSIS_MEMORY, false),
            row("Acids", DIAGNOSIS_REASONING, false),
            // Correct rows never contribute a weak topic.
            row("Enzymes", DIAGNOSIS_REASONING, true),
        ];
        let report = aggregate_report(&rows);
        assert_eq!(
            report.weak_topics,
            vec![
                "Kinetics".to_string(),
                "Acids".to_string(),
                "Zwitterions".to_string(),
            ]
        );
    }

    #[test]
    fn all_correct_yields_no_weak_topics() {
        let rows = [
            row("Kinetics", DIAGNOSIS_CORRECT, true),
            row("Acids", DIAGNOSIS_CORRECT, true),
        ];
        let report = aggregate_report(&rows);
        assert_eq!(report.total, 2);
        assert_eq!(report.correct, 2);
        assert_eq!(report.memory_misses, 0);
        assert_eq!(report.reasoning_misses, 0);
        assert_eq!(report.passage_misses, 0);
        assert_eq!(report.test_taking_misses, 0);
        assert!(report.weak_topics.is_empty());
    }

    #[test]
    fn empty_input_yields_zeroed_report() {
        let report = aggregate_report(&[]);
        assert_eq!(
            report,
            FeedbackReport {
                total: 0,
                correct: 0,
                memory_misses: 0,
                reasoning_misses: 0,
                passage_misses: 0,
                test_taking_misses: 0,
                weak_topics: Vec::new(),
            }
        );
    }
}
