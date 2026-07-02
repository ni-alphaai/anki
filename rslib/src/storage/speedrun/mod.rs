// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Storage for Speedrun's diagnostic evidence layer.
//!
//! These tables live beside the Anki collection tables but are intentionally
//! kept out of Anki's synced schema-version machinery: they are created
//! idempotently on collection open, and they are synced through Speedrun's own
//! sync service rather than Anki's collection sync. Attempts are append-only
//! evidence; the current diagnosis and routed action are embedded on the row
//! for v1 (see speedrun_architecture_research.md).

use rusqlite::params;
use rusqlite::OptionalExtension;
use rusqlite::Row;

use super::SqliteStorage;
use crate::prelude::*;

/// One recorded review or exam-style question attempt.
#[derive(Debug, Clone, PartialEq)]
pub struct SrAttempt {
    /// Millisecond timestamp id, mirroring the revlog id convention.
    pub id: i64,
    pub cid: CardId,
    pub nid: NoteId,
    pub session_id: String,
    pub answered_at_ms: i64,
    pub took_ms: i64,
    pub question_type: u8,
    pub selected: Option<i64>,
    pub correct: bool,
    pub diagnosis_kind: u8,
    pub diagnosis_confidence: f32,
    pub routed_action: u8,
    pub action_status: u8,
    pub usn: Usn,
    pub data: String,
    /// Pre-answer predicted probability of a correct/recall outcome (0..1);
    /// None when no prediction was captured. Used for calibration.
    pub predicted: Option<f32>,
}

/// A held-out exam-style question that paraphrases a source card's concept.
/// These are never added to the Anki collection as cards, so answering them
/// does not leak into the source card's SRS scheduling (paraphrase test).
#[derive(Debug, Clone, PartialEq)]
pub struct SrQuestionItem {
    /// 0 => assign a new id on insert.
    pub id: i64,
    /// Source card this question paraphrases (optional).
    pub cid: Option<i64>,
    pub topic: String,
    /// 0=hand_authored, 1=open_licensed, 2=ai_generated.
    pub provenance: u8,
    /// JSON: stem, options, correct index, explanation.
    pub payload: String,
}

fn row_to_sr_question_item(row: &Row) -> Result<SrQuestionItem> {
    Ok(SrQuestionItem {
        id: row.get(0)?,
        cid: row.get(1)?,
        topic: row.get(2)?,
        provenance: row.get(3)?,
        payload: row.get(4)?,
    })
}

/// Exam profile: the exam date and target score driving exam-anchored
/// scheduling.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct SrProfile {
    pub exam_date_ms: Option<i64>,
    /// Target MCAT score (472..=528); 0 = unset.
    pub target_score: u32,
}

/// One entry in the topic outline (e.g., an MCAT content category/concept).
#[derive(Debug, Clone, PartialEq)]
pub struct SrTopicMapEntry {
    pub topic: String,
    pub label: String,
    pub weight: f32,
}

/// A cached readiness snapshot (the three scores plus give-up state).
#[derive(Debug, Clone, PartialEq)]
pub struct SrReadiness {
    pub id: i64,
    pub computed_at_ms: i64,
    pub memory: f32,
    pub performance: f32,
    pub recall_perf_gap: f32,
    pub coverage: f32,
    pub readiness_scaled: u32,
    pub low_scaled: u32,
    pub high_scaled: u32,
    pub sufficient: bool,
    pub reason: String,
    pub memory_sufficient: bool,
    pub performance_sufficient: bool,
    pub blocking_dimension: String,
}

fn row_to_sr_readiness(row: &Row) -> Result<SrReadiness> {
    Ok(SrReadiness {
        id: row.get(0)?,
        computed_at_ms: row.get(1)?,
        memory: row.get(2)?,
        performance: row.get(3)?,
        recall_perf_gap: row.get(4)?,
        coverage: row.get(5)?,
        readiness_scaled: row.get(6)?,
        low_scaled: row.get(7)?,
        high_scaled: row.get(8)?,
        sufficient: row.get(9)?,
        reason: row.get(10)?,
        memory_sufficient: row.get(11)?,
        performance_sufficient: row.get(12)?,
        blocking_dimension: row.get(13)?,
    })
}

