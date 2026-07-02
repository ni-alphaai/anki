// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Topic-aware interleaving (AI-off).
//!
//! Distributed/interleaved practice beats blocked practice on the criterion
//! test (Dunlosky; Kornell & Bjork). This round-robins due cards across their
//! topic groups so the queue alternates topics instead of presenting long
//! same-topic blocks, while preserving the within-group order (which already
//! reflects the points-at-stake ranking).

use std::collections::HashMap;
use std::collections::VecDeque;

/// Given a group key per item (in queue order), return the item indices
/// reordered by round-robin across groups. Group order follows first
/// appearance; within-group order is preserved.
pub fn interleave_indices(group_keys: &[usize]) -> Vec<usize> {
    let mut appearance: Vec<usize> = Vec::new();
    let mut groups: HashMap<usize, VecDeque<usize>> = HashMap::new();
    for (idx, &key) in group_keys.iter().enumerate() {
        if !groups.contains_key(&key) {
            appearance.push(key);
        }
        groups.entry(key).or_default().push_back(idx);
    }

    let mut out = Vec::with_capacity(group_keys.len());
    let mut remaining = group_keys.len();
    while remaining > 0 {
        for key in &appearance {
            if let Some(queue) = groups.get_mut(key) {
                if let Some(idx) = queue.pop_front() {
                    out.push(idx);
                    remaining -= 1;
                }
            }
        }
    }
    out
}

/// Selective interleave: interleave *within* groups while keeping groups as
/// contiguous blocks.
///
/// `groups[i]` is the group key of item `i` (e.g., its parent concept), and
/// `topics[i]` its topic key (e.g., the child subtopic). Groups are emitted as
/// blocks in order of first appearance, which preserves the incoming ordering
/// across groups (e.g., points-at-stake weakness order). Within a block that
/// spans two or more distinct topics, items are round-robined across those
/// topics (preserving within-topic order) to juxtapose confusable siblings;
/// single-topic blocks (and singletons / rote / flat-tag groups) pass through
/// unchanged. This is the selective realization of the interleaving evidence:
/// interleave confusable siblings, keep unrelated concepts blocked.
pub fn interleave_grouped_indices(groups: &[usize], topics: &[usize]) -> Vec<usize> {
    debug_assert_eq!(groups.len(), topics.len());

    let mut group_order: Vec<usize> = Vec::new();
    let mut group_items: HashMap<usize, Vec<usize>> = HashMap::new();
    for (idx, &g) in groups.iter().enumerate() {
        if !group_items.contains_key(&g) {
            group_order.push(g);
        }
        group_items.entry(g).or_default().push(idx);
    }

    let mut out = Vec::with_capacity(groups.len());
    for g in &group_order {
        let items = &group_items[g];

        // Bucket the group's items by topic (topic order = first appearance).
        let mut topic_order: Vec<usize> = Vec::new();
        let mut topic_queues: HashMap<usize, VecDeque<usize>> = HashMap::new();
        for &idx in items {
            let t = topics[idx];
            if !topic_queues.contains_key(&t) {
                topic_order.push(t);
            }
            topic_queues.entry(t).or_default().push_back(idx);
        }

        if topic_order.len() < 2 {
            // Single topic (or singleton) -> keep blocked, unchanged.
            out.extend(items.iter().copied());
            continue;
        }

        // Two or more confusable siblings -> round-robin across topics.
        let mut remaining = items.len();
        while remaining > 0 {
            for t in &topic_order {
                if let Some(queue) = topic_queues.get_mut(t) {
                    if let Some(idx) = queue.pop_front() {
                        out.push(idx);
                        remaining -= 1;
                    }
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn alternates_two_topics() {
        // [A, A, B, B] -> A, B, A, B
        assert_eq!(interleave_indices(&[0, 0, 1, 1]), vec![0, 2, 1, 3]);
    }

    #[test]
    fn preserves_within_group_order() {
        // group 0 has idx 0,1,2; group 1 has idx 3 -> 0,3,1,2
        assert_eq!(interleave_indices(&[0, 0, 0, 1]), vec![0, 3, 1, 2]);
    }

    #[test]
    fn single_group_is_unchanged() {
        assert_eq!(interleave_indices(&[5, 5, 5]), vec![0, 1, 2]);
    }

    #[test]
    fn empty_is_empty() {
        assert_eq!(interleave_indices(&[]), Vec::<usize>::new());
    }

    #[test]
    fn grouped_interleaves_siblings_within_group() {
        // one group (0), two sibling topics a,a,b,b -> a,b,a,b
        let g = [0, 0, 0, 0];
        let t = [0, 0, 1, 1];
        assert_eq!(interleave_grouped_indices(&g, &t), vec![0, 2, 1, 3]);
    }

    #[test]
    fn grouped_keeps_groups_blocked() {
        // input alternates groups 0,1,0,1 (each a single topic) -> groups are
        // emitted as contiguous blocks, not interleaved across each other.
        let g = [0, 1, 0, 1];
        let t = [0, 1, 0, 1];
        assert_eq!(interleave_grouped_indices(&g, &t), vec![0, 2, 1, 3]);
    }

    #[test]
    fn grouped_interleaves_within_and_blocks_across() {
        // group 0: topics [a,a,b] -> a,b,a (idx 0,2,1); group 1: single topic
        // c,c -> unchanged (idx 3,4). Blocks stay separated.
        let g = [0, 0, 0, 1, 1];
        let t = [0, 0, 1, 2, 2];
        assert_eq!(interleave_grouped_indices(&g, &t), vec![0, 2, 1, 3, 4]);
    }

    #[test]
    fn grouped_singleton_group_unchanged() {
        let g = [7, 7, 7];
        let t = [5, 5, 5];
        assert_eq!(interleave_grouped_indices(&g, &t), vec![0, 1, 2]);
    }

    #[test]
    fn grouped_empty_is_empty() {
        assert_eq!(interleave_grouped_indices(&[], &[]), Vec::<usize>::new());
    }
}
