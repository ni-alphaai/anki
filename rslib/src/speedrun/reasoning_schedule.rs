// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Scheduled reasoning-due queue (D1).
//!
//! Promotes reasoning practice from an end-of-session offer to a scheduled
//! queue. Each topic accrues a "reasoning debt" from three signals: the
//! recall-vs-performance gap (memory outrunning application - see
//! `performance.rs`), how little of the topic is covered, and how long since
//! its last reasoning attempt. Weak, high-debt topics surface first,
//! interleaved with FSRS review. Pure and DB-free so it is unit-testable; the
//! DB fetch + interleave wiring lives in `service.rs`.

/// Weight on the recall-vs-performance gap (the core bridge-weakness signal).
pub const GAP_WEIGHT: f32 = 1.0;
/// Weight on the uncovered fraction of a topic.
pub const COVERAGE_WEIGHT: f32 = 0.5;
/// Weight on reasoning recency (days since last reasoning attempt, saturating).
pub const RECENCY_WEIGHT: f32 = 0.25;
/// Days at which the recency term saturates to 1.0.
pub const RECENCY_SATURATION_DAYS: f32 = 30.0;
/// A topic must accrue at least this much debt to be considered due.
pub const MIN_REASONING_DEBT: f32 = 0.15;

/// Per-topic inputs for the reasoning-debt score.
#[derive(Debug, Clone)]
pub struct TopicReasoningState {
    pub topic: String,
    /// recall_rate - performance_rate for this topic (see `performance.rs`);
    /// positive means memory outruns application (a weak bridge).
    pub recall_perf_gap: f32,
    /// Fraction of the topic covered, 0.0..=1.0.
    pub coverage: f32,
    /// Days since the last reasoning attempt on this topic (large if never).
    pub days_since_last_reasoning: f32,
    /// How many held-out reasoning items are available for this topic.
    pub open_questions: u32,
}

/// A topic that is due for reasoning practice, with its debt score.
#[derive(Debug, Clone, PartialEq)]
pub struct DueTopic {
    pub topic: String,
    pub debt: f32,
}

/// Compute a topic's reasoning debt.
///
/// Returns 0.0 when there are no held-out questions to practice (nothing to
/// schedule) so the caller abstains cleanly. Only a positive gap contributes
/// (performance ahead of recall is not a bridge weakness); the coverage term
/// rewards under-covered topics; the recency term saturates at
/// `RECENCY_SATURATION_DAYS`.
pub fn reasoning_debt(state: &TopicReasoningState) -> f32 {
    if state.open_questions == 0 {
        return 0.0;
    }

    let gap_term = GAP_WEIGHT * state.recall_perf_gap.max(0.0);
    let coverage_term = COVERAGE_WEIGHT * (1.0 - state.coverage.clamp(0.0, 1.0));
    let recency_term = RECENCY_WEIGHT
        * (state.days_since_last_reasoning / RECENCY_SATURATION_DAYS).clamp(0.0, 1.0);

    gap_term + coverage_term + recency_term
}

/// Rank topics by descending reasoning debt, keeping only those at or above
/// `min_debt`. Deterministic tie-break by topic name so the ordering is stable.
pub fn rank_due_topics(states: &[TopicReasoningState], min_debt: f32) -> Vec<DueTopic> {
    let mut due: Vec<DueTopic> = states
        .iter()
        .filter(|state| state.open_questions > 0)
        .filter_map(|state| {
            let debt = reasoning_debt(state);
            (debt >= min_debt).then(|| DueTopic {
                topic: state.topic.clone(),
                debt,
            })
        })
        .collect();

    due.sort_by(|a, b| {
        b.debt
            .partial_cmp(&a.debt)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.topic.cmp(&b.topic))
    });

    due
}

#[cfg(test)]
mod test {
    use super::*;

