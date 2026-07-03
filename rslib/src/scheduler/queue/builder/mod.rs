// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

mod burying;
mod gathering;
pub(crate) mod intersperser;
pub(crate) mod sized_chain;
mod sorting;

use std::collections::HashMap;
use std::collections::VecDeque;

use intersperser::Intersperser;
use sized_chain::SizedChain;

use super::BuryMode;
use super::CardQueues;
use super::Counts;
use super::LearningQueueEntry;
use super::MainQueueEntry;
use super::MainQueueEntryKind;
use crate::deckconfig::NewCardGatherPriority;
use crate::deckconfig::NewCardSortOrder;
use crate::deckconfig::ReviewCardOrder;
use crate::deckconfig::ReviewMix;
use crate::decks::limits::LimitTreeMap;
use crate::prelude::*;
use crate::scheduler::states::load_balancer::LoadBalancer;
use crate::scheduler::timing::SchedTimingToday;

/// Temporary holder for review cards that will be built into a queue.
#[derive(Debug, Clone, Copy)]
pub(crate) struct DueCard {
    pub id: CardId,
    pub note_id: NoteId,
    pub mtime: TimestampSecs,
    pub due: i32,
    pub current_deck_id: DeckId,
    pub original_deck_id: DeckId,
    pub kind: DueCardKind,
    pub reps: u32,
}

#[derive(Debug, Clone, Copy)]
pub(crate) enum DueCardKind {
    Review,
    Learning,
}

/// Temporary holder for new cards that will be built into a queue.
#[derive(Debug, Default, Clone, Copy)]
pub(crate) struct NewCard {
    pub id: CardId,
    pub note_id: NoteId,
    pub mtime: TimestampSecs,
    pub current_deck_id: DeckId,
    pub original_deck_id: DeckId,
    pub template_index: u32,
    pub hash: u64,
}

impl From<DueCard> for MainQueueEntry {
    fn from(c: DueCard) -> Self {
        MainQueueEntry {
            id: c.id,
            mtime: c.mtime,
            kind: match c.kind {
                DueCardKind::Review => MainQueueEntryKind::Review,
                DueCardKind::Learning => MainQueueEntryKind::InterdayLearning,
            },
        }
    }
}

impl From<NewCard> for MainQueueEntry {
    fn from(c: NewCard) -> Self {
        MainQueueEntry {
            id: c.id,
            mtime: c.mtime,
            kind: MainQueueEntryKind::New,
        }
    }
}

impl From<DueCard> for LearningQueueEntry {
    fn from(c: DueCard) -> Self {
        LearningQueueEntry {
            due: TimestampSecs(c.due as i64),
            id: c.id,
            mtime: c.mtime,
            reps: c.reps,
        }
    }
}

#[derive(Default, Clone, Debug)]
pub(super) struct QueueSortOptions {
    pub(super) new_order: NewCardSortOrder,
    pub(super) new_gather_priority: NewCardGatherPriority,
    pub(super) review_order: ReviewCardOrder,
    pub(super) day_learn_mix: ReviewMix,
    pub(super) new_review_mix: ReviewMix,
}

#[derive(Debug)]
pub(super) struct QueueBuilder {
    pub(super) new: Vec<NewCard>,
    pub(super) review: Vec<DueCard>,
    pub(super) learning: Vec<DueCard>,
    pub(super) day_learning: Vec<DueCard>,
    limits: LimitTreeMap,
    load_balancer: Option<LoadBalancer>,
    context: Context,
}

/// Data container and helper for building queues.
#[derive(Debug, Clone)]
struct Context {
    timing: SchedTimingToday,
    config_map: HashMap<DeckConfigId, DeckConfig>,
    root_deck: Deck,
    sort_options: QueueSortOptions,
    seen_note_ids: HashMap<NoteId, BuryMode>,
    deck_map: HashMap<DeckId, Deck>,
    fsrs: bool,
}

