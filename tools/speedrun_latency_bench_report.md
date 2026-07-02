# Speedrun §7h latency benchmark

One-command latency benchmark: load a large synthetic deck and report **p50 / p95 / worst-case** for each core engine action, scalable to 50,000 cards.

> **What this measures:** the wall-clock latency of the Rust engine/backend call behind each UI action (grading, next-card ordering, dashboard loads) — **not** the GUI paint/layout that happens on top. It is the backend floor a UI must add to, not the end-to-end frame time a user sees.

> Runs are **warm**: the collection is built in-process, so SQLite pages are hot. A true cold start (fresh process, cold OS cache) will be somewhat slower; the single cold first-load sample below is the honest worst case for that path.

- **Deck size:** 2,000 cards
- **Iterations per action:** 200
- **Seeded attempts:** 300
- **Cold first-load (single sample):** 3.928 ms (compute_readiness + coverage + performance, first call)

## Per-action latency

| Action | p50 (ms) | p95 (ms) | worst (ms) | iterations | deck size |
| --- | ---: | ---: | ---: | ---: | ---: |
| `get_review_order` | 2.196 | 3.109 | 4.276 | 200 | 2,000 |
| `compute_readiness` | 2.506 | 3.667 | 5.485 | 200 | 2,000 |
| `get_coverage_report` | 1.828 | 2.412 | 3.836 | 200 | 2,000 |
| `get_performance_report` | 0.108 | 0.144 | 0.249 | 200 | 2,000 |
| `get_readiness_snapshot` | 0.048 | 0.057 | 0.091 | 200 | 2,000 |
| `dashboard_first_load` | 4.518 | 5.202 | 9.839 | 200 | 2,000 |
| `find_cards_scan` | 0.312 | 0.475 | 0.969 | 200 | 2,000 |
| `sched_get_queued_cards` | 0.044 | 0.113 | 0.236 | 200 | 2,000 |
| `record_attempt` | 0.042 | 0.103 | 2.117 | 200 | 2,000 |

Action legend:

- `get_review_order` — points-at-stake study order for the deck
- `compute_readiness` — recompute the readiness snapshot (dashboard refresh)
- `get_coverage_report` — topic-coverage dashboard component
- `get_performance_report` — recall-vs-performance dashboard component
- `get_readiness_snapshot` — cached readiness snapshot read
- `dashboard_first_load` — readiness + coverage + performance in one open
- `find_cards_scan` — plain col.find_cards("") full-deck scan
- `sched_get_queued_cards` — vanilla next-card fetch, warm queue (raw review loop)
- `record_attempt` — grade an answer (button press) — insert + classify

## Spec targets (PASS/FAIL)

Targets are the product spec's per-action budgets. Each is compared against the p95 of the action that implements it.

| Target | Budget (p95) | Measured p95 (ms) | Result | Action |
| --- | ---: | ---: | :---: | --- |
| Button ack (grade an answer) | < 50 ms | 0.103 | ✅ PASS | `record_attempt` |
| Next card ordering | < 100 ms | 3.109 | ✅ PASS | `get_review_order` |
| Dashboard first load (cold path) | < 1000 ms | 5.202 | ✅ PASS | `dashboard_first_load` |
| Dashboard refresh (readiness recompute) | < 500 ms | 3.667 | ✅ PASS | `compute_readiness` |

**Overall: all targets PASS** at 2,000 cards.

## Notes & honesty

- These are **engine/backend** latencies (the protobuf RPC round-trip into Rust and back), not full UI frame times. A real screen adds rendering on top of these numbers.
- `get_review_order` is measured with the graded `speedrunPointsAtStake` reorder **enabled**, so it reflects the feature's cost. It scales with the number of due cards the reorder must weigh (so it grows with deck size, unlike the warm capped `sched_get_queued_cards` fetch) but stays within the 100 ms budget at this deck size.
- `dashboard_first_load` is the composite of the three RPCs a dashboard fires on open; `compute_readiness` alone stands in for a refresh.
- `compute_readiness` persists a snapshot keyed by the current millisecond, so it can be stored at most once per ms. The benchmark spaces those calls by ~3 ms **outside** the timed window (per-call latency is unaffected); a real dashboard never approaches that rate.
- `sched_get_queued_cards` runs on the (selected) bench deck and is the **warm** per-draw cost: Anki builds the study queue once and hands back the next card, so this is not a full rebuild (that is `get_review_order`).
- p50/p95 use linear interpolation between ranks; worst is the max observed sample.

## Reproduce

```bash
# quick self-test (small deck, asserts invariants)
./tools/speedrun_latency_bench.sh

# the headline 50,000-card run
./tools/speedrun_latency_bench.sh 50000

# custom deck size and iteration count
./tools/speedrun_latency_bench.sh 10000 300
```

Requires the pylib bridge to have been built once (`./ninja pylib`, or `just` per the project convention).
