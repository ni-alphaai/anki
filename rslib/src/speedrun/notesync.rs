// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Note-encoding sync for Speedrun's `sr_attempts` evidence.
//!
//! Stock Anki peers - including AnkiWeb - drop Speedrun's custom `sr_attempts`
//! sync chunk (see [`crate::sync::collection::chunks`]), so exam-style practice
//! history otherwise only reaches a second device through the fork's own
//! self-hosted sync server on the same LAN. To sync anywhere - AnkiWeb
//! included - each attempt is mirrored into a hidden Anki note that rides the
//! battle-tested note sync every peer keeps, then decoded back into
//! `sr_attempts` on the other device.
//!
//! Each attempt becomes one note in a dedicated, suspended "Speedrun Data"
//! notetype + deck (so it never surfaces in study or normal Browse), keyed by
//! the attempt's globally-unique millisecond id. Encode skips already-noted
//! ids; decode is insert-if-absent by id - so both are idempotent and converge
//! across devices with no merge conflict (append-only, unique keys). It also
//! coexists harmlessly with the custom chunk: decode ignores attempts the chunk
//! already delivered.
//!
//! This lives in `rslib` so desktop (via `qt/aqt/speedrun_notesync.py`) and the
//! Android app both drive the *same* encode/decode through the backend RPCs -
//! one implementation, one wire format.

use std::collections::HashSet;

use anki_proto::scheduler::bury_or_suspend_cards_request::Mode as BuryOrSuspendMode;

use crate::prelude::*;
use crate::search::SortMode;
use crate::sync::collection::chunks::SrAttemptEntry;

/// Notetype/deck/tag the encoded attempts live under. The payload is the two
/// fields ("Key", "Payload"); the wire format is a JSON [`SrAttemptEntry`], the
/// same named-field representation the custom sync chunk uses.
pub(crate) const SPEEDRUN_DATA_NOTETYPE: &str = "Speedrun Data";
pub(crate) const SPEEDRUN_DATA_DECK: &str = "Speedrun Data";
pub(crate) const SPEEDRUN_DATA_TAG: &str = "speedrun_data";

impl Collection {
    /// Mirror every `sr_attempt` not yet represented by a note into one hidden,
    /// suspended note so the standard note sync carries it. Returns the number
    /// of newly-encoded attempts. Idempotent.
    pub(crate) fn note_encode_attempts(&mut self) -> Result<u32> {
        let attempts = self.storage.all_sr_attempts()?;
        if attempts.is_empty() {
            return Ok(0);
        }
        let ntid = self.ensure_speedrun_data_notetype()?;
        let did = self.get_or_create_normal_deck(SPEEDRUN_DATA_DECK)?.id;
        let existing = self.speedrun_data_note_keys()?;
        let notetype = self.get_notetype(ntid)?.or_not_found(ntid)?;

        let mut made = 0u32;
        for attempt in attempts {
            let key = attempt.id.to_string();
            if existing.contains(&key) {
                continue;
            }
            let payload = serde_json::to_string(&SrAttemptEntry::from(attempt))?;
            let mut note = notetype.new_note();
            note.set_field(0, key)?;
            note.set_field(1, payload)?;
            note.tags = vec![SPEEDRUN_DATA_TAG.to_string()];
            self.add_note(&mut note, did)?;
            made += 1;
        }

        if made > 0 {
            // Suspend the freshly-generated data cards so they never surface for
            // review or count as due.
            let search = format!("deck:\"{SPEEDRUN_DATA_DECK}\" -is:suspended");
            let cids = self.search_cards(search.as_str(), SortMode::NoOrder)?;
            if !cids.is_empty() {
                self.bury_or_suspend_cards(&cids, BuryOrSuspendMode::Suspend)?;
            }
        }
        Ok(made)
    }

    /// Insert any attempts carried by "Speedrun Data" notes that aren't already
    /// in `sr_attempts`. Returns the number newly inserted. Idempotent: keyed
    /// on the attempt id, so re-running (or overlap with the custom chunk)
    /// is a no-op.
    pub(crate) fn note_decode_attempts(&mut self) -> Result<u32> {
        let search = format!("note:\"{SPEEDRUN_DATA_NOTETYPE}\"");
        let nids = self.search_notes(search.as_str(), SortMode::NoOrder)?;
        if nids.is_empty() {
            return Ok(0);
        }
        let mut inserted = 0u32;
        for nid in nids {
            let note = self.storage.get_note(nid)?.or_not_found(nid)?;
            let Some(payload) = note.fields().get(1) else {
                continue;
            };
            let entry: SrAttemptEntry = match serde_json::from_str(payload) {
                Ok(entry) => entry,
                // A malformed payload must not abort the whole decode.
                Err(_) => continue,
            };
            if self.storage.get_sr_attempt(entry.id)?.is_some() {
                continue;
            }
            self.storage.add_or_update_sr_attempt(&entry.into())?;
            inserted += 1;
        }
        Ok(inserted)
    }

