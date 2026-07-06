// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

#![cfg(test)]

use std::future::Future;
use std::sync::LazyLock;

use axum::http::StatusCode;
use reqwest::Client;
use reqwest::Url;
use serde_json::json;
use tempfile::tempdir;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tokio::sync::MutexGuard;
use tracing::Instrument;
use tracing::Span;
use wiremock::matchers::method;
use wiremock::matchers::path;
use wiremock::Mock;
use wiremock::MockServer;
use wiremock::ResponseTemplate;

use crate::card::CardQueue;
use crate::collection::CollectionBuilder;
use crate::deckconfig::DeckConfig;
use crate::decks::DeckKind;
use crate::error::SyncError;
use crate::error::SyncErrorKind;
use crate::log::set_global_logger;
use crate::notetype::all_stock_notetypes;
use crate::prelude::*;
use crate::revlog::RevlogEntry;
use crate::search::SortMode;
use crate::services::SpeedrunService;
use crate::storage::SrAttempt;
use crate::sync::collection::graves::ApplyGravesRequest;
use crate::sync::collection::meta::MetaRequest;
use crate::sync::collection::normal::NormalSyncer;
use crate::sync::collection::normal::SyncActionRequired;
use crate::sync::collection::normal::SyncOutput;
use crate::sync::collection::protocol::EmptyInput;
use crate::sync::collection::protocol::SyncProtocol;
use crate::sync::collection::start::StartRequest;
use crate::sync::collection::upload::UploadResponse;
use crate::sync::collection::upload::CORRUPT_MESSAGE;
use crate::sync::http_client::HttpSyncClient;
use crate::sync::http_server::default_ip_header;
use crate::sync::http_server::SimpleServer;
use crate::sync::http_server::SyncServerConfig;
use crate::sync::login::HostKeyRequest;
use crate::sync::login::SyncAuth;
use crate::sync::request::IntoSyncRequest;

struct TestAuth {
    username: String,
    password: String,
    host_key: String,
}

static AUTH: LazyLock<TestAuth> = LazyLock::new(|| {
    if let Ok(auth) = std::env::var("TEST_AUTH") {
        let mut auth = auth.split(':');
        TestAuth {
            username: auth.next().unwrap().into(),
            password: auth.next().unwrap().into(),
            host_key: auth.next().unwrap().into(),
        }
    } else {
        TestAuth {
            username: "user".to_string(),
            password: "pass".to_string(),
            host_key: "b2619aa1529dfdc4248e6edbf3c1b2a2b014cf6d".to_string(),
        }
    }
});

pub(in crate::sync) async fn with_active_server<F, O>(op: F) -> Result<()>
where
    F: FnOnce(HttpSyncClient) -> O,
    O: Future<Output = Result<()>>,
{
    let _ = set_global_logger(None);
    // start server
    let base_folder = tempdir()?;
    std::env::set_var("SYNC_USER1", "user:pass");
    let (addr, server_fut) = SimpleServer::make_server(SyncServerConfig {
        host: "127.0.0.1".parse().unwrap(),
        port: 0,
        base_folder: base_folder.path().into(),
        ip_header: default_ip_header(),
    })
    .await
    .unwrap();
    tokio::spawn(server_fut.instrument(Span::current()));
    // when not using ephemeral servers, tests need to be serialized
    static LOCK: LazyLock<Mutex<()>> = LazyLock::new(|| Mutex::new(()));
    let _lock: MutexGuard<()>;
    // setup client to connect to it
    let endpoint = if let Ok(endpoint) = std::env::var("TEST_ENDPOINT") {
        _lock = LOCK.lock().await;
        endpoint
    } else {
        format!("http://{addr}/")
    };
    let endpoint = Url::try_from(endpoint.as_str()).unwrap();
    let auth = SyncAuth {
        hkey: AUTH.host_key.clone(),
        endpoint: Some(endpoint),
        io_timeout_secs: None,
    };
    let client = HttpSyncClient::new(auth, Client::new());
    op(client).await
}

fn unwrap_sync_err_kind(err: AnkiError) -> SyncErrorKind {
    let AnkiError::SyncError {
        source: SyncError { kind, .. },
    } = err
    else {
        panic!("not sync err: {err:?}");
    };
    kind
}