fn row_to_sr_attempt(row: &Row) -> Result<SrAttempt> {
    Ok(SrAttempt {
        id: row.get(0)?,
        cid: row.get(1)?,
        nid: row.get(2)?,
        session_id: row.get(3)?,
        answered_at_ms: row.get(4)?,
        took_ms: row.get(5)?,
        question_type: row.get(6)?,
        selected: row.get(7)?,
        correct: row.get(8)?,
        diagnosis_kind: row.get(9)?,
        diagnosis_confidence: row.get(10)?,
        routed_action: row.get(11)?,
        action_status: row.get(12)?,
        usn: row.get(13)?,
        data: row.get(14)?,
        predicted: row.get(15)?,
    })
}

impl SqliteStorage {
    /// Create the Speedrun tables if they don't yet exist. Safe to call on
    /// every open.
    pub(crate) fn create_speedrun_tables(&self) -> Result<()> {
        self.db
            .execute_batch(include_str!("tables.sql"))
            .map_err(Into::into)
    }

    /// Append an attempt. If `id` is 0, SQLite assigns one (avoids collisions
    /// for attempts recorded within the same millisecond). Returns the stored id.
    pub(crate) fn add_sr_attempt(&self, attempt: &SrAttempt) -> Result<i64> {
        if attempt.id == 0 {
            self.db
                .prepare_cached(
                    "insert into sr_attempts (cid, nid, session_id, answered_at_ms, took_ms, \
                     question_type, selected, correct, diagnosis_kind, diagnosis_confidence, \
                     routed_action, action_status, usn, data, predicted) \
                     values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                )?
                .execute(params![
                    attempt.cid,
                    attempt.nid,
                    attempt.session_id,
                    attempt.answered_at_ms,
                    attempt.took_ms,
                    attempt.question_type,
                    attempt.selected,
                    attempt.correct,
                    attempt.diagnosis_kind,
                    attempt.diagnosis_confidence,
                    attempt.routed_action,
                    attempt.action_status,
                    attempt.usn,
                    attempt.data,
                    attempt.predicted,
                ])?;
        } else {
            self.db
                .prepare_cached(include_str!("add.sql"))?
                .execute(params![
                    attempt.id,
                    attempt.cid,
                    attempt.nid,
                    attempt.session_id,
                    attempt.answered_at_ms,
                    attempt.took_ms,
                    attempt.question_type,
                    attempt.selected,
                    attempt.correct,
                    attempt.diagnosis_kind,
                    attempt.diagnosis_confidence,
                    attempt.routed_action,
                    attempt.action_status,
                    attempt.usn,
                    attempt.data,
                    attempt.predicted,
                ])?;
        }
        Ok(self.db.last_insert_rowid())
    }

    pub(crate) fn sr_attempts_for_card(&self, cid: CardId) -> Result<Vec<SrAttempt>> {
        self.db
            .prepare_cached(concat!(include_str!("get.sql"), " where cid=? order by id"))?
            .query_and_then([cid], row_to_sr_attempt)?
            .collect()
    }

    /// Number of recorded attempts; used by readiness give-up logic.
    pub(crate) fn sr_attempt_count(&self) -> Result<u32> {
        self.db
            .prepare_cached("select count(*) from sr_attempts")?
            .query_row([], |r| r.get(0))
            .map_err(Into::into)
    }

    /// Fraction of recorded attempts for this card that were incorrect (0.0 if
    /// none). Used as a weakness signal by the points-at-stake queue.
    pub(crate) fn sr_card_weakness(&self, cid: CardId) -> Result<f32> {
        let (total, incorrect): (i64, i64) = self
            .db
            .prepare_cached(
                "select count(*), coalesce(sum(case when correct = 0 then 1 else 0 end), 0) \
                 from sr_attempts where cid = ?",
            )?
            .query_row([cid], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(if total > 0 {
            incorrect as f32 / total as f32
        } else {
            0.0
        })
    }

    /// Per-card recall-vs-performance gap: SRS-review accuracy minus exam-style
    /// accuracy, clamped to 0..1 (0 when either side has no attempts). A high
    /// gap = recall outruns application -> a reasoning-gap card to surface.
    pub(crate) fn sr_card_recall_perf_gap(&self, cid: CardId) -> Result<f32> {
        let (memory, performance): (Option<f64>, Option<f64>) = self
            .db
            .prepare_cached(
                "select avg(case when question_type = 0 then correct end), \
                 avg(case when question_type != 0 then correct end) \
                 from sr_attempts where cid = ?",
            )?
            .query_row([cid], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(match (memory, performance) {
            (Some(m), Some(p)) => ((m - p) as f32).clamp(0.0, 1.0),
            _ => 0.0,
        })
    }

    /// (review cards, mature review cards) for the memory signal. Mature =
    /// interval >= 21 days, matching Anki's convention.
    pub(crate) fn sr_card_counts(&self) -> Result<(u32, u32)> {
        self.db
            .prepare_cached(
                "select count(*), coalesce(sum(case when ivl >= 21 then 1 else 0 end), 0) \
                 from cards where type = 2",
            )?
            .query_row([], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(Into::into)
    }

    /// (exam-style attempts, correct exam-style attempts) for the performance
    /// signal. Exam-style = question_type != 0.
    pub(crate) fn sr_exam_attempt_stats(&self) -> Result<(u32, u32)> {
        self.db
            .prepare_cached(
                "select count(*), coalesce(sum(case when correct = 1 then 1 else 0 end), 0) \
                 from sr_attempts where question_type != 0",
            )?
            .query_row([], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(Into::into)
    }

    /// (total topics, covered topics) from the topic map. A topic is covered if
    /// at least one note is tagged with it.
    pub(crate) fn sr_topic_coverage(&self) -> Result<(u32, u32)> {
        let total: u32 = self
            .db
            .prepare_cached("select count(*) from sr_topic_map")?
            .query_row([], |r| r.get(0))?;
        let covered: u32 = self
            .db
            .prepare_cached(
                "select count(*) from sr_topic_map t where exists \
                 (select 1 from notes n where n.tags like '% ' || t.topic || ' %')",
            )?
            .query_row([], |r| r.get(0))?;
        Ok((total, covered))
    }

    pub(crate) fn add_sr_readiness(&self, snapshot: &SrReadiness) -> Result<i64> {
        self.db
            .prepare_cached(
                "insert into sr_readiness (id, computed_at_ms, memory, performance, \
                 recall_perf_gap, coverage, readiness_scaled, low_scaled, high_scaled, \
                 sufficient, reason, memory_sufficient, performance_sufficient, \
                 blocking_dimension) \
                 values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            )?
            .execute(params![
                snapshot.id,
                snapshot.computed_at_ms,
                snapshot.memory,
                snapshot.performance,
                snapshot.recall_perf_gap,
                snapshot.coverage,
                snapshot.readiness_scaled,
                snapshot.low_scaled,
                snapshot.high_scaled,
                snapshot.sufficient,
                snapshot.reason,
                snapshot.memory_sufficient,
                snapshot.performance_sufficient,
                snapshot.blocking_dimension,
            ])?;
        Ok(snapshot.id)
    }

    pub(crate) fn get_latest_sr_readiness(&self) -> Result<Option<SrReadiness>> {
        self.db
            .prepare_cached(
                "select id, computed_at_ms, memory, performance, recall_perf_gap, coverage, \
                 readiness_scaled, low_scaled, high_scaled, sufficient, reason, \
                 memory_sufficient, performance_sufficient, blocking_dimension \
                 from sr_readiness order by computed_at_ms desc, id desc limit 1",
            )?
            .query_and_then([], row_to_sr_readiness)?
            .next()
            .transpose()
    }

    /// Insert a held-out question item. If `id` is 0, SQLite assigns one.
    /// Returns the stored id.
    pub(crate) fn add_sr_question_item(&self, item: &SrQuestionItem) -> Result<i64> {
        if item.id == 0 {
            self.db
                .prepare_cached(
                    "insert into sr_question_items (cid, topic, provenance, payload) \
                     values (?, ?, ?, ?)",
                )?
                .execute(params![item.cid, item.topic, item.provenance, item.payload])?;
        } else {
            self.db
                .prepare_cached(
                    "insert into sr_question_items (id, cid, topic, provenance, payload) \
                     values (?, ?, ?, ?, ?)",
                )?
                .execute(params![
                    item.id,
                    item.cid,
                    item.topic,
                    item.provenance,
                    item.payload
                ])?;
        }
        Ok(self.db.last_insert_rowid())
    }

    pub(crate) fn get_sr_question_items_for_card(
        &self,
        cid: CardId,
    ) -> Result<Vec<SrQuestionItem>> {
        self.db
            .prepare_cached(
                "select id, cid, topic, provenance, payload from sr_question_items \
                 where cid = ? order by id",
            )?
            .query_and_then([cid], row_to_sr_question_item)?
            .collect()
    }

    pub(crate) fn get_sr_question_items_for_topic(
        &self,
        topic: &str,
    ) -> Result<Vec<SrQuestionItem>> {
        self.db
            .prepare_cached(
                "select id, cid, topic, provenance, payload from sr_question_items \
                 where topic = ? order by id",
            )?
            .query_and_then([topic], row_to_sr_question_item)?
            .collect()
    }

    /// Up to `limit` held-out question items for a practice session, optionally
    /// filtered to one topic, in random order (variety across sessions).
    pub(crate) fn list_sr_question_items(
        &self,
        limit: u32,
        topic: Option<&str>,
    ) -> Result<Vec<SrQuestionItem>> {
        match topic {
            Some(t) => self
                .db
                .prepare_cached(
                    "select id, cid, topic, provenance, payload from sr_question_items \
                     where topic = ? order by random() limit ?",
                )?
                .query_and_then(params![t, limit], row_to_sr_question_item)?
                .collect(),
            None => self
                .db
                .prepare_cached(
                    "select id, cid, topic, provenance, payload from sr_question_items \
                     order by random() limit ?",
                )?
                .query_and_then(params![limit], row_to_sr_question_item)?
                .collect(),
        }
    }

    /// Correct a recorded attempt's diagnosis (and routed action). Marks the row
    /// pending re-sync.
    pub(crate) fn update_sr_attempt_diagnosis(
        &self,
        id: i64,
        diagnosis_kind: u8,
        routed_action: u8,
    ) -> Result<()> {
        self.db
            .prepare_cached(
                "update sr_attempts set diagnosis_kind = ?, routed_action = ?, usn = -1 \
                 where id = ?",
            )?
            .execute(params![diagnosis_kind, routed_action, id])?;
        Ok(())
    }

    /// Advance a routed action's lifecycle (pending/accepted/dismissed/completed).
    pub(crate) fn set_sr_attempt_action_status(&self, id: i64, action_status: u8) -> Result<()> {
        self.db
            .prepare_cached("update sr_attempts set action_status = ?, usn = -1 where id = ?")?
            .execute(params![action_status, id])?;
        Ok(())
    }

    pub(crate) fn sr_question_item_count(&self) -> Result<u32> {
        self.db
            .prepare_cached("select count(*) from sr_question_items")?
            .query_row([], |r| r.get(0))
            .map_err(Into::into)
    }

    /// Each question item with the note text of its linked source card (if any):
    /// (item id, payload, note fields). Used by the leakage check.
    pub(crate) fn sr_question_items_with_note_text(
        &self,
    ) -> Result<Vec<(i64, String, Option<String>)>> {
        self.db
            .prepare_cached(
                "select q.id, q.payload, \
                 (select n.flds from cards c join notes n on c.nid = n.id where c.id = q.cid) \
                 from sr_question_items q order by q.id",
            )?
            .query_and_then([], |row| -> Result<(i64, String, Option<String>)> {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?))
            })?
            .collect()
    }

    /// Per source card with exam-style attempts: (card id, attempts, correct,
    /// source-card interval). Used to compute the recall-vs-performance gap.
    pub(crate) fn sr_performance_rows(&self) -> Result<Vec<(i64, u32, u32, i64)>> {
        self.db
            .prepare_cached(
                "select a.cid, count(*), \
                 coalesce(sum(case when a.correct = 1 then 1 else 0 end), 0), \
                 coalesce((select c.ivl from cards c where c.id = a.cid), 0) \
                 from sr_attempts a where a.question_type != 0 group by a.cid",
            )?
            .query_and_then([], |row| -> Result<(i64, u32, u32, i64)> {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
            })?
            .collect()
    }

    /// Replace the entire topic outline with the provided entries. Returns the
    /// number of topics stored.
    pub(crate) fn replace_sr_topic_map(&self, entries: &[SrTopicMapEntry]) -> Result<u32> {
        self.db.execute("delete from sr_topic_map", [])?;
        let mut stmt = self.db.prepare_cached(
            "insert or replace into sr_topic_map (topic, label, weight) values (?, ?, ?)",
        )?;
        for entry in entries {
            stmt.execute(params![entry.topic, entry.label, entry.weight])?;
        }
        Ok(entries.len() as u32)
    }

    pub(crate) fn get_sr_topic_map(&self) -> Result<Vec<SrTopicMapEntry>> {
        self.db
            .prepare_cached("select topic, label, weight from sr_topic_map order by topic")?
            .query_and_then([], |row| -> Result<SrTopicMapEntry> {
                Ok(SrTopicMapEntry {
                    topic: row.get(0)?,
                    label: row.get(1)?,
                    weight: row.get(2)?,
                })
            })?
            .collect()
    }

    pub(crate) fn set_sr_profile(&self, profile: &SrProfile) -> Result<()> {
        self.db
            .prepare_cached(
                "insert or replace into sr_profile (id, exam_date_ms, target_score) \
                 values (1, ?, ?)",
            )?
            .execute(params![profile.exam_date_ms, profile.target_score])?;
        Ok(())
    }

    pub(crate) fn get_sr_profile(&self) -> Result<SrProfile> {
        let profile = self
            .db
            .prepare_cached("select exam_date_ms, target_score from sr_profile where id = 1")?
            .query_and_then([], |row| -> Result<SrProfile> {
                Ok(SrProfile {
                    exam_date_ms: row.get(0)?,
                    target_score: row.get(1)?,
                })
            })?
            .next()
            .transpose()?;
        Ok(profile.unwrap_or_default())
    }

    /// (predicted probability, actual correctness) pairs for attempts that
    /// captured a prediction. Used for calibration (Brier/log-loss).
    pub(crate) fn sr_calibration_pairs(&self) -> Result<Vec<(f32, bool)>> {
        self.db
            .prepare_cached(
                "select predicted, correct from sr_attempts where predicted is not null",
            )?
            .query_and_then([], |row| -> Result<(f32, bool)> {
                Ok((row.get(0)?, row.get(1)?))
            })?
            .collect()
    }

    /// The first topic-map topic tagged on a card's note, if any. Used to group
    /// cards for topic-aware interleaving.
    pub(crate) fn sr_card_topic(&self, cid: CardId) -> Result<Option<String>> {
        self.db
            .prepare_cached(
                "select t.topic from sr_topic_map t, cards c, notes n \
                 where c.id = ? and c.nid = n.id and n.tags like '% ' || t.topic || ' %' \
                 order by t.topic limit 1",
            )?
            .query_row([cid], |r| r.get(0))
            .optional()
            .map_err(Into::into)
    }

    /// Per topic: (topic, label, weight, number of notes tagged with the topic).
    pub(crate) fn sr_topic_coverage_detail(&self) -> Result<Vec<(String, String, f32, u32)>> {
        self.db
            .prepare_cached(
                "select t.topic, t.label, t.weight, \
                 (select count(*) from notes n where n.tags like '% ' || t.topic || ' %') \
                 from sr_topic_map t order by t.topic",
            )?
            .query_and_then([], |row| -> Result<(String, String, f32, u32)> {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
            })?
            .collect()
    }
}

#[cfg(test)]
mod test {
    use anki_io::new_tempfile;

    use super::*;
    use crate::collection::CollectionBuilder;

    #[test]
    fn sr_attempt_roundtrip() -> Result<()> {
        let tempfile = new_tempfile()?;
        let col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        // tables are created on open
        assert_eq!(col.storage.sr_attempt_count()?, 0);

        let attempt = SrAttempt {
            id: 1_700_000_000_000,
            cid: CardId(123),
            nid: NoteId(456),
            session_id: "session-1".to_string(),
            answered_at_ms: 1_700_000_000_000,
            took_ms: 4200,
            question_type: 1,
            selected: Some(2),
            correct: false,
            diagnosis_kind: 2,
            diagnosis_confidence: 0.8,
            routed_action: 2,
            action_status: 0,
            usn: Usn(-1),
            data: "{}".to_string(),
            predicted: Some(0.9),
        };
        col.storage.add_sr_attempt(&attempt)?;

        let got = col.storage.sr_attempts_for_card(CardId(123))?;
        assert_eq!(got.len(), 1);
        assert_eq!(got[0], attempt);
        assert_eq!(col.storage.sr_attempt_count()?, 1);

        // unrelated card has no attempts
        assert!(col.storage.sr_attempts_for_card(CardId(999))?.is_empty());
        Ok(())
    }

    fn attempt_qt(id: i64, cid: i64, correct: bool, question_type: u8) -> SrAttempt {
        SrAttempt {
            id,
            cid: CardId(cid),
            nid: NoteId(1),
            session_id: String::new(),
            answered_at_ms: id,
            took_ms: 0,
            question_type,
            selected: None,
            correct,
            diagnosis_kind: 0,
            diagnosis_confidence: 0.0,
            routed_action: 0,
            action_status: 0,
            usn: Usn(-1),
            data: String::new(),
            predicted: None,
        }
    }

    #[test]
    fn card_recall_perf_gap() -> Result<()> {
        let tempfile = new_tempfile()?;
        let col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        // card 50: recalled (srs correct) but missed the exam-style question
        col.storage.add_sr_attempt(&attempt_qt(1, 50, true, 0))?;
        col.storage.add_sr_attempt(&attempt_qt(2, 50, false, 1))?;
        assert!((col.storage.sr_card_recall_perf_gap(CardId(50))? - 1.0).abs() < 1e-6);

        // card 60: only SRS attempts -> no gap signal
        col.storage.add_sr_attempt(&attempt_qt(3, 60, true, 0))?;
        assert_eq!(col.storage.sr_card_recall_perf_gap(CardId(60))?, 0.0);
        Ok(())
    }

    #[test]
    fn list_question_items_limit_and_topic() -> Result<()> {
        let tempfile = new_tempfile()?;
        let col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;

        let mut item = |topic: &str, n: i32| SrQuestionItem {
            id: 0,
            cid: None,
            topic: topic.to_string(),
            provenance: 1,
            payload: format!("{{\"n\":{n}}}"),
        };
        for n in 0..5 {
            col.storage.add_sr_question_item(&item("biology", n))?;
        }
        for n in 0..3 {
            col.storage.add_sr_question_item(&item("physics", n))?;
        }

        assert_eq!(col.storage.list_sr_question_items(100, None)?.len(), 8);
        assert_eq!(col.storage.list_sr_question_items(2, None)?.len(), 2);
        let bio = col.storage.list_sr_question_items(100, Some("biology"))?;
        assert_eq!(bio.len(), 5);
        assert!(bio.iter().all(|q| q.topic == "biology"));
        Ok(())
    }
}
