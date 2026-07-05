# The Rust change: why it belongs in the engine, and its merge cost

This is the spec section 7a note for the graded Rust change.

## What the change is

Speedrun adds two things inside Anki's Rust core (`rslib`):

1. A diagnostic evidence engine ([`rslib/src/speedrun/`](../../rslib/src/speedrun/) + [`rslib/src/storage/speedrun/`](../../rslib/src/storage/speedrun/)): the `sr_attempts` / `sr_readiness` / `sr_question_items` / `sr_topic_map` / `sr_profile` tables (created idempotently on collection open), a deterministic AI-off classifier, the three-signal scoring (memory / performance / readiness) with calibration, and the `SpeedrunService` protobuf service implemented on `Collection`.
2. A points-at-stake review queue ([`rslib/src/speedrun/points_at_stake.rs`](../../rslib/src/speedrun/points_at_stake.rs) + the hook in [`rslib/src/scheduler/queue/builder/mod.rs`](../../rslib/src/scheduler/queue/builder/mod.rs)): due review cards are reordered by a weakness-weighted value score read from recorded evidence, wired into the live queue builder behind the `speedrunPointsAtStake` config toggle.

The value score is `topic_weight * (1 + weakness) + 0.5 * recall_perf_gap + 0.1 * (memory_age_days clamped to 60 / 60)`, a stable descending sort where equal scores preserve the existing FSRS order.
The reorder only reshuffles cards that are already due, so FSRS intervals and undo are untouched.

## Why it belongs in Rust, not Python

- It is on the scheduling hot path. The queue builder (`build_queues`) runs in Rust every time a deck is opened; reordering there is the only place that affects the real review order for both clients. Doing it in Python would only reorder the desktop and would not exist on the phone.
- The engine is shared, and that is the whole point. Because the change lives in `rslib`, it is compiled into the desktop `pylib` bridge (`anki/_rsbridge.so`) and into `librsandroid.so` for Android, so the phone gets identical scheduling and scoring with no reimplementation. A JavaScript or Python scheduler on the client would be a second, divergent source of truth - exactly what the spec forbids.
- It reads the same storage the scheduler uses. The weakness/gap inputs come from `sr_attempts` in the same SQLite collection the queue builder already has open; keeping it in Rust avoids a cross-language round-trip per card on a 50,000-card queue.
- Latency budgets. The reorder is O(n log n) over the due set inside the existing builder pass; the scoring is pure and allocation-light, which keeps it inside the p95 budgets that a per-card Python callback could not meet.

## Undo-safety and no corruption

The reorder changes only the in-memory order of already-due cards; it never mutates card scheduling state, so answering through the reordered queue and then undoing restores the collection exactly.
This is asserted by a dedicated test that drives the real answer path (the weak card is surfaced first), undoes it, and then checks `check_database()` is clean and both cards' scheduling fields and the due counts are back to baseline (`speedrun_reorder_is_undo_safe_and_non_corrupting` in the queue builder tests).

## Tests (spec asks for >= 3 Rust unit tests + 1 Python test)

- `points_at_stake.rs`: 3 unit tests (score rises with weakness, with topic weight, and with the recall-vs-performance gap).
- `reasoning_round.rs`, `readiness.rs`, `performance.rs`, `coverage.rs`, `calibration.rs`, `interleave.rs`, `reasoning_schedule.rs`, `feedback.rs`: additional unit tests for the scoring and give-up rules.
- Queue builder integration tests: `speedrun_points_at_stake_reorders_due_reviews`, `speedrun_reorder_is_undo_safe_and_non_corrupting`, plus interleave tests.
- Sync round-trip tests in [`rslib/src/sync/collection/tests.rs`](../../rslib/src/sync/collection/tests.rs): `sr_attempts` and the config-carried `sr_question_items` / `sr_profile` survive incremental sync with no loss or duplication.
- Python-from-the-bridge: [`pylib/tests/test_speedrun.py`](../../pylib/tests/test_speedrun.py) drives `SpeedrunService` through the real `_rsbridge` boundary (record attempt, classify, compute readiness), which is the "calls your change from Python" test.

Run them all with `PROTOC=$(which protoc) ./tools/speedrun_check.sh` (Rust tests + Python bridge tests + tool self-tests).

## Upstream files touched, and future-merge difficulty

Almost all of Speedrun is additive: new files under `rslib/src/speedrun/`, `rslib/src/storage/speedrun/`, `proto/anki/speedrun.proto`, `qt/aqt/speedrun*.py`, `androidapp/`, `rsandroid/`, and `tools/`. Those cannot conflict on an upstream merge.

The modified upstream files are deliberately small, one-line registrations wherever possible, to keep a future rebase cheap. The full annotated list is in [files-touched.md](files-touched.md); the merge-relevant ones:

- `rslib/src/lib.rs` (+1): `pub mod speedrun;`. Trivial.
- `rslib/src/collection/mod.rs` (+1) and `rslib/src/config/bool.rs` (+7): register the `SpeedrunPointsAtStake` bool key. Low risk; a new enum variant.
- `rslib/src/storage/mod.rs` (+6) and `rslib/src/storage/sqlite.rs` (+5): create the `sr_*` tables on open and expose the storage module. Low risk; isolated calls.
- `rslib/proto/src/lib.rs` (+1): register the `speedrun.proto` service. Low risk.
- `rslib/src/scheduler/queue/builder/mod.rs` (+446): the highest-value edit. The bulk is new methods (`reorder_reviews_by_points_at_stake`, `interleave_*`) plus tests; the only change to existing upstream code is a two-branch block in `build_queues` guarded by config toggles. Medium risk: if upstream refactors `build_queues`, the two `if self.get_config_bool(...)` lines must be re-placed, but the reorder methods themselves are self-contained.
- `rslib/src/scheduler/queue/mod.rs` (+6): small plumbing for the reorder. Low risk.
- `rslib/src/sync/collection/chunks.rs` (+123): make `sr_attempts` ride the incremental changed-rows sync (a new chunk source alongside the existing ones). Medium risk: touches the sync chunk assembly, which upstream changes occasionally; the additions are grouped and clearly Speedrun-tagged. See [ADR 0001](../../../docs/adr/0001-incremental-sr-attempts-sync.md).
- `qt/aqt/main.py` (+13): wire the desktop integration on profile open. Low risk.
- `qt/aqt/utils.py` (+20): restyle the `tooltip` toast. Low risk; cosmetic.

Overall a future merge is low-to-medium cost: the two medium-risk files (`queue/builder/mod.rs`, `sync/collection/chunks.rs`) are where upstream churn could require re-anchoring, and both keep their Speedrun additions in self-contained, greppable blocks to make that easy.