#[tokio::test]
async fn host_key() -> Result<()> {
    with_active_server(|mut client| async move {
        let err = client
            .host_key(
                HostKeyRequest {
                    username: "bad".to_string(),
                    password: "bad".to_string(),
                }
                .try_into_sync_request()?,
            )
            .await
            .unwrap_err();
        assert_eq!(err.code, StatusCode::FORBIDDEN);
        assert_eq!(
            unwrap_sync_err_kind(AnkiError::from(err)),
            SyncErrorKind::AuthFailed
        );
        // hkey should be automatically set after successful login
        client.sync_key = String::new();
        let resp = client
            .host_key(
                HostKeyRequest {
                    username: AUTH.username.clone(),
                    password: AUTH.password.clone(),
                }
                .try_into_sync_request()?,
            )
            .await?
            .json()?;
        assert_eq!(resp.key, *AUTH.host_key);
        Ok(())
    })
    .await
}

#[tokio::test]
async fn meta() -> Result<()> {
    with_active_server(|client| async move {
        // unsupported sync version
        assert_eq!(
            SyncProtocol::meta(
                &client,
                MetaRequest {
                    sync_version: 0,
                    client_version: "".to_string(),
                }
                .try_into_sync_request()?,
            )
            .await
            .unwrap_err()
            .code,
            StatusCode::NOT_IMPLEMENTED
        );

        Ok(())
    })
    .await
}

#[tokio::test]
async fn aborting_is_idempotent() -> Result<()> {
    with_active_server(|mut client| async move {
        // abort is a no-op if no sync in progress
        client.abort(EmptyInput::request()).await?;

        // start a sync
        let _graves = client
            .start(
                StartRequest {
                    client_usn: Default::default(),
                    local_is_newer: false,
                    deprecated_client_graves: None,
                }
                .try_into_sync_request()?,
            )
            .await?;

        // an abort request with the wrong key is ignored
        let orig_key = client.skey().to_string();
        client.set_skey("aabbccdd".into());
        client.abort(EmptyInput::request()).await?;

        // it should succeed with the correct key
        client.set_skey(orig_key);
        client.abort(EmptyInput::request()).await?;
        Ok(())
    })
    .await
}

#[tokio::test]
async fn new_syncs_cancel_old_ones() -> Result<()> {
    with_active_server(|mut client| async move {
        let ctx = SyncTestContext::new(client.clone());

        // start a sync
        let req = StartRequest {
            client_usn: Default::default(),
            local_is_newer: false,
            deprecated_client_graves: None,
        }
        .try_into_sync_request()?;
        let _ = client.start(req.clone()).await?;

        // a new sync aborts the previous one
        let orig_key = client.skey().to_string();
        client.set_skey("1".into());
        let _ = client.start(req.clone()).await?;

        // old sync can no longer proceed
        client.set_skey(orig_key);
        let graves_req = ApplyGravesRequest::default().try_into_sync_request()?;
        assert_eq!(
            client
                .apply_graves(graves_req.clone())
                .await
                .unwrap_err()
                .code,
            StatusCode::CONFLICT
        );

        // with the correct key, it can continue
        client.set_skey("1".into());
        client.apply_graves(graves_req.clone()).await?;
        // but a full upload will break the lock
        ctx.full_upload(ctx.col1()).await;
        assert_eq!(
            client
                .apply_graves(graves_req.clone())
                .await
                .unwrap_err()
                .code,
            StatusCode::CONFLICT
        );

        // likewise with download
        let _ = client.start(req.clone()).await?;
        ctx.full_download(ctx.col1()).await;
        assert_eq!(
            client
                .apply_graves(graves_req.clone())
                .await
                .unwrap_err()
                .code,
            StatusCode::CONFLICT
        );

        Ok(())
    })
    .await
}

#[tokio::test]
async fn sync_roundtrip() -> Result<()> {
    with_active_server(|client| async move {
        let ctx = SyncTestContext::new(client);
        upload_download(&ctx).await?;
        regular_sync(&ctx).await?;
        Ok(())
    })
    .await
}

/// Build a minimal pending (usn = -1) attempt for the given card.
fn attempt(id: i64, cid: CardId) -> SrAttempt {
    SrAttempt {
        id,
        cid,
        nid: NoteId(1),
        session_id: String::new(),
        answered_at_ms: id,
        took_ms: 0,
        question_type: 1,
        selected: None,
        correct: true,
        diagnosis_kind: 0,
        diagnosis_confidence: 0.0,
        routed_action: 0,
        action_status: 0,
        usn: Usn(-1),
        data: String::new(),
        predicted: None,
        topic: String::new(),
    }
}