impl QueueBuilder {
    pub(super) fn new(col: &mut Collection, deck_id: DeckId) -> Result<Self> {
        let timing = col.timing_for_timestamp(TimestampSecs::now())?;
        let new_cards_ignore_review_limit = col.get_config_bool(BoolKey::NewCardsIgnoreReviewLimit);
        let apply_all_parent_limits = col.get_config_bool(BoolKey::ApplyAllParentLimits);
        let config_map = col.storage.get_deck_config_map()?;
        let root_deck = col.storage.get_deck(deck_id)?.or_not_found(deck_id)?;
        let mut decks = col.storage.child_decks(&root_deck)?;
        decks.insert(0, root_deck.clone());
        if apply_all_parent_limits {
            for parent in col.storage.parent_decks(&root_deck)? {
                decks.insert(0, parent);
            }
        }
        let limits = LimitTreeMap::build(
            &decks,
            &config_map,
            timing.days_elapsed,
            new_cards_ignore_review_limit,
        );
        let sort_options = sort_options(&root_deck, &config_map);
        let deck_map = col.storage.get_decks_map()?;

        let load_balancer = col
            .get_config_bool(BoolKey::LoadBalancerEnabled)
            .then(|| {
                let did_to_dcid = deck_map
                    .values()
                    .filter_map(|deck| Some((deck.id, deck.config_id()?)))
                    .collect::<HashMap<_, _>>();
                LoadBalancer::new(
                    timing.days_elapsed,
                    did_to_dcid,
                    col.timing_today()?.next_day_at,
                    &col.storage,
                )
            })
            .transpose()?;

        Ok(QueueBuilder {
            new: Vec::new(),
            review: Vec::new(),
            learning: Vec::new(),
            day_learning: Vec::new(),
            limits,
            load_balancer,
            context: Context {
                timing,
                config_map,
                root_deck,
                sort_options,
                seen_note_ids: HashMap::new(),
                deck_map,
                fsrs: col.get_config_bool(BoolKey::Fsrs),
            },
        })
    }

    pub(super) fn build(mut self, learn_ahead_secs: i64) -> CardQueues {
        self.sort_new();

        // intraday learning and total learn count
        let intraday_learning = sort_learning(self.learning);
        let now = TimestampSecs::now();
        let cutoff = now.adding_secs(learn_ahead_secs);
        let learn_count =
            intraday_learning.iter().filter(|e| e.due <= cutoff).count() + self.day_learning.len();
        let review_count = self.review.len();
        let new_count = self.new.len();

        // merge interday and new cards into main
        let with_interday_learn = merge_day_learning(
            self.review,
            self.day_learning,
            self.context.sort_options.day_learn_mix,
        );
        let main_iter = merge_new(
            with_interday_learn,
            self.new,
            self.context.sort_options.new_review_mix,
        );

        CardQueues {
            counts: Counts {
                new: new_count,
                review: review_count,
                learning: learn_count,
            },
            main: main_iter.collect(),
            intraday_learning,
            learn_ahead_secs,
            current_day: self.context.timing.days_elapsed,
            build_time: TimestampMillis::now(),
            load_balancer: self.load_balancer,
            current_learning_cutoff: now,
        }
    }
}

fn sort_options(deck: &Deck, config_map: &HashMap<DeckConfigId, DeckConfig>) -> QueueSortOptions {
    deck.config_id()
        .and_then(|config_id| config_map.get(&config_id))
        .map(|config| QueueSortOptions {
            new_order: config.inner.new_card_sort_order(),
            new_gather_priority: config.inner.new_card_gather_priority(),
            review_order: config.inner.review_order(),
            day_learn_mix: config.inner.interday_learning_mix(),
            new_review_mix: config.inner.new_mix(),
        })
        .unwrap_or_else(|| {
            // filtered decks do not space siblings
            QueueSortOptions {
                new_order: NewCardSortOrder::NoSort,
                ..Default::default()
            }
        })
}

fn merge_day_learning(
    reviews: Vec<DueCard>,
    day_learning: Vec<DueCard>,
    mode: ReviewMix,
) -> Box<dyn ExactSizeIterator<Item = MainQueueEntry>> {
    let day_learning_iter = day_learning.into_iter().map(Into::into);
    let reviews_iter = reviews.into_iter().map(Into::into);

    match mode {
        ReviewMix::AfterReviews => Box::new(SizedChain::new(reviews_iter, day_learning_iter)),
        ReviewMix::BeforeReviews => Box::new(SizedChain::new(day_learning_iter, reviews_iter)),
        ReviewMix::MixWithReviews => Box::new(Intersperser::new(reviews_iter, day_learning_iter)),
    }
}

fn merge_new(
    review_iter: impl ExactSizeIterator<Item = MainQueueEntry> + 'static,
    new: Vec<NewCard>,
    mode: ReviewMix,
) -> Box<dyn ExactSizeIterator<Item = MainQueueEntry>> {
    let new_iter = new.into_iter().map(Into::into);

    match mode {
        ReviewMix::BeforeReviews => Box::new(SizedChain::new(new_iter, review_iter)),
        ReviewMix::AfterReviews => Box::new(SizedChain::new(review_iter, new_iter)),
        ReviewMix::MixWithReviews => Box::new(Intersperser::new(review_iter, new_iter)),
    }
}

