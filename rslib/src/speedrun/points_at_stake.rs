// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! The points-at-stake ranking: order due cards by how much they matter for the
//! student's next score jump, rather than purely by FSRS due order. This is the
//! engine-side scheduling change; it reads weakness evidence recorded by the
//! diagnostic engine (sr_attempts).
//!
//! v1 keeps the scoring simple and pure so it is easy to test and to reason
//! about. The live queue builder can call [`Collection::points_at_stake_rank`]
//! to reorder a set of due cards.

/// Inputs to the value score for a single due card.
#[derive(Debug, Clone, Copy)]
pub struct CardValueInputs {
    /// Official topic weight (default 1.0).
    pub topic_weight: f32,
    /// Recent miss rate for this card/concept, 0.0..=1.0.
    pub weakness: f32,
    /// Days since last review; older cards are more at risk.
    pub memory_age_days: f32,
    /// Recall strong but exam-style performance weak, 0.0..=1.0.
    pub recall_perf_gap: f32,
}

/// Higher score = should be studied sooner.
pub fn points_at_stake_score(inputs: &CardValueInputs) -> f32 {
    inputs.topic_weight * (1.0 + inputs.weakness)
        + 0.5 * inputs.recall_perf_gap
        + 0.1 * (inputs.memory_age_days.clamp(0.0, 60.0) / 60.0)
}

#[cfg(test)]
mod test {
    use super::*;

    fn inputs(topic_weight: f32, weakness: f32) -> CardValueInputs {
        CardValueInputs {
            topic_weight,
            weakness,
            memory_age_days: 0.0,
            recall_perf_gap: 0.0,
        }
    }

    #[test]
    fn score_increases_with_weakness() {
        let weak = points_at_stake_score(&inputs(1.0, 0.9));
        let strong = points_at_stake_score(&inputs(1.0, 0.1));
        assert!(weak > strong, "weaker card should score higher");
    }

    #[test]
    fn score_increases_with_topic_weight() {
        let heavy = points_at_stake_score(&inputs(2.0, 0.5));
        let light = points_at_stake_score(&inputs(1.0, 0.5));
        assert!(heavy > light, "higher-weight topic should score higher");
    }

    #[test]
    fn recall_performance_gap_raises_score() {
        let base = CardValueInputs {
            topic_weight: 1.0,
            weakness: 0.0,
            memory_age_days: 0.0,
            recall_perf_gap: 0.0,
        };
        let mut with_gap = base;
        with_gap.recall_perf_gap = 1.0;
        assert!(points_at_stake_score(&with_gap) > points_at_stake_score(&base));
    }
}