/// The Speedrun attempt wire struct must serialize as a *named-field object*,
/// not a positional tuple, so appending a field never breaks sync between
/// mismatched peers (the regression that appending `topic` caused). This locks
/// in both compatibility directions at the serde layer, without two live peers.
#[test]
fn sr_attempt_entry_wire_format_is_forward_compatible() {
    use crate::sync::collection::chunks::SrAttemptEntry;

    // 1. Serializes as an object keyed by field name (not a JSON array).
    let entry = SrAttemptEntry::from(attempt(1, CardId(1)));
    let value = serde_json::to_value(&entry).unwrap();
    assert!(
        value.is_object(),
        "expected a named-field object, got {value}"
    );
    assert!(value.get("id").is_some());
    assert!(value.get("topic").is_some());

    // 2. New peer -> old peer: an object carrying an *unknown* extra key still
    //    deserializes (the key is ignored), so a future field append is safe.
    let mut with_extra = value.as_object().unwrap().clone();
    with_extra.insert("some_future_field".into(), json!("ignored"));
    let round: SrAttemptEntry =
        serde_json::from_value(serde_json::Value::Object(with_extra)).unwrap();
    assert_eq!(round.id, entry.id);

    // 3. Old peer -> new peer: an object *missing* the appended `topic`/`predicted`
    //    still deserializes via `#[serde(default)]`, coming through empty/None.
    let mut without_appended = value.as_object().unwrap().clone();
    without_appended.remove("topic");
    without_appended.remove("predicted");
    let defaulted: SrAttemptEntry =
        serde_json::from_value(serde_json::Value::Object(without_appended)).unwrap();
    assert_eq!(defaulted.topic, "");
    assert_eq!(defaulted.predicted, None);
}

/// Speedrun `sr_attempts` ride Anki's chunk transport (modeled on revlog):
/// an attempt recorded on one collection reaches the other on a normal sync,
/// and attempts recorded independently on each side union together.
#[tokio::test]
async fn sr_attempts_sync_roundtrip() -> Result<()> {
    with_active_server(|client| async move {
        let ctx = SyncTestContext::new(client);
        // establish a common base: col1 has a card, uploaded; col2 downloaded it
        upload_download(&ctx).await?;

        let cid = CardId(
            ctx.col1()
                .storage
                .db_scalar::<i64>("select id from cards")?,
        );

        // seed an attempt on col1 and push it up with a normal (incremental) sync.
        // set_modified_time marks the collection changed so a normal sync runs
        // (recording an attempt does not itself bump the collection mtime).
        {
            let mut col1 = ctx.col1();
            col1.storage
                .add_sr_attempt(&attempt(1_700_000_000_001, cid))?;
            col1.storage.set_modified_time(TimestampMillis::now())?;
            ctx.normal_sync(&mut col1).await;
        }
        // col2 pulls it down
        {
            let mut col2 = ctx.col2();
            ctx.normal_sync(&mut col2).await;
        }
        assert_eq!(ctx.col2().storage.sr_attempts_for_card(cid)?.len(), 1);

        // reverse direction: an attempt recorded on col2 unions back to col1
        {
            let mut col2 = ctx.col2();
            col2.storage
                .add_sr_attempt(&attempt(1_700_000_000_002, cid))?;
            col2.storage.set_modified_time(TimestampMillis::now())?;
            ctx.normal_sync(&mut col2).await;
        }
        {
            let mut col1 = ctx.col1();
            ctx.normal_sync(&mut col1).await;
        }

        // both attempts are present on both sides, and the rows agree
        assert_eq!(ctx.col1().storage.sr_attempts_for_card(cid)?.len(), 2);
        assert_eq!(
            ctx.col1().storage.sr_attempts_for_card(cid)?,
            ctx.col2().storage.sr_attempts_for_card(cid)?,
        );
        Ok(())
    })
    .await
}

