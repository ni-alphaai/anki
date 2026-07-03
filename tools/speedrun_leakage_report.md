# Speedrun s7e - Leakage Check (held-out data is CLEAN)

**Claim.** The held-out MCAT question bank and the diagnosis gold set contain **no test item that is a verbatim copy - or a reworded near-copy - of a study card**, and **no gold item duplicates another gold item**. If they did, the "performance" model would just be recall in disguise and the diagnosis yardstick would be padded, so a leaked item **zeroes that model in grading**. This scanner proves the real packs are clean, and proves the detector actually fires on a planted duplicate so "clean" is meaningful.

Two independent layers run over the same corpus. The **engine verbatim layer** goes through the Rust `SpeedrunService.get_leakage_report()` RPC - the exact check the desktop app and phone use - and the **Python near-duplicate layer** adds the reworded-copy check a substring test misses. No AI is involved.

Reproduce:

```bash
./tools/speedrun_leakage_check.sh                      # scan the 3 real packs + self-test
./tools/speedrun_leakage_check.sh some_pack.json ...   # scan specific packs
```

The wrapper uses the built pylib bridge (`out/pyenv` + `out/pylib`) so the engine layer runs; the pure-Python near-dup layer + self-test run with or without it. Exit codes: `0` clean, `1` real s7e leakage, `2` self-test failed, `3` pack not found.

## Verdict: CLEAN

Scanned the 3 existing packs (**2263 stems total**): `speedrun_question_pack.json`, `speedrun_mmlu_pack.json`, `speedrun_gold_set.json`.

| Pack       | Role                            | Total items | Verbatim-flagged | Near-dup-flagged |  Verdict  |
| ---------- | ------------------------------- | ----------: | ---------------: | ---------------: | :-------: |
| `question` | held-out, linked to study cards |           8 |                0 |                0 | **CLEAN** |
| `mmlu`     | held-out, global (unlinked)     |        2231 |                0 |                0 | **CLEAN** |
| `gold_set` | diagnosis gold                  |          24 |                0 |                0 | **CLEAN** |

- **Verbatim-flagged** is from the real engine RPC: for each question item, is its normalized stem a substring of its linked source card's note text? (`question` linked **3** study cards - one per `card_tag` `fc1`/`fc4`/`fc5`; `mmlu`/`gold_set` have no `card_tag`, so their items are unlinked and there is no study card to leak _from_.)
- **Near-dup-flagged** is the s7e near-duplicate count: held-out stem vs its study-card stand-in (`question`), and gold-vs-gold (`gold_set`).

**s7e leakage conditions (these drive the verdict and the exit code):**

| #  | Condition                                                | Flagged |
| -- | -------------------------------------------------------- | ------: |
| L1 | a held-out stem is a **verbatim** copy of its study card |   **0** |
| L2 | a held-out stem is a **near** copy of its study card     |   **0** |
| L3 | a **gold** item duplicates another gold item             |   **0** |

## Thresholds (stated, fixed)

| Knob                          | Value                                                | Where                                                |
| ----------------------------- | ---------------------------------------------------- | ---------------------------------------------------- |
| `MIN_STEM_LEN` (verbatim)     | **12** normalized chars                              | ported from `rslib/src/speedrun/leakage.rs`          |
| word-set Jaccard              | **>= 0.80**                                          | near-dup flag                                        |
| char n-gram Jaccard (n=**5**) | **>= 0.80**                                          | near-dup flag                                        |
| near-dup rule                 | `word_jaccard >= 0.80` **OR** `char_jaccard >= 0.80` | a pair is flagged if _either_ fires                  |
| candidate gate                | `word_jaccard >= 0.40`                               | perf only; a pair below it cannot reach char >= 0.80 |

`normalize()` is a byte-for-byte port of the Rust normalizer (lowercase, keep alphanumerics, collapse everything else to single spaces), so the Python and engine layers agree on "the same text".

## Detector self-test (planted duplicates - proof the check fires)

A "clean" result is only meaningful if we have just watched the detector catch a real leak. Every run first feeds the detectors a deliberately-leaked **verbatim copy** and a reworded **near-copy** and asserts they fire (and that unrelated / reworded text does **not**). **7/7 checks pass**, including the two that call the real engine RPC.

| # | Planted input                                                                                                  | Detector          | Expected | Result |
| - | -------------------------------------------------------------------------------------------------------------- | ----------------- | :------: | :----: |
| 1 | stem `"the peptide bond is an amide bond"` inside note `"The peptide bond is an amide bond between residues."` | verbatim (Python) |   flag   |  PASS  |
| 2 | reworded `"Which functional group links adjacent amino acids in a protein?"` vs same note                      | verbatim (Python) | no flag  |  PASS  |
| 3 | too-short stem `"amino"`                                                                                       | verbatim (Python) | no flag  |  PASS  |
| 4 | `"...thrown straight upward..."` vs `"...thrown straight up..."` (word J=0.833, char J=0.803)                  | near-dup (Python) |   flag   |  PASS  |
| 5 | ball-apex stem vs transaminase-cofactor stem                                                                   | near-dup (Python) | no flag  |  PASS  |
| 6 | question item whose linked card's front **contains its stem verbatim**                                         | **engine RPC**    |   flag   |  PASS  |
| 7 | a reworded item on the same card (only 1 of 2 flagged, `clean=false`)                                          | **engine RPC**    | no flag  |  PASS  |

