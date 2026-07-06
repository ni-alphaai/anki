// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Topic-coverage map (AI-off).
//!
//! Speedrun maps the user's deck onto a topic outline (e.g., the MCAT content
//! outline) so it can report how much of the exam the deck actually covers, and
//! so readiness can abstain when coverage is too thin. A topic is "covered"
//! only when it holds at least `MIN_CARDS_PER_TOPIC` tagged cards, so a lone
//! incidental card cannot light up a whole topic and inflate coverage.

/// Minimum tagged cards before a topic counts as "covered". One (or two)
/// incidental cards do not represent a topic's breadth, so the bar sits at a
/// small but non-trivial floor: high enough to reject topics that are merely
/// touched, low enough not to penalise a genuinely (if lightly) studied topic.
pub const MIN_CARDS_PER_TOPIC: u32 = 3;

/// One topic's coverage evidence.
#[derive(Debug, Clone, Copy)]
pub struct TopicCoverageRow {
    pub weight: f32,
    pub cards: u32,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CoverageSummary {
    pub topics_total: u32,
    pub topics_covered: u32,
    /// covered / total, 0..1.
    pub coverage: f32,
    /// weight-weighted coverage, 0..1.
    pub weighted_coverage: f32,
}

pub fn summarize_coverage(rows: &[TopicCoverageRow]) -> CoverageSummary {
    let topics_total = rows.len() as u32;
    let mut topics_covered = 0u32;
    let mut weight_sum = 0.0f32;
    let mut covered_weight = 0.0f32;

    for row in rows {
        let covered = row.cards >= MIN_CARDS_PER_TOPIC;
        if covered {
            topics_covered += 1;
            covered_weight += row.weight;
        }
        weight_sum += row.weight;
    }

    let coverage = if topics_total > 0 {
        topics_covered as f32 / topics_total as f32
    } else {
        0.0
    };
    let weighted_coverage = if weight_sum > 0.0 {
        covered_weight / weight_sum
    } else {
        0.0
    };

    CoverageSummary {
        topics_total,
        topics_covered,
        coverage,
        weighted_coverage,
    }
}

/// A built-in starter outline: the MCAT's ten Foundational Concepts. This is a
/// coarse seed so coverage works out of the box; users can replace it with a
/// finer outline (matching their deck's tags) via SetTopicMap.
pub const MCAT_FOUNDATIONAL_CONCEPTS: &[(&str, &str, f32)] = &[
    ("fc1", "Biomolecules: structure and function", 1.0),
    ("fc2", "Cells: structure, function, and assemblies", 1.0),
    ("fc3", "Organ systems and homeostasis", 1.0),
    ("fc4", "Physical principles of living systems", 1.0),
    ("fc5", "Chemical principles of biological systems", 1.0),
    ("fc6", "Sensing and processing the environment", 1.0),
    ("fc7", "Behavior and behavior change", 1.0),
    ("fc8", "Self-identity and social thinking", 1.0),
    ("fc9", "Social structure and demographics", 1.0),
    ("fc10", "Social inequality and resource access", 1.0),
];

#[cfg(test)]
mod test {
    use super::*;

    fn row(weight: f32, cards: u32) -> TopicCoverageRow {
        TopicCoverageRow { weight, cards }
    }

    #[test]
    fn empty_outline_has_zero_coverage() {
        let summary = summarize_coverage(&[]);
        assert_eq!(summary.topics_total, 0);
        assert_eq!(summary.coverage, 0.0);
    }

    #[test]
    fn coverage_counts_topics_with_cards() {
        // Only the topic at/above MIN_CARDS_PER_TOPIC counts; the 1-card topic
        // is too thin to be covered.
        let rows = [row(1.0, 3), row(1.0, 0), row(1.0, 1), row(1.0, 0)];
        let summary = summarize_coverage(&rows);
        assert_eq!(summary.topics_total, 4);
        assert_eq!(summary.topics_covered, 1);
        assert!((summary.coverage - 0.25).abs() < 1e-6);
    }

    #[test]
    fn thin_topics_below_the_bar_are_not_covered() {
        let rows = [row(1.0, 1), row(1.0, 2), row(1.0, MIN_CARDS_PER_TOPIC)];
        let summary = summarize_coverage(&rows);
        assert_eq!(summary.topics_total, 3);
        assert_eq!(summary.topics_covered, 1);
        assert!((summary.coverage - 1.0 / 3.0).abs() < 1e-6);
    }

    #[test]
    fn weighted_coverage_respects_weights() {
        // Heavy topic clears the bar, light topic is below it (2 < 3) -> the
        // weighted figure outruns the unweighted one.
        let rows = [row(3.0, 5), row(1.0, 2)];
        let summary = summarize_coverage(&rows);
        assert!((summary.coverage - 0.5).abs() < 1e-6);
        assert!((summary.weighted_coverage - 0.75).abs() < 1e-6);
    }

    #[test]
    fn starter_outline_is_nonempty() {
        assert_eq!(MCAT_FOUNDATIONAL_CONCEPTS.len(), 10);
    }
}
