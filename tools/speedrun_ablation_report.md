<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# Points-at-stake queue - three-arm study-feature ablation (§8)

**Status:** simulated-learner experiment (deterministic, AI-off).
**Harness:** `tools/speedrun_ablation.py` (`run_experiment`), driven through the
same protobuf boundary the app uses (`get_review_order`, `record_attempt`).
**Reproduce:** `./tools/speedrun_ablation.sh --experiment`

---

## 1. What this tests

The one feature under ablation is the **points-at-stake queue**: when enabled,
the engine reorders due review cards so that weak, high-value cards surface
first, using the weakness evidence the diagnostic engine has recorded. When
disabled, the engine falls back to Anki's default review order (the
points-at-stake code path is skipped entirely).

Three builds are compared on the **same cards** at an **equal study budget**:

| Arm | Build | Operational definition in the harness |
| --- | --- | --- |
| **1. Full app** | points-at-stake **ON** + diagnostic routing | `get_review_order` with the toggle ON, after the shared weakness evidence is recorded. |
| **2. Feature off** | points-at-stake **OFF**, everything else identical | `get_review_order` with the toggle OFF, *with the same recorded weakness evidence present*. |
| **3. Plain Anki** | unmodified default review order | `get_review_order` with the toggle OFF, captured **before any Speedrun evidence exists**. |

