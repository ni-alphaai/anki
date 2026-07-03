// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! The end-of-session reasoning round: after a review session (memory), pick a
//! short set of held-out questions for the concepts just reviewed (reasoning),
//! so the two halves become one loop. This module holds the pure, testable
//! pieces; the DB fetching + wiring lives in `service.rs`.

use std::collections::HashSet;

use crate::storage::SrQuestionItem;

/// Default number of questions in a reasoning round when the caller passes 0.
pub const DEFAULT_ROUND_SIZE: u32 = 5;

/// Map an Anki deck name onto an MCAT topic used by the question packs.
///
/// This is a deliberately coarse heuristic so the round is concept-relevant on
/// real decks (e.g. MileDown) whose cards are not tagged with the topic
/// outline. It matches on the leaf deck name (Anki decks are `Parent::Child`)
/// and is case-insensitive. Unknown names return `None` and fall back to unseen
/// items.
///
/// Organic chemistry maps to `general_chemistry` because that is the closest
/// topic present in the bundled packs.
pub fn deck_name_to_topic(deck_name: &str) -> Option<&'static str> {
    let leaf = deck_name
        .rsplit("::")
        .next()
        .unwrap_or(deck_name)
        .trim()
        .to_lowercase();
    if leaf.contains("biochem") {
        Some("biochemistry")
    } else if leaf.contains("biolog") {
        Some("biology")
    } else if leaf.contains("physic") {
        Some("physics")
    } else if leaf.contains("chem") {
        Some("general_chemistry")
    } else if leaf.contains("behav") || leaf.contains("psych") || leaf.contains("sociol") {
        Some("psychology_sociology")
    } else {
        None
    }
}

/// Merge the three candidate tiers in priority order (card-linked first, then
/// topic-matched, then fallback), de-duplicating by question id and capping at
/// `limit`. Pure so it can be unit-tested independently of the database.
pub fn select_round(
    card_linked: Vec<SrQuestionItem>,
    topic_matched: Vec<SrQuestionItem>,
    fallback: Vec<SrQuestionItem>,
    limit: usize,
) -> Vec<SrQuestionItem> {
    let mut seen: HashSet<i64> = HashSet::new();
    let mut out: Vec<SrQuestionItem> = Vec::new();
    for tier in [card_linked, topic_matched, fallback] {
        for item in tier {
            if out.len() >= limit {
                return out;
            }
            if seen.insert(item.id) {
                out.push(item);
            }
        }
    }
    out
}

#[cfg(test)]
mod test {
    use super::*;

    fn item(id: i64, topic: &str) -> SrQuestionItem {
        SrQuestionItem {
            id,
            cid: None,
            topic: topic.to_string(),
            provenance: 0,
            payload: "{}".to_string(),
        }
    }

    #[test]
    fn deck_name_maps_mcat_subjects() {
        assert_eq!(deck_name_to_topic("Biology"), Some("biology"));
        assert_eq!(deck_name_to_topic("Biochemistry"), Some("biochemistry"));
        assert_eq!(
            deck_name_to_topic("General Chemistry"),
            Some("general_chemistry")
        );
        assert_eq!(
            deck_name_to_topic("Organic Chemistry"),
            Some("general_chemistry")
        );
        assert_eq!(deck_name_to_topic("Physics and Math"), Some("physics"));
        assert_eq!(
            deck_name_to_topic("Behavioral"),
            Some("psychology_sociology")
        );
    }

    #[test]
    fn deck_name_uses_leaf_and_is_case_insensitive() {
        assert_eq!(
            deck_name_to_topic("MileDown's MCAT Decks::BIOLOGY"),
            Some("biology")
        );
        assert_eq!(deck_name_to_topic("Essential Equations"), None);
        assert_eq!(deck_name_to_topic("Default"), None);
    }

    #[test]
    fn select_prioritizes_card_linked_then_topic_then_fallback() {
        let round = select_round(
            vec![item(1, "biology")],
            vec![item(2, "biology")],
            vec![item(3, "physics")],
            10,
        );
        let ids: Vec<i64> = round.iter().map(|q| q.id).collect();
        assert_eq!(ids, vec![1, 2, 3]);
    }

    #[test]
    fn select_dedupes_across_tiers() {
        // id 1 appears in card-linked and topic-matched; keep it once, first.
        let round = select_round(
            vec![item(1, "biology")],
            vec![item(1, "biology"), item(2, "biology")],
            vec![item(2, "biology"), item(3, "biology")],
            10,
        );
        let ids: Vec<i64> = round.iter().map(|q| q.id).collect();
        assert_eq!(ids, vec![1, 2, 3]);
    }

    #[test]
    fn select_respects_limit() {
        let round = select_round(
            vec![item(1, "biology"), item(2, "biology")],
            vec![item(3, "biology")],
            vec![item(4, "biology")],
            2,
        );
        assert_eq!(round.len(), 2);
        assert_eq!(round[0].id, 1);
        assert_eq!(round[1].id, 2);
    }

    #[test]
    fn select_handles_empty_inputs() {
        let round = select_round(vec![], vec![], vec![], 5);
        assert!(round.is_empty());
    }
}