/// Regression for the "Sync now does nothing after practice" bug: recording a
/// Speedrun attempt through the real service path (`record_attempt`) writes a
/// pending (usn = -1) row, but if it does not also mark the collection
/// modified, `sync_meta` reports the same `mod` as the server and the sync
/// short-circuits to `NoChanges` *before* it ever gathers the pending
/// attempts. This mirrors the user symptom exactly: exam-style practice
/// recorded on device A never reaches device B after both hit "Sync now", so
/// A's readiness score cannot be reproduced on B.
///
/// Unlike `sr_attempts_sync_roundtrip`, this test deliberately does NOT call
/// `set_modified_time`: the product code must do that itself.
#[tokio::test]
async fn practice_only_attempt_reaches_other_device_after_sync() -> Result<()> {
    with_active_server(|client| async move {
        let ctx = SyncTestContext::new(client);
        upload_download(&ctx).await?;

        // Put device A (col1) into the ordinary steady state right after a
        // successful "Sync now": fully level with the server (col1.mod ==
        // server.mod). Being level is load-bearing - it is exactly when the
        // mod-based meta check short-circuits to NoChanges. (A full
        // upload/download alone leaves col1.mod != server.mod, which would mask
        // the bug by forcing a normal sync regardless.)
        {
            let mut col2 = ctx.col2();
            ctx.normal_sync(&mut col2).await;
        }
        {
            let mut col1 = ctx.col1();
            ctx.normal_sync(&mut col1).await;
        }

        // Device A: the student answers one exam-style practice question - the
        // ONLY change since the last sync (no card review), exactly like
        // practicing in the Speedrun Practice tab - then taps "Sync now".
        {
            let mut col1 = ctx.col1();
            let _ = col1.record_attempt(anki_proto::speedrun::RecordAttemptRequest {
                card_id: 0,
                note_id: 0,
                question_type: 1,
                correct: true,
                topic: "biology".to_string(),
                ..Default::default()
            })?;
            ctx.normal_sync(&mut col1).await; // "Sync now"
        }
        // Device B: taps "Sync now".
        {
            let mut col2 = ctx.col2();
            ctx.normal_sync(&mut col2).await;
        }

        // The evidence behind the readiness score must have reached device B:
        // both the total attempt count and the exam-style attempt count (the
        // two readiness inputs the user saw stuck at 0 on one device) match A.
        let (a_total, a_exam) = {
            let col1 = ctx.col1();
            (
                col1.storage.sr_attempt_count()?,
                col1.storage.sr_exam_attempt_stats()?.0,
            )
        };
        let (b_total, b_exam) = {
            let col2 = ctx.col2();
            (
                col2.storage.sr_attempt_count()?,
                col2.storage.sr_exam_attempt_stats()?.0,
            )
        };
        assert_eq!(a_total, 1, "sanity: A recorded exactly one attempt");
        assert_eq!(
            b_total, a_total,
            "attempt recorded on A did not reach B after Sync now"
        );
        assert_eq!(
            b_exam, a_exam,
            "exam-style attempt count on B must match A after sync"
        );
        Ok(())
    })
    .await
}

/// Companion to `practice_only_attempt_reaches_other_device_after_sync` for the
/// config round-trip evidence (question bank / topic map / exam profile). These
/// tables carry no USN and ride Anki's config sync, but adding a practice
/// question persists it via `set_config`, which - because the Speedrun service
/// bypasses `col.transact` - never bumps `col.mod`. So when device A is level
/// with the server, adding a question and tapping "Sync now" would
/// short-circuit to NoChanges and the question bank would never reach device B.
///
/// Unlike `sr_question_items_sync_via_config_roundtrip`, this test deliberately
/// does NOT call `set_modified_time`: the product code must do that itself.
#[tokio::test]
async fn practice_only_question_item_reaches_other_device_after_sync() -> Result<()> {
    with_active_server(|client| async move {
        let ctx = SyncTestContext::new(client);
        upload_download(&ctx).await?;

        // Steady state: device A (col1) is fully level with the server (it was
        // the last to sync) - exactly when the mod-based meta check would
        // short-circuit to NoChanges.
        {
            let mut col2 = ctx.col2();
            ctx.normal_sync(&mut col2).await;
        }
        {
            let mut col1 = ctx.col1();
            ctx.normal_sync(&mut col1).await;
        }

        // Device A: the student adds a held-out practice question (a config
        // round-trip write, no card review) then taps "Sync now".
        {
            let mut col1 = ctx.col1();
            let _ = col1.add_question_item(anki_proto::speedrun::QuestionItem {
                id: 0,
                card_id: 0,
                topic: "biology".to_string(),
                provenance: 1,
                payload: "{\"stem\":\"synced question\"}".to_string(),
            })?;
            ctx.normal_sync(&mut col1).await; // "Sync now"
        }
        // Device B: taps "Sync now".
        {
            let mut col2 = ctx.col2();
            ctx.normal_sync(&mut col2).await;
        }

        // The practice bank must have reached device B. get_practice_bank_summary
        // reconstitutes the bank from synced config, so a non-empty total proves
        // the question crossed devices.
        let summary = ctx.col2().get_practice_bank_summary()?;
        assert_eq!(
            summary.total, 1,
            "question item added on A did not reach B after Sync now"
        );
        assert_eq!(summary.topics[0].topic, "biology");
        Ok(())
    })
    .await
}

