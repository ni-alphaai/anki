// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Readiness scoring (AI-off).
//!
//! Speedrun separates three signals that other tools blend:
//! - **memory**: can you recall a fact (from the SRS substrate),
//! - **performance**: can you apply it on an exam-style question,
//! - **readiness**: what would you score today, honestly, on the MCAT scale.
//!
//! The computation is a deliberately simple, transparent v1 (placeholder
//! calibration; real Brier/log-loss calibration lands later). The important
//! honest property is the **give-up rule**: readiness refuses to commit to a
//! number until there is enough evidence.

// MCAT total-score scale.
pub const SCALE_LOW: u32 = 472;
pub const SCALE_HIGH: u32 = 528;
const SCALE_SPAN: f32 = (SCALE_HIGH - SCALE_LOW) as f32;

// Give-up-rule thresholds (conservative placeholders).
pub const MIN_GRADED_ATTEMPTS: u32 = 30;
pub const MIN_EXAM_ATTEMPTS: u32 = 20;
pub const MIN_REVIEW_CARDS: u32 = 20;
pub const MIN_COVERAGE: f32 = 0.5;

/// Aggregated evidence used to compute readiness. Gathered from the collection
/// (cards) and Speedrun evidence (attempts, topic map).
#[derive(Debug, Clone, Copy, Default)]
pub struct ReadinessInputs {
    pub review_cards: u32,
    pub mature_cards: u32,
    /// Exam-style attempts (question_type != 0).
    pub exam_attempts: u32,
    pub exam_correct: u32,
    /// All recorded attempts.
    pub graded_attempts: u32,
    pub topics_total: u32,
    pub topics_covered: u32,
    /// Weight-weighted coverage (0..1). Lets the give-up rule catch a deck that
    /// covers many low-weight topics but skips a whole high-weight section.
    pub weighted_coverage: f32,
}

/// The computed three-score report.
#[derive(Debug, Clone, PartialEq)]
pub struct ReadinessReport {
    pub memory: f32,
    pub performance: f32,
    /// memory - performance; positive means recall outruns application.
    pub recall_perf_gap: f32,
    pub coverage: f32,
    pub readiness_scaled: u32,
    pub low_scaled: u32,
    pub high_scaled: u32,
    /// Whether the give-up rule was satisfied.
    pub sufficient: bool,
    pub reason: String,
    /// Per-dimension sufficiency (the brainlift's "refuse when either dimension
    /// is thin") and the dimension currently blocking confidence.
    pub memory_sufficient: bool,
    pub performance_sufficient: bool,
    pub blocking_dimension: String,
}

fn ratio(num: u32, denom: u32) -> f32 {
    if denom > 0 {
        num as f32 / denom as f32
    } else {
        0.0
    }
}