All three arms see the **same recorded misses** (the diagnostic engine's output);
only the queue **ordering** differs between arms. That is the fair comparison the
spec asks for: same evidence, one feature toggled.

## 2. Honest disclaimer - this is a simulation

You cannot run real human learners inside a script, so this is an explicit
*simulated-learner* experiment (the harness emits `"simulation": true`). It is
**not** a claim about real score gains. What it does show, deterministically, is
whether the engine's ordering change actually front-loads the cards a
budget-limited learner should study first, and by how much - on a model where we
control the ground truth.

Simulated learner:

- **48 due review cards** across **6 topics** (8 cards each). Each card gets a
  hidden **true mastery** in `[0,1]`; some topics are weak (low mastery), some
  strong. Each topic also has a **yield weight** modelling how much it matters
  for the exam.
- **Yield weight is assigned independently of mastery** (some weak topics are
  high-yield, some low-yield), so the queue is *not* handed a weight-aligned
  signal to exploit. Importantly, **the live queue never sees the weight at all**
  - it ranks purely by recorded weakness (miss rate). The weight lives only in
  our scoring metric, as a stand-in for exam yield.
- **Shared evidence:** for each card we record `round((1 - mastery) * 6)` misses
  out of 6 attempts, so the engine's weakness signal tracks true mastery
  (weaker card -> more recorded misses). This same evidence is present for all
  arms; only arm 1 consumes it for ordering.
- **Creation/`due` order is shuffled independently of mastery**, so the
  plain-Anki default order (`ORDER BY due`) is a mastery-blind, fair baseline.
- **12 random seeds**; we report mean and range.

## 3. Pre-registered metric (fixed before results)

> **Primary metric - expected exam-score gain within the study budget.**
> With an equal budget of **K = 16** reviews (one third of the 48 due cards -
> a time-limited session), take each arm's ordering, "study" the first K cards,
> and score
>
> `expected_gain@K = sum over the first K cards of (1 - true_mastery) * yield_weight`.
>
> This rewards spending limited study time on cards the learner is weak on
> (high `1 - mastery`) and that matter (high weight). An ordering that surfaces
> weak, high-value cards earlier accumulates more expected gain within K.
> **Direction predicted before running:** the full app should score highest;
> feature-off and plain Anki should be roughly equal (the ablated feature is the
> only thing that reorders); the effect should shrink as K -> N (if you review
> everything, order cannot matter).

Supporting metrics (also fixed beforehand): **weak cards landing in the budget**
(count with `mastery < 0.5` in the first K; weight-independent), the existing
**ordering diagnostic** (`positions_changed`, weak card surfaced first), and a
**value-oracle reference** (the best possible order given the hidden truth) as an
upper bound - not one of the three arms.

## 4. Results (12 seeds, K = 16 of 48)

### Primary metric - expected gain

| Arm | Mean | Range (min-max) | Std | vs Plain Anki |
| --- | ---: | ---: | ---: | ---: |
| **1. Full app (points-at-stake ON)** | **15.58** | 14.59 - 17.44 | 0.81 | **+7.72 (+98.1%)** |
| 2. Feature off (PaS OFF, evidence present) | 7.86 | 5.98 - 9.32 | 0.94 | +0.00 (+0.0%) |
| 3. Plain Anki (default order) | 7.86 | 5.98 - 9.32 | 0.94 | baseline |
| *ref: value-oracle (upper bound, not an arm)* | *17.24* | *16.12 - 18.11* | *0.57* | *+9.38 (+119%)* |

**Fraction of the deck's total achievable value captured in the first K:**
Full app **0.58** (0.53-0.66) vs Feature-off/Plain **0.29** (0.21-0.34).

**Weak cards (mastery < 0.5) landing in the K-card budget (of 16):**
Full app **14.7** (13-16) vs Feature-off/Plain **5.25** (3-8).

### Ordering diagnostic (supporting evidence)

| Signal | Value |
| --- | --- |
| Positions changed, ON vs plain (of 48) | 46.7 mean (43-48) |
| First card is a weak card (`mastery < 0.5`) | **100%** of seeds |
| First card is the single globally-weakest card | 25% of seeds |
| **Feature-off order == Plain-Anki order** | **100%** of seeds |

### Budget sensitivity (mean expected gain by budget K)

| Budget | K | Full app | Feature-off / Plain | Oracle | Full vs Plain |
| --- | ---: | ---: | ---: | ---: | ---: |
| 25% | 12 | 12.54 | 5.71 | 14.78 | **+119.5%** |
| 33% | 16 | 15.58 | 7.86 | 17.24 | +98.1% |
| 50% | 24 | 20.26 | 12.56 | 21.20 | +61.4% |
| 75% | 36 | 24.85 | 19.51 | 25.17 | +27.4% |
| 100% | 48 | 26.72 | 26.72 | 26.72 | **+0.0%** |

## 5. Did the feature help?

**Yes - clearly, and in the direction predicted, under a limited study budget.**

- At the pre-registered budget (K = 16), the full app surfaces **~98% more**
  expected exam-value than plain Anki (15.58 vs 7.86), and the separation is
  **clean across every seed**: the worst full-app seed (14.59) still beats the
  best plain-Anki seed (9.32). It also puts **~14.7 of 16** budget slots on
  genuinely weak cards, versus ~5.25 for plain.
- The full app reaches **~90%** of the value-oracle's gain (15.58 / 17.24),
  while plain Anki reaches only **~46%**. So the feature closes most - but not
  all - of the gap to a perfect, truth-aware ordering.

**Honest caveats and null results:**

- **Feature-off is bit-for-bit identical to plain Anki** (100% of seeds), so its
  improvement is exactly **0.0%**. This is a *good* null result: it confirms the
  reordering is attributable **entirely** to the points-at-stake feature. The
  recorded diagnostic evidence, on its own, changes nothing about the queue.
- **The benefit vanishes at full budget.** At K = N = 48 all three arms score
  identically (+0.0%): if you review the whole due pile, ordering cannot change
  which cards you saw. The feature helps *precisely* when study time is scarce,
  and its advantage grows as the budget tightens (+27% at 75% budget, rising to
  +120% at 25% budget).
- **Headroom remains.** The full app does not reach the oracle because the live
  queue ranks by **weakness only** and ignores topic yield - it will "spend" a
  budget slot on a weak-but-low-yield card ahead of a stronger high-yield one.
  Wiring topic weight into the live ranking is the obvious next improvement.
- **The single weakest card leads only 25% of the time**, even though a weak
  card *always* leads (100%). This is an honest artifact of coarse evidence: with
  only 6 recorded attempts the miss rate is quantized to sixths, so the weakest
  tier ties and the tie is broken by default order. Finer evidence would sharpen
  this; it does not affect the headline metric, which scores the whole budget.

## 6. Reproduce

```bash
# self-test (original ordering check) + the 3-arm experiment, with assertions
./tools/speedrun_ablation.sh

# the 3-arm experiment only, full JSON incl. per-seed table (12 seeds default)
./tools/speedrun_ablation.sh --experiment

# choose the seed count, e.g. 24 seeds
./tools/speedrun_ablation.sh --experiment 24
```

Requires the built pylib bridge (`out/pyenv`, `out/pylib`). Everything is
deterministic given the seed list, so the numbers above reproduce exactly.

## 7. Deviations & limitations

- **Simulation, not a human trial.** By design (see §2). The metric is a proxy
  for "value surfaced within a limited session", not a measured MCAT delta.
- **Arms 2 and 3 coincide in queue order** because points-at-stake is the *only*
  ordering feature toggled here (topic interleaving is a separate feature owned
  elsewhere and is left OFF). They are still reported separately to match the
  spec, and their exact equality is used as the attribution check in §4.
- **Topic yield weight is not read by the live queue** (it uses `topic_weight =
  1.0` internally); it appears only in the scoring metric as an exam-yield proxy.
  This makes the test *harder* for the feature, not easier - the feature wins on
  weakness alone.
- Per-seed values are in the `per_seed` block of `--experiment` output for full
  transparency.