/// The same attempt edited in place on two devices resolves deterministically
/// as last-chunk-wins (ADR 0001): the first side to sync its edit up wins,
/// because the other side pulls that edit before it can push its own. Both
/// sides converge, and the append-only chunk stream never forces a full sync.
#[tokio::test]
async fn sr_attempt_edit_conflict_is_last_writer_wins() -> Result<()> {
    with_active_server(|client| async move {
        let ctx = SyncTestContext::new(client);
        upload_download(&ctx).await?;

        let cid = CardId(
            ctx.col1()
                .storage
                .db_scalar::<i64>("select id from cards")?,
        );
        let attempt_id = 1_700_000_000_010;

        // seed one shared attempt and propagate it to both collections
        {
            let mut col1 = ctx.col1();
            col1.storage.add_sr_attempt(&attempt(attempt_id, cid))?;
            col1.storage.set_modified_time(TimestampMillis::now())?;
            ctx.normal_sync(&mut col1).await;
        }
        {
            let mut col2 = ctx.col2();
            ctx.normal_sync(&mut col2).await;
        }
        assert_eq!(ctx.col1().storage.sr_attempts_for_card(cid)?.len(), 1);
        assert_eq!(ctx.col2().storage.sr_attempts_for_card(cid)?.len(), 1);

        // both devices edit the SAME attempt in place while offline
        {
            let col1 = ctx.col1();
            col1.storage.update_sr_attempt_diagnosis(attempt_id, 1, 1)?;
            col1.storage.set_modified_time(TimestampMillis::now())?;
        }
        {
            let col2 = ctx.col2();
            col2.storage.update_sr_attempt_diagnosis(attempt_id, 4, 4)?;
            col2.storage.set_modified_time(TimestampMillis::now())?;
        }

        // col1 syncs first, so its edit lands on the server first; col2 then
        // pulls col1's edit before pushing its own, so col1's edit wins
        {
            let mut col1 = ctx.col1();
            ctx.normal_sync(&mut col1).await;
        }
        {
            let mut col2 = ctx.col2();
            ctx.normal_sync(&mut col2).await;
        }
        {
            let mut col1 = ctx.col1();
            ctx.normal_sync(&mut col1).await;
        }

        let a1 = ctx.col1().storage.sr_attempts_for_card(cid)?;
        let a2 = ctx.col2().storage.sr_attempts_for_card(cid)?;
        assert_eq!(a1.len(), 1);
        assert_eq!(a2.len(), 1);
        // col1's edit (first to sync) is the winner on both sides
        assert_eq!(a1[0].diagnosis_kind, 1);
        assert_eq!(a1, a2);
        Ok(())
    })
    .await
}

#[tokio::test]
async fn sr_question_items_sync_via_config_roundtrip() -> Result<()> {
    with_active_server(|client| async move {
        let ctx = SyncTestContext::new(client);
        upload_download(&ctx).await?;

        {
            let mut col1 = ctx.col1();
            let _ = col1.add_question_item(anki_proto::speedrun::QuestionItem {
                id: 0,
                card_id: 0,
                topic: "biology".to_string(),
                provenance: 1,
                payload: "{\"stem\":\"synced question\"}".to_string(),
            })?;
            col1.storage.set_modified_time(TimestampMillis::now())?;
            ctx.normal_sync(&mut col1).await;
        }
        {
            let mut col2 = ctx.col2();
            ctx.normal_sync(&mut col2).await;
        }

        let summary = ctx.col2().get_practice_bank_summary()?;
        assert_eq!(summary.total, 1);
        assert_eq!(summary.topics[0].topic, "biology");
        Ok(())
    })
    .await
}

#[tokio::test]
async fn sr_exam_profile_syncs_via_config_roundtrip() -> Result<()> {
    with_active_server(|client| async move {
        let ctx = SyncTestContext::new(client);
        upload_download(&ctx).await?;

        {
            let mut col1 = ctx.col1();
            let _ = col1.set_exam_profile(anki_proto::speedrun::ExamProfile {
                exam_date_ms: 1_800_000_000_000,
                target_score: 515,
            })?;
            col1.storage.set_modified_time(TimestampMillis::now())?;
            ctx.normal_sync(&mut col1).await;
        }
        {
            let mut col2 = ctx.col2();
            ctx.normal_sync(&mut col2).await;
        }

        let profile = ctx.col2().get_exam_profile()?;
        assert_eq!(profile.exam_date_ms, 1_800_000_000_000);
        assert_eq!(profile.target_score, 515);
        Ok(())
    })
    .await
}