fn sort_learning(learning: Vec<DueCard>) -> VecDeque<LearningQueueEntry> {
    let mut entries: Vec<LearningQueueEntry> =
        learning.into_iter().map(LearningQueueEntry::from).collect();
    entries.sort_by(|a, b| a.cmp_by_reps_then_due(b));
    entries.into_iter().collect()
}

impl Collection {
    pub(crate) fn build_queues(&mut self, deck_id: DeckId) -> Result<CardQueues> {
        let mut queues = QueueBuilder::new(self, deck_id)?;
        self.storage
            .update_active_decks(&queues.context.root_deck)?;

        queues.gather_cards(self)?;

        // Speedrun: reorder due review cards by points-at-stake when enabled.
        // This only reshuffles already-due cards, so FSRS intervals and undo
        // are untouched.
        if self.get_config_bool(BoolKey::SpeedrunPointsAtStake) {
            self.reorder_reviews_by_points_at_stake(&mut queues.review)?;
        }

        // Speedrun: interleave confusable sibling topics (after points-at-stake,
        // so within-topic order still reflects value). Applies to due reviews and
        // to new-card introduction order; ordering only, so FSRS intervals and
        // undo are untouched.
        if self.get_config_bool(BoolKey::SpeedrunInterleaveTopics) {
            self.interleave_reviews_by_topic(&mut queues.review)?;
            self.interleave_new_by_topic(&mut queues.new)?;
            // Preserve our interleaved new-card order through build()'s sort_new.
            queues.context.sort_options.new_order = NewCardSortOrder::NoSort;
        }

        let queues = queues.build(self.learn_ahead_secs() as i64);

        Ok(queues)
    }

    /// Stable-sort due review cards by descending points-at-stake score, using
    /// recorded attempt weakness as evidence. Equal scores preserve the
    /// existing (FSRS) order.
    fn reorder_reviews_by_points_at_stake(&self, reviews: &mut [DueCard]) -> Result<()> {
        use crate::speedrun::points_at_stake::points_at_stake_score;
        use crate::speedrun::points_at_stake::CardValueInputs;

        let mut scores: Vec<f32> = Vec::with_capacity(reviews.len());
        for card in reviews.iter() {
            let weakness = self.storage.sr_card_weakness(card.id)?;
            let recall_perf_gap = self.storage.sr_card_recall_perf_gap(card.id)?;
            scores.push(points_at_stake_score(&CardValueInputs {
                topic_weight: 1.0,
                weakness,
                memory_age_days: 0.0,
                recall_perf_gap,
            }));
        }
        let mut order: Vec<usize> = (0..reviews.len()).collect();
        order.sort_by(|&a, &b| {
            scores[b]
                .partial_cmp(&scores[a])
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(a.cmp(&b))
        });
        let reordered: Vec<DueCard> = order.into_iter().map(|i| reviews[i]).collect();
        reviews.copy_from_slice(&reordered);
        Ok(())
    }

    /// Compute (group, topic) key arrays for a sequence of cards. topic = the
    /// first matching `sr_topic_map` tag; group = the topic's parent path (the
    /// substring before the last "::"), or the topic itself when it has no
    /// hierarchy -- so flat / rote tags become their own singleton group and
    /// are never interleaved.
    fn confusable_keys(&self, ids: &[CardId]) -> Result<(Vec<usize>, Vec<usize>)> {
        let mut group_keys: HashMap<String, usize> = HashMap::new();
        let mut topic_keys: HashMap<String, usize> = HashMap::new();
        let mut groups: Vec<usize> = Vec::with_capacity(ids.len());
        let mut topics: Vec<usize> = Vec::with_capacity(ids.len());
        for &id in ids {
            let topic = self.storage.sr_card_topic(id)?.unwrap_or_default();
            let group = match topic.rfind("::") {
                Some(pos) => topic[..pos].to_string(),
                None => topic.clone(),
            };
            let gnext = group_keys.len();
            groups.push(*group_keys.entry(group).or_insert(gnext));
            let tnext = topic_keys.len();
            topics.push(*topic_keys.entry(topic).or_insert(tnext));
        }
        Ok((groups, topics))
    }