    /// The `Key` field (= attempt id) of every existing data note, for dedupe.
    fn speedrun_data_note_keys(&mut self) -> Result<HashSet<String>> {
        let search = format!("note:\"{SPEEDRUN_DATA_NOTETYPE}\"");
        let nids = self.search_notes(search.as_str(), SortMode::NoOrder)?;
        let mut keys = HashSet::with_capacity(nids.len());
        for nid in nids {
            if let Some(note) = self.storage.get_note(nid)? {
                if let Some(key) = note.fields().first() {
                    keys.insert(key.clone());
                }
            }
        }
        Ok(keys)
    }

    /// Get (or create once) the minimal two-field "Speedrun Data" notetype.
    fn ensure_speedrun_data_notetype(&mut self) -> Result<NotetypeId> {
        if let Some(nt) = self.get_notetype_by_name(SPEEDRUN_DATA_NOTETYPE)? {
            return Ok(nt.id);
        }
        let mut nt = Notetype {
            name: SPEEDRUN_DATA_NOTETYPE.to_string(),
            config: Notetype::new_config(),
            ..Default::default()
        };
        nt.add_field("Key");
        nt.add_field("Payload");
        nt.add_template("Card 1", "{{Key}}", "{{Payload}}");
        self.add_notetype(&mut nt, true)?;
        Ok(nt.id)
    }
}

#[cfg(test)]
mod test {
    use anki_io::new_tempfile;

    use super::*;
    use crate::collection::CollectionBuilder;
    use crate::storage::SrAttempt;

    fn attempt(id: i64, correct: bool, topic: &str) -> SrAttempt {
        SrAttempt {
            id,
            cid: CardId(12),
            nid: NoteId(34),
            session_id: "s".to_string(),
            answered_at_ms: id,
            took_ms: 5000,
            question_type: 1,
            selected: None,
            correct,
            diagnosis_kind: 0,
            diagnosis_confidence: 0.0,
            routed_action: 0,
            action_status: 0,
            usn: Usn(-1),
            data: "{}".to_string(),
            predicted: Some(0.5),
            topic: topic.to_string(),
        }
    }

    #[test]
    fn note_encode_decode_roundtrip_is_idempotent() -> Result<()> {
        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;
        col.storage
            .add_sr_attempt(&attempt(1_700_000_000_001, true, "biology"))?;
        col.storage
            .add_sr_attempt(&attempt(1_700_000_000_002, false, "physics"))?;

        // Encode: one hidden note per attempt.
        assert_eq!(col.note_encode_attempts()?, 2);
        let nids = col.search_notes(
            format!("note:\"{SPEEDRUN_DATA_NOTETYPE}\"").as_str(),
            SortMode::NoOrder,
        )?;
        assert_eq!(nids.len(), 2);
        // The data cards are suspended (queue = -1) so they never appear in study.
        let cids = col.search_cards(
            format!("deck:\"{SPEEDRUN_DATA_DECK}\"").as_str(),
            SortMode::NoOrder,
        )?;
        assert!(!cids.is_empty());
        for cid in &cids {
            assert_eq!(col.storage.get_card(*cid)?.unwrap().queue as i8, -1);
        }

        // Simulate the second device: attempts gone, only the synced notes remain.
        col.storage.db.execute("delete from sr_attempts", [])?;
        assert_eq!(col.note_decode_attempts()?, 2);
        let decoded = col.storage.all_sr_attempts()?;
        assert_eq!(decoded.len(), 2);
        assert_eq!(decoded[0].id, 1_700_000_000_001);
        assert!(decoded[0].correct);
        assert_eq!(decoded[0].topic, "biology");
        assert_eq!(decoded[1].topic, "physics");
        assert!(!decoded[1].correct);

        // Idempotent both directions (no duplicate notes / rows).
        assert_eq!(col.note_encode_attempts()?, 0);
        assert_eq!(col.note_decode_attempts()?, 0);
        Ok(())
    }

    #[test]
    fn note_encode_with_no_attempts_is_a_noop() -> Result<()> {
        let tempfile = new_tempfile()?;
        let mut col = CollectionBuilder::default()
            .set_collection_path(tempfile.path())
            .build()?;
        assert_eq!(col.note_encode_attempts()?, 0);
        assert_eq!(col.note_decode_attempts()?, 0);
        Ok(())
    }
}