#[tokio::test]
async fn sanity_check_should_roll_back_and_force_full_sync() -> Result<()> {
    with_active_server(|client| async move {
        let ctx = SyncTestContext::new(client);
        upload_download(&ctx).await?;

        let mut col1 = ctx.col1();

        // add a deck but don't mark it as requiring a sync, which will trigger the
        // sanity check to fail
        let mut deck = col1.get_or_create_normal_deck("unsynced deck")?;
        col1.add_or_update_deck(&mut deck)?;
        col1.storage
            .db
            .execute("update decks set usn=0 where id=?", [deck.id])?;

        // the sync should fail
        let err = NormalSyncer::new(&mut col1, ctx.cloned_client())
            .sync()
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            AnkiError::SyncError {
                source: SyncError {
                    kind: SyncErrorKind::SanityCheckFailed { .. },
                    ..
                }
            }
        ));

        // the server should have rolled back
        let mut col2 = ctx.col2();
        let out = ctx.normal_sync(&mut col2).await;
        assert_eq!(out.required, SyncActionRequired::NoChanges);

        // and the client should have forced a one-way sync
        let out = ctx.normal_sync(&mut col1).await;
        assert_eq!(
            out.required,
            SyncActionRequired::FullSyncRequired {
                upload_ok: true,
                download_ok: true,
            }
        );

        Ok(())
    })
    .await
}

#[tokio::test]
async fn sync_errors_should_prompt_db_check() -> Result<()> {
    with_active_server(|client| async move {
        let ctx = SyncTestContext::new(client);
        upload_download(&ctx).await?;

        let mut col1 = ctx.col1();

        // Add a a new notetype, and a note that uses it, but don't mark the notetype as
        // requiring a sync, which will cause the sync to fail as the note is added.
        let mut nt = all_stock_notetypes(&col1.tr).remove(0);
        nt.name = "new".into();
        col1.add_notetype(&mut nt, false)?;
        let mut note = nt.new_note();
        note.set_field(0, "test")?;
        col1.add_note(&mut note, DeckId(1))?;
        col1.storage.db.execute("update notetypes set usn=0", [])?;

        // the sync should fail
        let err = NormalSyncer::new(&mut col1, ctx.cloned_client())
            .sync()
            .await
            .unwrap_err();
        let AnkiError::SyncError {
            source: SyncError { info: _, kind },
        } = err
        else {
            panic!()
        };
        assert_eq!(kind, SyncErrorKind::DatabaseCheckRequired);

        // the server should have rolled back
        let mut col2 = ctx.col2();
        let out = ctx.normal_sync(&mut col2).await;
        assert_eq!(out.required, SyncActionRequired::NoChanges);

        // and the client should be able to sync again without a forced one-way sync
        let err = NormalSyncer::new(&mut col1, ctx.cloned_client())
            .sync()
            .await
            .unwrap_err();
        let AnkiError::SyncError {
            source: SyncError { info: _, kind },
        } = err
        else {
            panic!()
        };
        assert_eq!(kind, SyncErrorKind::DatabaseCheckRequired);

        Ok(())
    })
    .await
}

/// Old AnkiMobile versions sent grave ids as strings
#[tokio::test]
async fn string_grave_ids_are_handled() -> Result<()> {
    with_active_server(|client| async move {
        let req = json!({
            "minUsn": 0,
            "lnewer": false,
            "graves": {
                "cards": vec!["1"],
                "decks": vec!["2", "3"],
                "notes": vec!["4"],
            }
        });
        let req = serde_json::to_vec(&req)
            .unwrap()
            .try_into_sync_request()
            .unwrap();
        // should not return err 400
        client.start(req.into_output_type()).await.unwrap();
        client.abort(EmptyInput::request()).await?;
        Ok(())
    })
    .await?;
    // a missing value should be handled
    with_active_server(|client| async move {
        let req = json!({
            "minUsn": 0,
            "lnewer": false,
        });
        let req = serde_json::to_vec(&req)
            .unwrap()
            .try_into_sync_request()
            .unwrap();
        client.start(req.into_output_type()).await.unwrap();
        client.abort(EmptyInput::request()).await?;
        Ok(())
    })
    .await
}

#[tokio::test]
async fn invalid_uploads_should_be_handled() -> Result<()> {
    with_active_server(|client| async move {
        let ctx = SyncTestContext::new(client);
        let res = ctx
            .client
            .upload(b"fake data".to_vec().try_into_sync_request()?)
            .await?;
        assert_eq!(
            res.upload_response(),
            UploadResponse::Err(CORRUPT_MESSAGE.into())
        );
        Ok(())
    })
    .await
}

#[tokio::test]
async fn meta_redirect_is_handled() -> Result<()> {
    with_active_server(|client| async move {
        let mock_server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/sync/meta"))
            .respond_with(
                ResponseTemplate::new(308).insert_header("location", client.endpoint.as_str()),
            )
            .mount(&mock_server)
            .await;
        // starting from in-sync state
        let mut ctx = SyncTestContext::new(client);
        upload_download(&ctx).await?;
        // add another note to trigger a normal sync
        let mut col1 = ctx.col1();
        col1_setup(&mut col1);
        // switch to bad endpoint
        let orig_url = ctx.client.endpoint.to_string();
        ctx.client.endpoint = Url::try_from(mock_server.uri().as_str()).unwrap();
        // sync should succeed
        let out = ctx.normal_sync(&mut col1).await;
        // client should have received new endpoint
        assert_eq!(out.new_endpoint, Some(orig_url));
        // client should not have tried the old endpoint more than once
        assert_eq!(mock_server.received_requests().await.unwrap().len(), 1);
        Ok(())
    })
    .await
}