The end-to-end non-zero exit path is also exercised: scanning a pack with two identical gold stems reports `gold_vs_gold=1`, `clean=false`, and the process **exits 1**.

## Data-hygiene audit (NOT s7e leakage - reported for transparency)

The near-dup layer compares every stem against every other stem in the corpus. Beyond the three s7e conditions above (all clean), it surfaced redundancy that is **outside** the s7e definition and does **not** fail the run, because none of it is a held-out item copying a _study card_ or a gold-vs-gold duplicate:

| Comparison                                   | Pairs >= threshold | Notes                                                           |
| -------------------------------------------- | -----------------: | --------------------------------------------------------------- |
| intra-`mmlu` redundancy                      |                103 | 83 near-exact (char >= 0.90), 20 structural twins (char < 0.90) |
| cross-pack reuse (`question` <-> `gold_set`) |                  2 | 1 exact, 1 reworded                                             |
| all other intra/cross-pack                   |                  0 | -                                                               |

- **intra-`mmlu` (103 pairs).** `mmlu` items are _all_ held-out performance items with no `card_tag` - none is a study card - so these are held-out<->held-out redundancy, not held-out<->study leakage. The open MMLU pool has **66 distinct stems repeated across 136 items** (64 appear twice, plus the generic templates `"which of the following statements is correct"` x5 and `"...is false"` x3). The generic templates are _distinct_ questions that share boilerplate stems and differ only in their options; the twice-seen specific stems (e.g. `"Codons are composed of"`, `"Fatty acids are transported into the mitochondria bound to"`) are largely genuine duplicate items. This scan keys on **stems** (per the s7e spec), so it cannot split template-collisions from true dupes on its own. _Recommendation: de-duplicate `mmlu` on (stem + options) before it seeds the global performance signal, to avoid over-weighting a repeated item._
- **cross-pack (2 pairs).** The performance pack and the diagnosis gold set intentionally reuse the same two MCAT scenarios: `"If an object's speed doubles, its kinetic energy changes by a factor of:"` (`question#7` == `g09`, identical) and the ball-at-apex item (`question#6` ~ `g19`, "straight up" vs "straight upward"). These feed **different** models (`question` -> performance; `gold_set` -> diagnosis agreement), so an item shared between them is _not_ train/test leakage for either model. Flagged here only so the reuse is on the record.

## Method

- **Engine verbatim layer.** For each pack the scanner builds a fresh temp collection, creates one study-card note per distinct `card_tag` (front text = that tag's answer explanations joined - the faithful stand-in for the taught fact, since the student's private SRS cards aren't in the pack), links every question via `add_question_item(card_id=...)`, and calls `get_leakage_report()`. Items without a `card_tag` are added with `card_id=0` (counted in `total_items`, never flagged - no source card to leak from). The RPC extracts each stem from the payload JSON and flags it when `is_leaked(stem, note_text)` (normalized stem of >= 12 chars is a substring of the normalized note text).
- **Python near-duplicate layer.** Pure-Python, no engine required (so the near-dup logic is unit-testable standalone). It measures (a) each linked held-out stem vs its study-card stand-in (verbatim substring **and** token-set near-dup), (b) every stem vs every other stem in the corpus, and (c) gold vs gold. Similarity = word-set Jaccard and character 5-gram Jaccard over the normalized stems.
- **Determinism.** No RNG, no network, no clock in the comparison; the verdict, engine numbers, per-pack rows, and audit are byte-stable across runs.

## Deviations / notes

- **Engine ran here.** With `out/pyenv` + `out/pylib` present, the wrapper's primary path executed the real `get_leakage_report()` RPC (question 8 / mmlu 2231 / gold 24 items, all 0 flagged). If the bridge is absent the wrapper prints a notice and runs the Python layer only (the verbatim check is then covered by the Python substring mirror of `is_leaked`).
- **Study-card stand-in.** The packs reference study cards only by `card_tag`, not by text, so the engine layer represents each tag's studied material by that tag's answer explanations. This keeps the verbatim check non-trivial: a copy-pasted stem would still be caught, while a genuine (interrogative) stem is not a substring of its (declarative) explanation.
- **Wrapper.** Mirrors `tools/speedrun_benchmark.sh` but, unlike it, falls back to a system Python when the bridge is missing (the near-dup layer + self-test are designed to run without the engine). Override the fallback interpreter with `SPEEDRUN_PY=/path/to/python`.
- **Self-test on every run.** The spec asks for a no-arg self-test; this scanner runs it on _every_ invocation so a CLEAN verdict is always backed by a just-fired detector.
- No files outside the three deliverables were modified; nothing was committed or staged.