    /// Interleave confusable sibling topics within due reviews, keeping
    /// unrelated concepts (and rote/flat tags) blocked. Reorders due cards
    /// only.
    fn interleave_reviews_by_topic(&self, reviews: &mut [DueCard]) -> Result<()> {
        use crate::speedrun::interleave::interleave_grouped_indices;

        let ids: Vec<CardId> = reviews.iter().map(|c| c.id).collect();
        let (groups, topics) = self.confusable_keys(&ids)?;
        let reordered: Vec<DueCard> = interleave_grouped_indices(&groups, &topics)
            .into_iter()
            .map(|i| reviews[i])
            .collect();
        reviews.copy_from_slice(&reordered);
        Ok(())
    }

    /// Interleave confusable sibling topics within the new-card introduction
    /// order (ordering only; FSRS intervals are untouched).
    fn interleave_new_by_topic(&self, new: &mut [NewCard]) -> Result<()> {
        use crate::speedrun::interleave::interleave_grouped_indices;

        let ids: Vec<CardId> = new.iter().map(|c| c.id).collect();
        let (groups, topics) = self.confusable_keys(&ids)?;
        let reordered: Vec<NewCard> = interleave_grouped_indices(&groups, &topics)
            .into_iter()
            .map(|i| new[i])
            .collect();
        new.copy_from_slice(&reordered);
        Ok(())
    }
}

#[cfg(test)]
mod test {
    use anki_proto::deck_config::deck_config::config::NewCardGatherPriority;
    use anki_proto::deck_config::deck_config::config::NewCardSortOrder;

    use super::*;
    use crate::card::CardQueue;
    use crate::card::CardType;

    impl Collection {
        fn set_deck_gather_order(&mut self, deck: &mut Deck, order: NewCardGatherPriority) {
            let mut conf = DeckConfig::default();
            conf.inner.new_card_gather_priority = order as i32;
            conf.inner.new_card_sort_order = NewCardSortOrder::NoSort as i32;
            self.add_or_update_deck_config(&mut conf).unwrap();
            deck.normal_mut().unwrap().config_id = conf.id.0;
            self.add_or_update_deck(deck).unwrap();
        }

        fn set_deck_new_limit(&mut self, deck: &mut Deck, new_limit: u32) {
            let mut conf = DeckConfig::default();
            conf.inner.new_per_day = new_limit;
            self.add_or_update_deck_config(&mut conf).unwrap();
            deck.normal_mut().unwrap().config_id = conf.id.0;
            self.add_or_update_deck(deck).unwrap();
        }

        fn set_deck_review_limit(&mut self, deck: DeckId, limit: u32) {
            let dcid = self.get_deck(deck).unwrap().unwrap().config_id().unwrap();
            let mut conf = self.get_deck_config(dcid, false).unwrap().unwrap();
            conf.inner.reviews_per_day = limit;
            self.add_or_update_deck_config(&mut conf).unwrap();
        }

        fn queue_as_deck_and_template(&mut self, deck_id: DeckId) -> Vec<(DeckId, u16)> {
            self.build_queues(deck_id)
                .unwrap()
                .iter()
                .map(|entry| {
                    let card = self.storage.get_card(entry.card_id()).unwrap().unwrap();
                    (card.deck_id, card.template_idx)
                })
                .collect()
        }

        fn set_deck_review_order(&mut self, deck: &mut Deck, order: ReviewCardOrder) {
            let mut conf = DeckConfig::default();
            conf.inner.review_order = order as i32;
            self.add_or_update_deck_config(&mut conf).unwrap();
            deck.normal_mut().unwrap().config_id = conf.id.0;
            self.add_or_update_deck(deck).unwrap();
        }

        fn queue_as_due_and_ivl(&mut self, deck_id: DeckId) -> Vec<(i32, u32)> {
            self.build_queues(deck_id)
                .unwrap()
                .iter()
                .map(|entry| {
                    let card = self.storage.get_card(entry.card_id()).unwrap().unwrap();
                    (card.due, card.interval)
                })
                .collect()
        }
    }

    #[test]
    fn should_build_empty_queue_if_limit_is_reached() {
        let mut col = Collection::new();
        CardAdder::new().due_dates(["0"]).add(&mut col);
        col.set_deck_review_limit(DeckId(1), 0);
        assert_eq!(col.queue_as_deck_and_template(DeckId(1)), vec![]);
    }