pub(in crate::sync) struct SyncTestContext {
    pub folder: TempDir,
    pub client: HttpSyncClient,
}

impl SyncTestContext {
    pub fn new(client: HttpSyncClient) -> Self {
        Self {
            folder: tempdir().expect("create temp dir"),
            client,
        }
    }

    pub fn col1(&self) -> Collection {
        let base = self.folder.path();
        CollectionBuilder::new(base.join("col1.anki2"))
            .with_desktop_media_paths()
            .build()
            .unwrap()
    }

    pub fn col2(&self) -> Collection {
        let base = self.folder.path();
        CollectionBuilder::new(base.join("col2.anki2"))
            .with_desktop_media_paths()
            .build()
            .unwrap()
    }

    async fn normal_sync(&self, col: &mut Collection) -> SyncOutput {
        NormalSyncer::new(col, self.cloned_client())
            .sync()
            .await
            .unwrap()
    }

    async fn full_upload(&self, col: Collection) {
        col.full_upload_with_server(self.cloned_client())
            .await
            .unwrap()
    }

    async fn full_download(&self, col: Collection) {
        col.full_download_with_server(self.cloned_client())
            .await
            .unwrap()
    }

    fn cloned_client(&self) -> HttpSyncClient {
        self.client.clone()
    }
}

// Setup + full syncs
/////////////////////

fn col1_setup(col: &mut Collection) {
    let nt = col.get_notetype_by_name("Basic").unwrap().unwrap();
    let mut note = nt.new_note();
    note.set_field(0, "1").unwrap();
    col.add_note(&mut note, DeckId(1)).unwrap();
}

async fn upload_download(ctx: &SyncTestContext) -> Result<()> {
    let mut col1 = ctx.col1();
    col1_setup(&mut col1);

    let out = ctx.normal_sync(&mut col1).await;
    assert!(matches!(
        out.required,
        SyncActionRequired::FullSyncRequired { .. }
    ));

    ctx.full_upload(col1).await;

    // another collection
    let mut col2 = ctx.col2();

    // won't allow ankiweb clobber
    let out = ctx.normal_sync(&mut col2).await;
    assert_eq!(
        out.required,
        SyncActionRequired::FullSyncRequired {
            upload_ok: false,
            download_ok: true,
        }
    );

    // fetch so we're in sync
    ctx.full_download(col2).await;

    Ok(())
}

// Regular syncs
/////////////////////

