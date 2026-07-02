// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Performance signal and the recall-vs-performance gap (AI-off).
//!
//! Speedrun separates **recall** (can you remember a fact, from the SRS
//! substrate) from **performance** (can you apply it on an exam-style question).
//! The held-out "paraphrase test" answers reworded questions whose source card
//! the SRS already tracks; if recall and performance are basically equal there
//! is no gap and the bridge from memory to application has not been built.
//!
//! Held-out discipline is structural: question items live in `sr_question_items`
//! and are never added to the collection as cards, so answering them does not
//! leak into the source card's scheduling.

/// Mature-interval threshold (days) used as the binary recall proxy, matching
/// Anki's "mature card" convention.
pub const MATURE_INTERVAL_DAYS: i64 = 21;

/// Minimum cards with exam-style attempts before the gap is meaningful.
pub const MIN_CARDS_FOR_GAP: u32 = 5;

/// One source card's evidence: exam-style attempts, how many were correct, and
/// the card's SRS interval (for the recall proxy).
#[derive(Debug, Clone, Copy)]
pub struct PerfCardRow {
    pub attempts: u32,
    pub correct: u32,
    pub interval_days: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PerformanceSummary {
    pub cards_evaluated: u32,
    pub exam_attempts: u32,
    /// Mean SRS recall (mature proxy) over evaluated cards, 0..1.
    pub recall_rate: f32,
    /// Mean exam-style accuracy over evaluated cards, 0..1.
    pub performance_rate: f32,
    /// recall_rate - performance_rate; positive means recall outruns application.
    pub recall_perf_gap: f32,
    /// True only when there are enough cards to trust the gap.
    pub sufficient: bool,
    pub note: String,
}

/// Summarise the recall-vs-performance gap across source cards.
///
/// recall per card = 1.0 if the source card is mature else 0.0;
/// performance per card = correct / attempts. Both are averaged per card (not
/// per attempt) so a heavily-drilled card doesn't dominate.
pub fn summarize_performance(rows: &[PerfCardRow]) -> PerformanceSummary {
    let mut recall_sum = 0.0f32;
    let mut perf_sum = 0.0f32;
    let mut exam_attempts = 0u32;
    let mut evaluated = 0u32;

    for row in rows {
        if row.attempts == 0 {
            continue;
        }
        evaluated += 1;
        exam_attempts += row.attempts;
        recall_sum += if row.interval_days >= MATURE_INTERVAL_DAYS {
            1.0
        } else {
            0.0
        };
        perf_sum += row.correct as f32 / row.attempts as f32;
    }

    let (recall_rate, performance_rate) = if evaluated > 0 {
        (recall_sum / evaluated as f32, perf_sum / evaluated as f32)
    } else {
        (0.0, 0.0)
    };
    let recall_perf_gap = recall_rate - performance_rate;
    let sufficient = evaluated >= MIN_CARDS_FOR_GAP;

    let note = if !sufficient {
        format!(
            "not enough evidence: {}/{} cards with exam-style attempts",
            evaluated, MIN_CARDS_FOR_GAP
        )
    } else if recall_perf_gap > 0.1 {
        "recall outruns performance: memory-to-application bridge is weak".to_string()
    } else if recall_perf_gap < -0.1 {
        "performance outruns recall (unusual; check leakage)".to_string()
    } else {
        "recall and performance are aligned".to_string()
    };

    PerformanceSummary {
        cards_evaluated: evaluated,
        exam_attempts,
        recall_rate,
        performance_rate,
        recall_perf_gap,
        sufficient,
        note,
    }
}

#[cfg(test)]
mod test {
    use super::*;

    fn row(attempts: u32, correct: u32, interval_days: i64) -> PerfCardRow {
        PerfCardRow {
            attempts,
            correct,
            interval_days,
        }
    }

    #[test]
    fn abstains_without_enough_cards() {
        let rows = [row(2, 1, 30), row(2, 2, 30)];
        let summary = summarize_performance(&rows);
        assert!(!summary.sufficient);
        assert!(summary.note.contains("not enough evidence"));
    }

    #[test]
    fn detects_gap_when_recall_beats_performance() {
        // 6 mature cards (recall 1.0) but only half the questions correct (perf 0.5)
        let rows: Vec<PerfCardRow> = (0..6).map(|_| row(2, 1, 30)).collect();
        let summary = summarize_performance(&rows);
        assert!(summary.sufficient);
        assert!((summary.recall_rate - 1.0).abs() < 1e-6);
        assert!((summary.performance_rate - 0.5).abs() < 1e-6);
        assert!((summary.recall_perf_gap - 0.5).abs() < 1e-6);
        assert!(summary.note.contains("bridge is weak"));
    }

    #[test]
    fn aligned_when_recall_matches_performance() {
        // 6 mature cards, all questions correct -> no gap
        let rows: Vec<PerfCardRow> = (0..6).map(|_| row(2, 2, 30)).collect();
        let summary = summarize_performance(&rows);
        assert!(summary.sufficient);
        assert!(summary.recall_perf_gap.abs() < 1e-6);
        assert!(summary.note.contains("aligned"));
    }

    #[test]
    fn ignores_cards_without_attempts() {
        let rows = [row(0, 0, 30), row(2, 1, 30)];
        let summary = summarize_performance(&rows);
        assert_eq!(summary.cards_evaluated, 1);
        assert_eq!(summary.exam_attempts, 2);
    }
}