    #[test]
    fn new_queue_building() -> Result<()> {
        let mut col = Collection::new();

        // parent
        // ┣━━child━━grandchild
        // ┗━━child_2
        let mut parent = DeckAdder::new("parent").add(&mut col);
        let mut child = DeckAdder::new("parent::child").add(&mut col);
        let child_2 = DeckAdder::new("parent::child_2").add(&mut col);
        let grandchild = DeckAdder::new("parent::child::grandchild").add(&mut col);

        // add 2 new cards to each deck
        for deck in [&parent, &child, &child_2, &grandchild] {
            CardAdder::new().siblings(2).deck(deck.id).add(&mut col);
        }

        // set child's new limit to 3, which should affect grandchild
        col.set_deck_new_limit(&mut child, 3);

        // depth-first tree order
        col.set_deck_gather_order(&mut parent, NewCardGatherPriority::Deck);
        let cards = vec![
            (parent.id, 0),
            (parent.id, 1),
            (child.id, 0),
            (child.id, 1),
            (grandchild.id, 0),
            (child_2.id, 0),
            (child_2.id, 1),
        ];
        assert_eq!(col.queue_as_deck_and_template(parent.id), cards);

        // insertion order
        col.set_deck_gather_order(&mut parent, NewCardGatherPriority::LowestPosition);
        let cards = vec![
            (parent.id, 0),
            (parent.id, 1),
            (child.id, 0),
            (child.id, 1),
            (child_2.id, 0),
            (child_2.id, 1),
            (grandchild.id, 0),
        ];
        assert_eq!(col.queue_as_deck_and_template(parent.id), cards);

        // inverted insertion order, but sibling order is preserved
        col.set_deck_gather_order(&mut parent, NewCardGatherPriority::HighestPosition);
        let cards = vec![
            (grandchild.id, 0),
            (grandchild.id, 1),
            (child_2.id, 0),
            (child_2.id, 1),
            (child.id, 0),
            (parent.id, 0),
            (parent.id, 1),
        ];
        assert_eq!(col.queue_as_deck_and_template(parent.id), cards);

        Ok(())
    }

    #[test]
    fn review_queue_building() -> Result<()> {
        let mut col = Collection::new();

        let mut deck = col.get_or_create_normal_deck("Default").unwrap();
        let nt = col.get_notetype_by_name("Basic")?.unwrap();
        let mut cards = vec![];

        // relative overdueness
        let expected_queue = vec![
            (-150, 1),
            (-100, 1),
            (-50, 1),
            (-150, 5),
            (-100, 5),
            (-50, 5),
            (-150, 20),
            (-150, 20),
            (-100, 20),
            (-50, 20),
            (-150, 100),
            (-100, 100),
            (-50, 100),
            (0, 1),
            (0, 5),
            (0, 20),
            (0, 100),
        ];
        for t in expected_queue.iter() {
            let mut note = nt.new_note();
            note.set_field(0, "foo")?;
            note.id.0 = 0;
            col.add_note(&mut note, deck.id)?;
            let mut card = col.storage.get_card_by_ordinal(note.id, 0)?.unwrap();
            card.interval = t.1;
            card.due = t.0;
            card.ctype = CardType::Review;
            card.queue = CardQueue::Review;
            cards.push(card);
        }
        col.update_cards_maybe_undoable(cards, false)?;
        col.set_deck_review_order(&mut deck, ReviewCardOrder::RelativeOverdueness);
        assert_eq!(col.queue_as_due_and_ivl(deck.id), expected_queue);

        Ok(())
    }

    impl Collection {
        fn card_queue_len(&mut self) -> usize {
            self.get_queued_cards(5, false).unwrap().cards.len()
        }
    }

    #[test]
    fn new_card_potentially_burying_review_card() {
        let mut col = Collection::new();
        // add one new and one review card
        CardAdder::new().siblings(2).due_dates(["0"]).add(&mut col);
        // Potentially problematic config: New cards are shown first and would bury
        // review siblings. This poses a problem because we gather review cards first.
        col.update_default_deck_config(|config| {
            config.new_mix = ReviewMix::BeforeReviews as i32;
            config.bury_new = false;
            config.bury_reviews = true;
        });

        let old_queue_len = col.card_queue_len();
        col.answer_easy();
        col.clear_study_queues();

        // The number of cards in the queue must decrease by exactly 1, either because
        // no burying was performed, or the first built queue anticipated it and didn't
        // include the buried card.
        assert_eq!(col.card_queue_len(), old_queue_len - 1);
    }