/// Compute the three scores from aggregated evidence.
pub fn compute_readiness(inputs: &ReadinessInputs) -> ReadinessReport {
    let memory = ratio(inputs.mature_cards, inputs.review_cards);
    let performance = ratio(inputs.exam_correct, inputs.exam_attempts);
    let coverage = ratio(inputs.topics_covered, inputs.topics_total);
    let recall_perf_gap = memory - performance;

    // Composite weights memory, performance, and coverage. Placeholder model.
    let composite = 0.4 * memory + 0.4 * performance + 0.2 * coverage;
    let scaled = SCALE_LOW as f32 + composite * SCALE_SPAN;

    // Range half-width: a normal-approx on the performance proportion, scaled to
    // the MCAT span. With no exam evidence the band is deliberately wide.
    let half_width_scaled = if inputs.exam_attempts > 0 {
        let p = performance;
        SCALE_SPAN * 1.96 * (p * (1.0 - p) / inputs.exam_attempts as f32).sqrt()
    } else {
        SCALE_SPAN / 2.0
    };

    let readiness_scaled = scaled.round().clamp(SCALE_LOW as f32, SCALE_HIGH as f32) as u32;
    let low_scaled =
        (scaled - half_width_scaled).round().clamp(SCALE_LOW as f32, SCALE_HIGH as f32) as u32;
    let high_scaled =
        (scaled + half_width_scaled).round().clamp(SCALE_LOW as f32, SCALE_HIGH as f32) as u32;

    let mut missing = Vec::new();
    if inputs.graded_attempts < MIN_GRADED_ATTEMPTS {
        missing.push(format!(
            "graded attempts {}/{}",
            inputs.graded_attempts, MIN_GRADED_ATTEMPTS
        ));
    }
    if inputs.exam_attempts < MIN_EXAM_ATTEMPTS {
        missing.push(format!(
            "exam-style attempts {}/{}",
            inputs.exam_attempts, MIN_EXAM_ATTEMPTS
        ));
    }
    if inputs.review_cards < MIN_REVIEW_CARDS {
        missing.push(format!(
            "review cards {}/{}",
            inputs.review_cards, MIN_REVIEW_CARDS
        ));
    }
    // Gate on BOTH raw and weighted coverage: a deck can cover many low-weight
    // topics yet skip a whole high-weight section (raw looks fine, weighted does
    // not). Readiness must abstain in that case, so block on the weaker of the two.
    let effective_coverage = coverage.min(inputs.weighted_coverage);
    if effective_coverage < MIN_COVERAGE {
        missing.push(format!(
            "topic coverage {:.0}%/{:.0}% (weighted {:.0}%)",
            coverage * 100.0,
            MIN_COVERAGE * 100.0,
            inputs.weighted_coverage * 100.0
        ));
    }

    let sufficient = missing.is_empty();
    let reason = if sufficient {
        "enough evidence to estimate readiness".to_string()
    } else {
        format!("not enough evidence: need {}", missing.join(", "))
    };

    let memory_sufficient = inputs.review_cards >= MIN_REVIEW_CARDS;
    let performance_sufficient = inputs.exam_attempts >= MIN_EXAM_ATTEMPTS;
    // Dimension-first: surface the weakest evidence dimension before generic gaps.
    let blocking_dimension = if !memory_sufficient {
        "memory"
    } else if !performance_sufficient {
        "performance"
    } else if effective_coverage < MIN_COVERAGE {
        "coverage"
    } else if inputs.graded_attempts < MIN_GRADED_ATTEMPTS {
        "attempts"
    } else {
        "none"
    };

    ReadinessReport {
        memory,
        performance,
        recall_perf_gap,
        coverage,
        readiness_scaled,
        low_scaled,
        high_scaled,
        sufficient,
        reason,
        memory_sufficient,
        performance_sufficient,
        blocking_dimension: blocking_dimension.to_string(),
    }
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn abstains_without_enough_evidence() {
        let report = compute_readiness(&ReadinessInputs::default());
        assert!(!report.sufficient);
        assert!(report.reason.contains("not enough evidence"));
    }

    #[test]
    fn sufficient_evidence_produces_in_range_score() {
        let inputs = ReadinessInputs {
            review_cards: 100,
            mature_cards: 70,
            exam_attempts: 50,
            exam_correct: 40,
            graded_attempts: 60,
            topics_total: 10,
            topics_covered: 8,
            weighted_coverage: 0.8,
        };
        let report = compute_readiness(&inputs);
        assert!(report.sufficient, "reason: {}", report.reason);
        assert!((report.memory - 0.7).abs() < 1e-6);
        assert!((report.performance - 0.8).abs() < 1e-6);
        assert!(report.readiness_scaled >= SCALE_LOW && report.readiness_scaled <= SCALE_HIGH);
        assert!(report.low_scaled <= report.readiness_scaled);
        assert!(report.high_scaled >= report.readiness_scaled);
    }

    #[test]
    fn recall_performance_gap_is_signed_difference() {
        let inputs = ReadinessInputs {
            review_cards: 100,
            mature_cards: 90,
            exam_attempts: 50,
            exam_correct: 25,
            graded_attempts: 60,
            topics_total: 10,
            topics_covered: 9,
            weighted_coverage: 0.9,
        };
        let report = compute_readiness(&inputs);
        // memory 0.9, performance 0.5 -> gap 0.4 (recall outruns application)
        assert!((report.recall_perf_gap - 0.4).abs() < 1e-6);
    }

    #[test]
    fn low_coverage_blocks_sufficiency() {
        let inputs = ReadinessInputs {
            review_cards: 100,
            mature_cards: 70,
            exam_attempts: 50,
            exam_correct: 40,
            graded_attempts: 60,
            topics_total: 10,
            topics_covered: 2, // 20% < 50%
            weighted_coverage: 0.2,
        };
        let report = compute_readiness(&inputs);
        assert!(!report.sufficient);
        assert!(report.reason.contains("topic coverage"));
    }

    #[test]
    fn high_raw_but_low_weighted_coverage_blocks() {
        // A deck that covers many topics by count (54.8%) but skips a whole
        // high-weight section (weighted 43.9%) must NOT show ready. This is the
        // spec's "10,000-card deck that skips a high-weight section" case.
        let inputs = ReadinessInputs {
            review_cards: 100,
            mature_cards: 70,
            exam_attempts: 50,
            exam_correct: 40,
            graded_attempts: 60,
            topics_total: 31,
            topics_covered: 17, // 54.8% by count -> passes the raw floor
            weighted_coverage: 0.439, // but a heavy section is missing
        };
        let report = compute_readiness(&inputs);
        assert!(!report.sufficient);
        assert_eq!(report.blocking_dimension, "coverage");
        assert!(report.reason.contains("weighted"));
    }
}
