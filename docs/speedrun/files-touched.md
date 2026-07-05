# Files touched vs upstream Anki

Base commit: `b00308e55` (upstream Anki `fix(search): normalize whitespace in search query parser (#4853)`).
Diff summary against that base: 196 files changed, +122,943 / -41.
Most of that line count is committed data (the MMLU question pack, content library, generated fixtures); the source footprint is small and almost entirely additive.

Regenerate this view any time with:

```bash
git diff --name-status b00308e55 HEAD          # A = added, M = modified
git diff --stat b00308e55 HEAD -- <path>       # per-file line delta
```

## Modified upstream files (the only merge-relevant edits)

These 17 files existed in upstream and were changed. They are kept as small, self-contained, greppable edits so a future rebase stays cheap. Line deltas from `git diff --stat`.

| File                                       | +/-   | What changed                                                                                                | Merge risk       |
| ------------------------------------------ | ----- | ----------------------------------------------------------------------------------------------------------- | ---------------- |
| `rslib/src/lib.rs`                         | +1    | `pub mod speedrun;`                                                                                         | trivial          |
| `rslib/src/collection/mod.rs`              | +1    | expose the Speedrun storage/service on `Collection`                                                         | trivial          |
| `rslib/src/config/bool.rs`                 | +7    | add the `SpeedrunPointsAtStake` bool config key                                                             | low              |
| `rslib/src/storage/mod.rs`                 | +6    | register the `speedrun` storage submodule                                                                   | low              |
| `rslib/src/storage/sqlite.rs`              | +5    | create the `sr_*` tables idempotently on collection open                                                    | low              |
| `rslib/proto/src/lib.rs`                   | +1    | register `speedrun.proto` in the service list                                                               | low              |
| `rslib/src/scheduler/queue/builder/mod.rs` | +446  | points-at-stake reorder + topic interleave methods and tests; a 2-branch toggle block inside `build_queues` | medium           |
| `rslib/src/scheduler/queue/mod.rs`         | +6    | small plumbing for the reorder                                                                              | low              |
| `rslib/src/sync/collection/chunks.rs`      | +123  | make `sr_attempts` ride the incremental changed-rows sync                                                   | medium           |
| `rslib/src/sync/collection/tests.rs`       | +210  | sync round-trip tests for `sr_attempts` / `sr_question_items` / `sr_profile`                                | additive (tests) |
| `qt/aqt/main.py`                           | +13   | wire the desktop Speedrun integration on profile open                                                       | low              |
| `qt/aqt/utils.py`                          | +20   | restyle the `tooltip` into a rounded dark toast                                                             | low (cosmetic)   |
| `build/configure/src/python.rs`            | +1/-1 | build-config tweak                                                                                          | trivial          |
| `qt/pyproject.toml`                        | +14   | desktop packaging / dependency metadata                                                                     | low              |
| `.mypy.ini`                                | +14   | mypy config for the Speedrun sources + numpy stub path                                                      | low              |
| `.gitignore`                               | +2    | ignore `.env` and Android/native build outputs                                                              | trivial          |
| `README.md`                                | +218  | the Speedrun README (exam, build, architecture, Rust note)                                                  | n/a (fork doc)   |

The two medium-risk files are where upstream churn could require re-anchoring:

- `rslib/src/scheduler/queue/builder/mod.rs`: the only edit to existing code is two `if self.get_config_bool(...)` lines in `build_queues`; the reorder/interleave methods are self-contained. If upstream refactors `build_queues`, re-place those two lines.
- `rslib/src/sync/collection/chunks.rs`: `sr_attempts` is added as a new chunk source alongside the existing ones (see [ADR 0001](../../../docs/adr/0001-incremental-sr-attempts-sync.md)). Grouped and Speedrun-tagged for easy re-anchoring.

## New Speedrun source files (added; cannot conflict on merge)

### Rust engine - `rslib/src/speedrun/`

`mod.rs` (deterministic classifier), `service.rs` (protobuf service on `Collection`), `readiness.rs`, `performance.rs`, `coverage.rs`, `calibration.rs`, `exam.rs`, `leakage.rs`, `interleave.rs`, `points_at_stake.rs`, `reasoning_round.rs`, `reasoning_schedule.rs`, `feedback.rs`.

### Rust storage - `rslib/src/storage/speedrun/`

`tables.sql`, `add.sql`, `add_or_update.sql`, `get.sql`, `mod.rs`.

### Protobuf

`proto/anki/speedrun.proto` (the `SpeedrunService` contract shared by desktop + phone).

### Android JNI engine

`rsandroid/` (`Cargo.toml`, `Cargo.lock`, `src/lib.rs`) - produces `librsandroid.so`.

### Desktop (Python/Qt) - `qt/aqt/`

`speedrun.py`, `speedrun_theme.py`, `speedrun_ai.py`, `speedrun_mcat.py`, `speedrun_sync.py`, `speedrun_library.py`, `speedrun_grouping.py`, `speedrun_voice.py`, plus web fonts/assets under `qt/aqt/data/web/imgs/` and desktop tests under `qt/tests/test_speedrun_*.py`.

### Android app - `androidapp/`

Kotlin/Compose app under `app/src/main/java/net/speedrun/app/` (backend bridge `AnkiBackend.kt` / `NativeBackend.kt`, `EngineRepository.kt`, screens, theme), the protobuf symlink `app/src/main/proto`, bundled assets, and JVM + instrumented tests under `app/src/test/` and `app/src/androidTest/`.

### Tests + tooling - `tools/` and `pylib/tests/`

`pylib/tests/test_speedrun.py` (Python-from-the-bridge test) and the full `tools/speedrun_*` harness suite (calibration, ablation, coverage, leakage, card-check, paraphrase, latency, crash, sync-check, AI eval) plus the AI coach in `tools/speedrun_ai/`.

### Fork docs

`docs/speedrun/` (`ai-note.md`, `architecture.md`, `rust-change.md`, `files-touched.md`, `model-notes.md`) and `pystubs/numpy/__init__.pyi` (mypy stub).