    #[test]
    fn new_cards_may_ignore_review_limit() {
        let mut col = Collection::new();
        col.set_config_bool(BoolKey::NewCardsIgnoreReviewLimit, true, false)
            .unwrap();
        col.update_default_deck_config(|config| {
            config.reviews_per_day = 0;
        });
        CardAdder::new().add(&mut col);

        // review limit doesn't apply to new card
        assert_eq!(col.card_queue_len(), 1);
    }

    #[test]
    fn reviews_dont_affect_new_limit_before_review_limit_is_reached() {
        let mut col = Collection::new();
        col.update_default_deck_config(|config| {
            config.new_per_day = 1;
        });
        CardAdder::new().siblings(2).due_dates(["0"]).add(&mut col);
        assert_eq!(col.card_queue_len(), 2);
    }

    #[test]
    fn may_apply_parent_limits() {
        let mut col = Collection::new();
        col.set_config_bool(BoolKey::ApplyAllParentLimits, true, false)
            .unwrap();
        col.update_default_deck_config(|config| {
            config.new_per_day = 0;
        });
        let child = DeckAdder::new("Default::child")
            .with_config(|_| ())
            .add(&mut col);
        CardAdder::new().deck(child.id).add(&mut col);
        col.set_current_deck(child.id).unwrap();
        assert_eq!(col.card_queue_len(), 0);
    }

    #[test]
    fn speedrun_points_at_stake_reorders_due_reviews() -> Result<()> {
        use crate::storage::SrAttempt;

        let mut col = Collection::new();
        let deck = col.get_or_create_normal_deck("Default").unwrap();
        let nt = col.get_notetype_by_name("Basic")?.unwrap();

        // two due review cards with identical due/interval
        let mut cards = vec![];
        for _ in 0..2 {
            let mut note = nt.new_note();
            note.set_field(0, "foo")?;
            note.id.0 = 0;
            col.add_note(&mut note, deck.id)?;
            let mut card = col.storage.get_card_by_ordinal(note.id, 0)?.unwrap();
            card.interval = 10;
            card.due = 0;
            card.ctype = CardType::Review;
            card.queue = CardQueue::Review;
            cards.push(card);
        }
        let strong_id = cards[0].id;
        let weak_id = cards[1].id;
        col.update_cards_maybe_undoable(cards, false)?;

        let attempt = |id: i64, cid: CardId, correct: bool| SrAttempt {
            id,
            cid,
            nid: NoteId(1),
            session_id: String::new(),
            answered_at_ms: id,
            took_ms: 0,
            question_type: 0,
            selected: None,
            correct,
            diagnosis_kind: 0,
            diagnosis_confidence: 0.0,
            routed_action: 0,
            action_status: 0,
            usn: Usn(-1),
            data: String::new(),
            predicted: None,
        };
        // weak card missed; strong card correct
        col.storage.add_sr_attempt(&attempt(1, weak_id, false))?;
        col.storage.add_sr_attempt(&attempt(2, strong_id, true))?;

        // flag off: both present, default order
        let baseline: Vec<CardId> = col
            .build_queues(deck.id)?
            .iter()
            .map(|e| e.card_id())
            .collect();
        assert_eq!(baseline.len(), 2);

        // flag on: weak card surfaces first
        col.set_config_bool(BoolKey::SpeedrunPointsAtStake, true, false)?;
        let ranked: Vec<CardId> = col
            .build_queues(deck.id)?
            .iter()
            .map(|e| e.card_id())
            .collect();
        assert_eq!(ranked.len(), 2);
        assert_eq!(ranked[0], weak_id, "weak card should be first when enabled");
        assert!(ranked.contains(&strong_id));
        Ok(())
    }