    const EPS: f32 = 1e-6;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < EPS
    }

    /// A neutral baseline state: fully covered, no gap, just practiced, with
    /// open questions available. Its debt is exactly 0.0.
    fn baseline() -> TopicReasoningState {
        TopicReasoningState {
            topic: "baseline".to_string(),
            recall_perf_gap: 0.0,
            coverage: 1.0,
            days_since_last_reasoning: 0.0,
            open_questions: 5,
        }
    }

    #[test]
    fn baseline_state_has_zero_debt() {
        assert!(approx(reasoning_debt(&baseline()), 0.0));
    }

    #[test]
    fn debt_increases_with_positive_gap() {
        let mut state = baseline();
        state.recall_perf_gap = 0.2;
        let low = reasoning_debt(&state);
        state.recall_perf_gap = 0.5;
        let high = reasoning_debt(&state);

        assert!(high > low);
        // Only the gap term is active here, weighted by GAP_WEIGHT.
        assert!(approx(low, GAP_WEIGHT * 0.2));
        assert!(approx(high, GAP_WEIGHT * 0.5));
    }

    #[test]
    fn negative_gap_contributes_nothing() {
        // A negative gap (performance ahead of recall) must not add debt; only
        // the coverage and recency terms should remain.
        let mut state = baseline();
        state.recall_perf_gap = -0.9;
        state.coverage = 0.25;
        state.days_since_last_reasoning = 15.0;

        let expected =
            COVERAGE_WEIGHT * (1.0 - 0.25) + RECENCY_WEIGHT * (15.0 / RECENCY_SATURATION_DAYS);
        assert!(approx(reasoning_debt(&state), expected));

        // Zeroing the (already non-positive) gap leaves debt unchanged.
        let mut zero_gap = state.clone();
        zero_gap.recall_perf_gap = 0.0;
        assert!(approx(reasoning_debt(&state), reasoning_debt(&zero_gap)));
    }

    #[test]
    fn debt_decreases_as_coverage_increases() {
        let mut state = baseline();
        state.coverage = 0.0;
        let uncovered = reasoning_debt(&state);
        state.coverage = 0.5;
        let half = reasoning_debt(&state);
        state.coverage = 1.0;
        let covered = reasoning_debt(&state);

        assert!(uncovered > half);
        assert!(half > covered);
        assert!(approx(uncovered, COVERAGE_WEIGHT * 1.0));
        assert!(approx(half, COVERAGE_WEIGHT * 0.5));
        assert!(approx(covered, 0.0));
    }

    #[test]
    fn coverage_is_clamped_to_unit_range() {
        // Coverage above 1.0 clamps to 1.0 (no negative coverage term); below
        // 0.0 clamps to 0.0 (full coverage term).
        let mut over = baseline();
        over.coverage = 2.5;
        assert!(approx(reasoning_debt(&over), 0.0));

        let mut under = baseline();
        under.coverage = -1.0;
        assert!(approx(reasoning_debt(&under), COVERAGE_WEIGHT * 1.0));
    }

    #[test]
    fn debt_increases_with_recency_and_saturates() {
        let mut state = baseline();
        state.days_since_last_reasoning = 0.0;
        let fresh = reasoning_debt(&state);
        state.days_since_last_reasoning = RECENCY_SATURATION_DAYS / 2.0;
        let mid = reasoning_debt(&state);
        state.days_since_last_reasoning = RECENCY_SATURATION_DAYS;
        let saturated = reasoning_debt(&state);
        state.days_since_last_reasoning = RECENCY_SATURATION_DAYS * 10.0;
        let beyond = reasoning_debt(&state);

        assert!(mid > fresh);
        assert!(saturated > mid);
        assert!(approx(fresh, 0.0));
        assert!(approx(mid, RECENCY_WEIGHT * 0.5));
        assert!(approx(saturated, RECENCY_WEIGHT));
        // At/above saturation the recency term stays capped at RECENCY_WEIGHT.
        assert!(approx(beyond, saturated));
    }

    #[test]
    fn negative_recency_clamps_to_zero() {
        let mut state = baseline();
        state.days_since_last_reasoning = -5.0;
        assert!(approx(reasoning_debt(&state), 0.0));
    }

    #[test]
    fn no_open_questions_yields_exactly_zero_debt() {
        // Even with strong signals on every term, zero open questions => 0.0.
        let state = TopicReasoningState {
            topic: "unavailable".to_string(),
            recall_perf_gap: 5.0,
            coverage: 0.0,
            days_since_last_reasoning: 1000.0,
            open_questions: 0,
        };
        assert_eq!(reasoning_debt(&state), 0.0);
    }

    #[test]
    fn debt_sums_all_three_terms() {
        let state = TopicReasoningState {
            topic: "combined".to_string(),
            recall_perf_gap: 0.4,
            coverage: 0.25,
            days_since_last_reasoning: RECENCY_SATURATION_DAYS / 3.0,
            open_questions: 2,
        };
        let expected =
            GAP_WEIGHT * 0.4 + COVERAGE_WEIGHT * (1.0 - 0.25) + RECENCY_WEIGHT * (1.0 / 3.0);
        assert!(approx(reasoning_debt(&state), expected));
    }

    #[test]
    fn rank_filters_below_min_debt_and_zero_open_questions() {
        let states = vec![
            // Debt just above the default threshold via coverage term.
            TopicReasoningState {
                topic: "kept".to_string(),
                recall_perf_gap: 0.0,
                coverage: 0.0,
                days_since_last_reasoning: 0.0,
                open_questions: 3,
            },
            // Tiny debt, below the threshold -> filtered out.
            TopicReasoningState {
                topic: "too_small".to_string(),
                recall_perf_gap: 0.0,
                coverage: 0.9,
                days_since_last_reasoning: 0.0,
                open_questions: 3,
            },
            // High raw signals but no open questions -> filtered out.
            TopicReasoningState {
                topic: "no_questions".to_string(),
                recall_perf_gap: 1.0,
                coverage: 0.0,
                days_since_last_reasoning: 100.0,
                open_questions: 0,
            },
        ];

        let ranked = rank_due_topics(&states, MIN_REASONING_DEBT);
        assert_eq!(ranked.len(), 1);
        assert_eq!(ranked[0].topic, "kept");
        assert!(approx(ranked[0].debt, COVERAGE_WEIGHT * 1.0));
    }

    #[test]
    fn rank_orders_by_debt_desc_with_topic_tie_break() {
        // Two topics ("alpha", "bravo") share the same debt (coverage 0.5);
        // "charlie" has strictly higher debt (coverage 0.0). Expected order:
        // charlie (highest debt), then the tie alpha < bravo by name.
        let states = vec![
            TopicReasoningState {
                topic: "bravo".to_string(),
                recall_perf_gap: 0.0,
                coverage: 0.5,
                days_since_last_reasoning: 0.0,
                open_questions: 1,
            },
            TopicReasoningState {
                topic: "charlie".to_string(),
                recall_perf_gap: 0.0,
                coverage: 0.0,
                days_since_last_reasoning: 0.0,
                open_questions: 1,
            },
            TopicReasoningState {
                topic: "alpha".to_string(),
                recall_perf_gap: 0.0,
                coverage: 0.5,
                days_since_last_reasoning: 0.0,
                open_questions: 1,
            },
        ];

        let ranked = rank_due_topics(&states, 0.0);
        let order: Vec<&str> = ranked.iter().map(|t| t.topic.as_str()).collect();
        assert_eq!(order, vec!["charlie", "alpha", "bravo"]);

        assert!(approx(ranked[0].debt, COVERAGE_WEIGHT * 1.0));
        assert!(approx(ranked[1].debt, COVERAGE_WEIGHT * 0.5));
        assert!(approx(ranked[2].debt, COVERAGE_WEIGHT * 0.5));
    }

    #[test]
    fn rank_empty_input_yields_empty_output() {
        let ranked = rank_due_topics(&[], MIN_REASONING_DEBT);
        assert!(ranked.is_empty());
    }

    #[test]
    fn rank_min_debt_boundary_is_inclusive() {
        // debt exactly == min_debt is kept (>=), just below is dropped.
        let state = TopicReasoningState {
            topic: "boundary".to_string(),
            recall_perf_gap: 0.0,
            coverage: 1.0 - (MIN_REASONING_DEBT / COVERAGE_WEIGHT),
            days_since_last_reasoning: 0.0,
            open_questions: 1,
        };
        let debt = reasoning_debt(&state);
        assert!(approx(debt, MIN_REASONING_DEBT));

        assert_eq!(rank_due_topics(&[state.clone()], debt).len(), 1);
        assert_eq!(rank_due_topics(&[state], debt + EPS).len(), 0);
    }
}
