# Speedrun Crash-Safety & Offline (AI-off) Report

Proof artifact for projectspec **§7g** (crash + offline tests), produced by `tools/speedrun_crash.py` over the Rust SpeedrunService engine — the same protobuf boundary the desktop app and the phone use. No AI, no network.

Reproduce: `./tools/speedrun_crash.sh` (or `./tools/speedrun_crash.sh 20`).

## 1. Crash test — kill the app mid-review, prove zero corruption

Each round a child process opens a **real on-disk** collection, durably commits 3 reviews, then opens a transaction and writes 5,000 rows of a “review in progress” it **never commits**. The parent then **`SIGKILL`s** it while it is parked mid-write (no clean close), reopens the collection, and checks it.

| Metric                 | Result                                                                                                                                           |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Kills performed        | **20**                                                                                                                                           |
| Collections corrupted  | **0**                                                                                                                                            |
| SQLite journal mode    | `wal`                                                                                                                                            |
| Reviews before → after | 0 → **60** (expected 60)                                                                                                                         |
| Child exit signal      | `-9` (SIGKILL) each round                                                                                                                        |
| Integrity method       | pragma integrity_check + pragma foreign_key_check + Collection.fix_integrity() (backend check_database) + compute_readiness() reloads the engine |

Every round the interrupted transaction (the in-flight review) is rolled back by SQLite's crash recovery, the durably-committed reviews survive exactly (count is monotonic and matches the child's pre-kill count), and the engine reloads and still computes readiness.

### Per-kill detail

| #  | child rc | mid-write? | committed (pre-kill) | reviews (post-kill) | expected | integrity_check | fk | fix_integrity | readiness | verdict |
| -- | -------- | ---------- | -------------------- | ------------------- | -------- | --------------- | -- | ------------- | --------- | ------- |
| 1  | `-9`     | yes        | 3                    | 3                   | 3        | ok              | 0  | ok            | 472       | PASS    |
| 2  | `-9`     | yes        | 6                    | 6                   | 6        | ok              | 0  | ok            | 472       | PASS    |
| 3  | `-9`     | yes        | 9                    | 9                   | 9        | ok              | 0  | ok            | 472       | PASS    |
| 4  | `-9`     | yes        | 12                   | 12                  | 12       | ok              | 0  | ok            | 472       | PASS    |
| 5  | `-9`     | yes        | 15                   | 15                  | 15       | ok              | 0  | ok            | 472       | PASS    |
| 6  | `-9`     | yes        | 18                   | 18                  | 18       | ok              | 0  | ok            | 472       | PASS    |
| 7  | `-9`     | yes        | 21                   | 21                  | 21       | ok              | 0  | ok            | 472       | PASS    |
| 8  | `-9`     | yes        | 24                   | 24                  | 24       | ok              | 0  | ok            | 472       | PASS    |
| 9  | `-9`     | yes        | 27                   | 27                  | 27       | ok              | 0  | ok            | 472       | PASS    |
| 10 | `-9`     | yes        | 30                   | 30                  | 30       | ok              | 0  | ok            | 472       | PASS    |
| 11 | `-9`     | yes        | 33                   | 33                  | 33       | ok              | 0  | ok            | 472       | PASS    |
| 12 | `-9`     | yes        | 36                   | 36                  | 36       | ok              | 0  | ok            | 472       | PASS    |
| 13 | `-9`     | yes        | 39                   | 39                  | 39       | ok              | 0  | ok            | 472       | PASS    |
| 14 | `-9`     | yes        | 42                   | 42                  | 42       | ok              | 0  | ok            | 472       | PASS    |
| 15 | `-9`     | yes        | 45                   | 45                  | 45       | ok              | 0  | ok            | 472       | PASS    |
| 16 | `-9`     | yes        | 48                   | 48                  | 48       | ok              | 0  | ok            | 472       | PASS    |
| 17 | `-9`     | yes        | 51                   | 51                  | 51       | ok              | 0  | ok            | 472       | PASS    |
| 18 | `-9`     | yes        | 54                   | 54                  | 54       | ok              | 0  | ok            | 472       | PASS    |
| 19 | `-9`     | yes        | 57                   | 57                  | 57       | ok              | 0  | ok            | 472       | PASS    |
| 20 | `-9`     | yes        | 60                   | 60                  | 60       | ok              | 0  | ok            | 472       | PASS    |

**Crash test: PASS** — 20 hard kills, 0 corrupted collections, 60 committed reviews preserved.

## 2. Offline / AI-off test — network pulled, engine still scores

All outbound Python sockets are black-holed (any `connect()` raises and is counted). The deterministic engine path is pure local Rust computation, so it keeps working with the network gone; the optional AI coach fails fast and the app falls back to it.

### Deterministic diagnosis with the network pulled

| Attempt (signals)       | Diagnosis   | Expected    | Valid non-AI kind? |
| ----------------------- | ----------- | ----------- | ------------------ |
| recall miss (SRS)       | memory      | memory      | yes                |
| missed passage evidence | passage     | passage     | yes                |
| confident + rushed miss | test_taking | test_taking | yes                |
| slow deliberate miss    | reasoning   | reasoning   | yes                |
| correct application     | correct     | correct     | yes                |

| Metric                              | Result                                                                                                                                                                 |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Engine outbound connection attempts | **0**                                                                                                                                                                  |
| Readiness (AI-off, offline)         | abstains (not enough evidence: need graded attempts 5/30, exam-style attempts 4/20, review cards 0/20, topic coverage 0%/50% (weighted 0%)), scaled 478 (MCAT 472–528) |
| AI coach call (network pulled)      | attempted=True, blocked=True                                                                                                                                           |
| Clean fallback to engine            | diagnosis `passage`, readiness 476                                                                                                                                     |

**Offline / AI-off test: PASS** — the engine made 0 outbound connections, produced valid deterministic diagnoses and a readiness signal with the network pulled, and the AI path degraded cleanly to the deterministic engine (which still gives a score).