    /// The points-at-stake reorder only reshuffles already-due review cards, so
    /// answering a card through the reordered queue and then undoing must leave
    /// the collection uncorrupted and fully restored (the design claim at the
    /// top of `build_queues`). This drives the real answer path (which surfaces
    /// the weak card first) and then undoes it, asserting DB integrity and that
    /// both cards' scheduling fields and the due counts are back to baseline.
    #[test]
    fn speedrun_reorder_is_undo_safe_and_non_corrupting() -> Result<()> {
        use crate::dbcheck::CheckDatabaseOutput;
        use crate::storage::SrAttempt;

        let mut col = Collection::new();
        let deck = col.get_or_create_normal_deck("Default").unwrap();
        let nt = col.get_notetype_by_name("Basic")?.unwrap();

        // two due review cards with identical due/interval
        let mut cards = vec![];
        for _ in 0..2 {
            let mut note = nt.new_note();
            note.set_field(0, "foo")?;
            note.id.0 = 0;
            col.add_note(&mut note, deck.id)?;
            let mut card = col.storage.get_card_by_ordinal(note.id, 0)?.unwrap();
            card.interval = 10;
            card.due = 0;
            card.ctype = CardType::Review;
            card.queue = CardQueue::Review;
            cards.push(card);
        }
        let strong_id = cards[0].id;
        let weak_id = cards[1].id;
        col.update_cards_maybe_undoable(cards, false)?;

        // weakness evidence so the points-at-stake reorder is active (mirrors the
        // sibling test): weak card missed, strong card correct.
        let attempt = |id: i64, cid: CardId, correct: bool| SrAttempt {
            id,
            cid,
            nid: NoteId(1),
            session_id: String::new(),
            answered_at_ms: id,
            took_ms: 0,
            question_type: 0,
            selected: None,
            correct,
            diagnosis_kind: 0,
            diagnosis_confidence: 0.0,
            routed_action: 0,
            action_status: 0,
            usn: Usn(-1),
            data: String::new(),
            predicted: None,
        };
        col.storage.add_sr_attempt(&attempt(1, weak_id, false))?;
        col.storage.add_sr_attempt(&attempt(2, strong_id, true))?;

        col.set_config_bool(BoolKey::SpeedrunPointsAtStake, true, false)?;

        // snapshot the pre-answer state
        let counts_before = col.counts();
        let strong_before = col.storage.get_card(strong_id)?.unwrap();
        let weak_before = col.storage.get_card(weak_id)?.unwrap();

        // answer through the reorder-affected review path: the weak card is
        // surfaced first, so the answer really travels the reordered queue.
        let answered = col.answer_good();
        assert_eq!(
            answered.card_id, weak_id,
            "reorder should surface the weak card first"
        );

        // undo the answer
        col.undo()?;

        // no corruption after reorder + answer + undo
        assert_eq!(col.check_database()?, CheckDatabaseOutput::default());
        assert!(!col.storage.quick_check_corrupt());

        // due counts and both cards' scheduling fields fully restored
        assert_eq!(col.counts(), counts_before);
        let strong_after = col.storage.get_card(strong_id)?.unwrap();
        let weak_after = col.storage.get_card(weak_id)?.unwrap();
        assert_eq!(strong_after.interval, strong_before.interval);
        assert_eq!(strong_after.due, strong_before.due);
        assert_eq!(weak_after.interval, weak_before.interval);
        assert_eq!(weak_after.due, weak_before.due);
        Ok(())
    }

    #[test]
    fn speedrun_interleaves_due_reviews_by_topic() -> Result<()> {
        use crate::storage::SrTopicMapEntry;

        let mut col = Collection::new();
        let deck = col.get_or_create_normal_deck("Default").unwrap();
        let nt = col.get_notetype_by_name("Basic")?.unwrap();

        // sibling subtopics under the same concept (fc1) -> confusable set
        col.storage.replace_sr_topic_map(&[
            SrTopicMapEntry {
                topic: "fc1::a".to_string(),
                label: String::new(),
                weight: 1.0,
            },
            SrTopicMapEntry {
                topic: "fc1::b".to_string(),
                label: String::new(),
                weight: 1.0,
            },
        ])?;

        // two due review cards per sibling topic
        let mut cards = vec![];
        for tag in ["fc1::a", "fc1::a", "fc1::b", "fc1::b"] {
            let mut note = nt.new_note();
            note.set_field(0, "x")?;
            note.tags = vec![tag.to_string()];
            col.add_note(&mut note, deck.id)?;
            let mut card = col.storage.get_card_by_ordinal(note.id, 0)?.unwrap();
            card.ctype = CardType::Review;
            card.queue = CardQueue::Review;
            card.interval = 10;
            card.due = 0;
            cards.push(card);
        }
        col.update_cards_maybe_undoable(cards, false)?;

        col.set_config_bool(BoolKey::SpeedrunInterleaveTopics, true, false)?;
        let order: Vec<CardId> = col
            .build_queues(deck.id)?
            .iter()
            .map(|e| e.card_id())
            .collect();
        assert_eq!(order.len(), 4);

        // consecutive cards should not share a topic
        let topics: Vec<String> = order
            .iter()
            .map(|cid| col.storage.sr_card_topic(*cid).unwrap().unwrap())
            .collect();
        for window in topics.windows(2) {
            assert_ne!(window[0], window[1], "topics should alternate: {topics:?}");
        }
        Ok(())
    }

