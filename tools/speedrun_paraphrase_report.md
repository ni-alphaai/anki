# Speedrun §7d — Paraphrase Test (recall vs. performance)

Speedrun reports **recall** (memory: can you remember the fact, read off the SRS substrate) and **performance** (application: can you answer an exam-style question that rewords the same idea) as two separate numbers. This artifact tests whether performance is genuinely measured separately from memory, or just a relabeled copy of it. If the two numbers always matched, the performance model would carry no independent information.

**Pack:** `Speedrun paraphrase test (30 cards x 2)` (license AGPL-3.0-or-later) — 30 source cards × 2 reworded exam-style questions = 60 held-out items.

**Engine:** `SpeedrunService.get_performance_report()` over a fresh temp collection, AI-off. Recall proxy = source card is mature (SRS interval ≥ 21 days); performance = correct/attempts on the held-out reworded questions, averaged per card.

## Headline — main arm (a realistic student)

All 30 source cards were made mature review cards (interval 30d ≥ 21d), so the recall proxy is 1.0 for every card. The student then answered the reworded, held-out questions with graded mastery.

| metric                         | value                                                            |
| ------------------------------ | ---------------------------------------------------------------- |
| cards evaluated                | 30                                                               |
| exam-style attempts            | 60                                                               |
| recall_rate (memory)           | 1.000                                                            |
| performance_rate (application) | 0.600                                                            |
| **recall_perf_gap**            | **0.400**                                                        |
| sufficient (≥5 cards)          | True                                                             |
| engine note                    | recall outruns performance: memory-to-application bridge is weak |
| leakage check                  | CLEAN (0 / 60 flagged)                                           |

**The gap is 0.400** — recall 1.000 vs. performance 0.600. The student reliably remembers every fact but applies it correctly only about 60% of the time on reworded items. The two numbers are far apart, so performance is not an echo of memory.

## Control arm — same memory, perfect application

The same 30 mature cards (recall still 1.0) with **every** reworded question answered correctly:

| metric           | main arm                                                         | control arm                        |
| ---------------- | ---------------------------------------------------------------- | ---------------------------------- |
| recall_rate      | 1.000                                                            | 1.000                              |
| performance_rate | 0.600                                                            | 1.000                              |
| recall_perf_gap  | 0.400                                                            | 0.000                              |
| engine note      | recall outruns performance: memory-to-application bridge is weak | recall and performance are aligned |

Memory was held fixed at 1.000 across both arms, yet performance moved from 0.600 to 1.000 and the gap from 0.400 to 0.000. Only the held-out application evidence changed, so the performance signal is computed from that evidence — it is not derived from, or copied out of, the memory signal.

## Is the bridge real? (interpretation)

**Yes — performance is measured separately from memory, and here the memory→application bridge is weak.**

- **Independent sources.** Recall is read from each card's SRS interval; performance is read from held-out reworded questions that never enter the collection as cards, so answering them cannot change scheduling. The two signals come from different evidence.
- **They diverge.** A 0.400 gap (well above the engine's 0.10 "aligned" band) shows the numbers can pull apart. If performance were just copying memory it would read ≈1.000 like recall and the gap would be ≈0.
- **The gap tracks application, not memory.** The control arm holds memory constant and lifts only the reworded-answer accuracy; performance and the gap move accordingly. That is the manipulation that rules out "copying."
- **The rewording is real.** All 60 questions passed the engine's leakage check (none is a verbatim substring of its source card), so the divergence reflects genuine application, not the student re-reading the card.

**Honest caveats.** Recall here is a binary maturity proxy (interval ≥ 21d), not a graded retrieval probability, so the recall arm is saturated at 1.0 by construction. The "student" is simulated with a fixed, documented answer pattern to produce a controlled, reproducible gap. And a _zero_ gap on its own would be ambiguous — it could mean a genuinely strong student _or_ a metric that merely echoes memory — which is exactly why the test's power comes from its ability to reveal divergence (the main arm), not agreement. The headline claim is about measurement separability; the _sign_ of the gap (recall outruns application) is the actionable, student-specific finding.

## Per-card breakdown — main arm

Recall proxy vs. reworded-question accuracy per source card, read back from the engine's stored attempts.

| tag  | topic | ivl (d) | mature | recall | reworded correct | reworded accuracy |
| ---- | ----- | ------- | ------ | ------ | ---------------- | ----------------- |
| pp01 | fc1   | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp02 | fc1   | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp03 | fc1   | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp04 | fc2   | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp05 | fc2   | 30      | yes    | 1.000  | 0/2              | 0.000             |
| pp06 | fc2   | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp07 | fc3   | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp08 | fc3   | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp09 | fc3   | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp10 | fc4   | 30      | yes    | 1.000  | 0/2              | 0.000             |
| pp11 | fc4   | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp12 | fc4   | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp13 | fc5   | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp14 | fc5   | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp15 | fc5   | 30      | yes    | 1.000  | 0/2              | 0.000             |
| pp16 | fc6   | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp17 | fc6   | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp18 | fc6   | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp19 | fc7   | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp20 | fc7   | 30      | yes    | 1.000  | 0/2              | 0.000             |
| pp21 | fc7   | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp22 | fc8   | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp23 | fc8   | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp24 | fc8   | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp25 | fc9   | 30      | yes    | 1.000  | 0/2              | 0.000             |
| pp26 | fc9   | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp27 | fc9   | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp28 | fc10  | 30      | yes    | 1.000  | 2/2              | 1.000             |
| pp29 | fc10  | 30      | yes    | 1.000  | 1/2              | 0.500             |
| pp30 | fc10  | 30      | yes    | 1.000  | 0/2              | 0.000             |

Every card: recall = 1.00 (mature). Reworded accuracy varies across cards and averages 0.600 — that spread, against a flat recall of 1.00, is the recall-vs-performance gap.

_Coverage note:_ the 30 cards span all 10/10 MCAT foundational-concept topics (fc1–fc10).

## Reproduce

```bash
./tools/speedrun_paraphrase.sh
# or point it at any pack in the same format:
./tools/speedrun_paraphrase.sh tools/speedrun_paraphrase_pack.json
```

The harness builds a fresh temp collection each run and writes this report; the numbers above are produced by the Rust engine, not hand-entered.