async fn regular_sync(ctx: &SyncTestContext) -> Result<()> {
    // add a deck
    let mut col1 = ctx.col1();
    let mut col2 = ctx.col2();

    let mut deck = col1.get_or_create_normal_deck("new deck")?;

    // give it a new option group
    let mut dconf = DeckConfig {
        name: "new dconf".into(),
        ..Default::default()
    };
    col1.add_or_update_deck_config(&mut dconf)?;
    if let DeckKind::Normal(deck) = &mut deck.kind {
        deck.config_id = dconf.id.0;
    }
    col1.add_or_update_deck(&mut deck)?;

    // and a new notetype
    let mut nt = all_stock_notetypes(&col1.tr).remove(0);
    nt.name = "new".into();
    col1.add_notetype(&mut nt, false)?;

    // add another note+card+tag
    let mut note = nt.new_note();
    note.set_field(0, "2")?;
    note.tags.push("tag".into());
    col1.add_note(&mut note, deck.id)?;

    // mock revlog entry
    col1.storage.add_revlog_entry(
        &RevlogEntry {
            id: RevlogId(123),
            cid: CardId(456),
            usn: Usn(-1),
            interval: 10,
            ..Default::default()
        },
        true,
    )?;

    // config + creation
    col1.set_config("test", &"test1")?;
    // bumping this will affect 'last studied at' on decks at the moment
    // col1.storage.set_creation_stamp(TimestampSecs(12345))?;

    // and sync our changes
    let remote_meta = ctx
        .client
        .meta(MetaRequest::request())
        .await
        .unwrap()
        .json()
        .unwrap();
    let out = col1.sync_meta()?.compared_to_remote(remote_meta, None);
    assert_eq!(out.required, SyncActionRequired::NormalSyncRequired);

    let out = ctx.normal_sync(&mut col1).await;
    assert_eq!(out.required, SyncActionRequired::NoChanges);

    // sync the other collection
    let out = ctx.normal_sync(&mut col2).await;
    assert_eq!(out.required, SyncActionRequired::NoChanges);

    let ntid = nt.id;
    let deckid = deck.id;
    let dconfid = dconf.id;
    let noteid = note.id;
    let cardid = col1.search_cards(note.id, SortMode::NoOrder)?[0];
    let revlogid = RevlogId(123);

    let compare_sides = |col1: &mut Collection, col2: &mut Collection| -> Result<()> {
        assert_eq!(
            col1.get_notetype(ntid)?.unwrap(),
            col2.get_notetype(ntid)?.unwrap()
        );
        assert_eq!(
            col1.get_deck(deckid)?.unwrap(),
            col2.get_deck(deckid)?.unwrap()
        );
        assert_eq!(
            col1.get_deck_config(dconfid, false)?.unwrap(),
            col2.get_deck_config(dconfid, false)?.unwrap()
        );
        assert_eq!(
            col1.storage.get_note(noteid)?.unwrap(),
            col2.storage.get_note(noteid)?.unwrap()
        );
        assert_eq!(
            col1.storage.get_card(cardid)?.unwrap(),
            col2.storage.get_card(cardid)?.unwrap()
        );
        assert_eq!(
            col1.storage.get_revlog_entry(revlogid)?,
            col2.storage.get_revlog_entry(revlogid)?,
        );
        assert_eq!(
            col1.storage.get_all_config()?,
            col2.storage.get_all_config()?
        );
        assert_eq!(
            col1.storage.creation_stamp()?,
            col2.storage.creation_stamp()?
        );

        // server doesn't send tag usns, so we can only compare tags, not usns,
        // as the usns may not match
        assert_eq!(
            col1.storage
                .all_tags()?
                .into_iter()
                .map(|t| t.name)
                .collect::<Vec<_>>(),
            col2.storage
                .all_tags()?
                .into_iter()
                .map(|t| t.name)
                .collect::<Vec<_>>()
        );
        std::thread::sleep(std::time::Duration::from_millis(1));
        Ok(())
    };

    // make sure everything has been transferred across
    compare_sides(&mut col1, &mut col2)?;

    // make some modifications
    let mut note = col2.storage.get_note(note.id)?.unwrap();
    note.set_field(1, "new")?;
    note.tags.push("tag2".into());
    col2.update_note(&mut note)?;

    col2.get_and_update_card(cardid, |card| {
        card.queue = CardQueue::Review;
        Ok(())
    })?;

    let mut deck = col2.storage.get_deck(deck.id)?.unwrap();
    deck.name = NativeDeckName::from_native_str("newer");
    col2.add_or_update_deck(&mut deck)?;

    let mut nt = col2.storage.get_notetype(nt.id)?.unwrap();
    nt.name = "newer".into();
    col2.update_notetype(&mut nt, false)?;

    // sync the changes back
    let out = ctx.normal_sync(&mut col2).await;
    assert_eq!(out.required, SyncActionRequired::NoChanges);
    let out = ctx.normal_sync(&mut col1).await;
    assert_eq!(out.required, SyncActionRequired::NoChanges);

    // should still match
    compare_sides(&mut col1, &mut col2)?;

    // deletions should sync too
    for table in &["cards", "notes", "decks"] {
        assert_eq!(
            col1.storage
                .db_scalar::<u8>(&format!("select count() from {table}"))?,
            2
        );
    }

    // fixme: inconsistent usn arg
    std::thread::sleep(std::time::Duration::from_millis(1));
    col1.remove_cards_and_orphaned_notes(&[cardid])?;
    let usn = col1.usn()?;
    col1.remove_note_only_undoable(noteid, usn)?;
    col1.remove_decks_and_child_decks(&[deckid])?;

    let out = ctx.normal_sync(&mut col1).await;
    assert_eq!(out.required, SyncActionRequired::NoChanges);
    let out = ctx.normal_sync(&mut col2).await;
    assert_eq!(out.required, SyncActionRequired::NoChanges);

    for table in &["cards", "notes", "decks"] {
        assert_eq!(
            col2.storage
                .db_scalar::<u8>(&format!("select count() from {table}"))?,
            1
        );
    }

    // removing things like a notetype forces a full sync
    std::thread::sleep(std::time::Duration::from_millis(1));
    col2.remove_notetype(ntid)?;
    let out = ctx.normal_sync(&mut col2).await;
    assert!(matches!(
        out.required,
        SyncActionRequired::FullSyncRequired { .. }
    ));
    Ok(())
}