    #[test]
    fn speedrun_blocks_unrelated_concepts() -> Result<()> {
        use crate::storage::SrTopicMapEntry;

        let mut col = Collection::new();
        let deck = col.get_or_create_normal_deck("Default").unwrap();
        let nt = col.get_notetype_by_name("Basic")?.unwrap();

        // topics under different concepts (fc1 vs fc2) are NOT confusable
        col.storage.replace_sr_topic_map(&[
            SrTopicMapEntry {
                topic: "fc1::a".to_string(),
                label: String::new(),
                weight: 1.0,
            },
            SrTopicMapEntry {
                topic: "fc2::b".to_string(),
                label: String::new(),
                weight: 1.0,
            },
        ])?;

        // gather order alternates the two concepts
        let mut cards = vec![];
        for tag in ["fc1::a", "fc2::b", "fc1::a", "fc2::b"] {
            let mut note = nt.new_note();
            note.set_field(0, "x")?;
            note.tags = vec![tag.to_string()];
            col.add_note(&mut note, deck.id)?;
            let mut card = col.storage.get_card_by_ordinal(note.id, 0)?.unwrap();
            card.ctype = CardType::Review;
            card.queue = CardQueue::Review;
            card.interval = 10;
            card.due = 0;
            cards.push(card);
        }
        col.update_cards_maybe_undoable(cards, false)?;

        col.set_config_bool(BoolKey::SpeedrunInterleaveTopics, true, false)?;
        let order: Vec<CardId> = col
            .build_queues(deck.id)?
            .iter()
            .map(|e| e.card_id())
            .collect();
        assert_eq!(order.len(), 4);

        // unrelated concepts stay blocked (each concept's cards are adjacent),
        // not interleaved across each other.
        let parents: Vec<String> = order
            .iter()
            .map(|cid| {
                let topic = col.storage.sr_card_topic(*cid).unwrap().unwrap();
                topic.split("::").next().unwrap().to_string()
            })
            .collect();
        assert_eq!(
            parents[0], parents[1],
            "concept should be blocked: {parents:?}"
        );
        assert_eq!(
            parents[2], parents[3],
            "concept should be blocked: {parents:?}"
        );
        assert_ne!(
            parents[0], parents[2],
            "concepts should not interleave: {parents:?}"
        );
        Ok(())
    }

    #[test]
    fn speedrun_interleaves_new_cards_by_topic() -> Result<()> {
        use crate::storage::SrTopicMapEntry;

        let mut col = Collection::new();
        let deck = col.get_or_create_normal_deck("Default").unwrap();
        let nt = col.get_notetype_by_name("Basic")?.unwrap();

        col.storage.replace_sr_topic_map(&[
            SrTopicMapEntry {
                topic: "fc1::a".to_string(),
                label: String::new(),
                weight: 1.0,
            },
            SrTopicMapEntry {
                topic: "fc1::b".to_string(),
                label: String::new(),
                weight: 1.0,
            },
        ])?;

        // new cards (not reviews), two per sibling topic
        let mut cards = vec![];
        for (i, tag) in ["fc1::a", "fc1::a", "fc1::b", "fc1::b"].iter().enumerate() {
            let mut note = nt.new_note();
            note.set_field(0, "x")?;
            note.tags = vec![tag.to_string()];
            col.add_note(&mut note, deck.id)?;
            let mut card = col.storage.get_card_by_ordinal(note.id, 0)?.unwrap();
            card.ctype = CardType::New;
            card.queue = CardQueue::New;
            card.due = i as i32;
            cards.push(card);
        }
        col.update_cards_maybe_undoable(cards, false)?;

        col.set_config_bool(BoolKey::SpeedrunInterleaveTopics, true, false)?;
        let order: Vec<CardId> = col
            .build_queues(deck.id)?
            .iter()
            .map(|e| e.card_id())
            .collect();
        assert_eq!(order.len(), 4);

        // new-card introduction order interleaves the sibling topics
        let topics: Vec<String> = order
            .iter()
            .map(|cid| col.storage.sr_card_topic(*cid).unwrap().unwrap())
            .collect();
        for window in topics.windows(2) {
            assert_ne!(
                window[0], window[1],
                "new-card topics should alternate: {topics:?}"
            );
        }
        Ok(())
    }
}
