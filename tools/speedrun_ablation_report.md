<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# Points-at-stake + interleave queue - three-arm study-feature ablation (§8)

**Status:** simulated-learner experiment (deterministic, AI-off).
**Harness:** `tools/speedrun_ablation.py` (`run_experiment`), driven through the same protobuf boundary the app uses (`get_review_order`, `record_attempt`, `set_topic_map`).
**Reproduce:** `./tools/speedrun_ablation.sh --experiment`

---

## 1. What this tests

Two study features are ablated, each stacked on top of the previous build so that every arm adds exactly one feature.

The **points-at-stake queue** reorders due review cards so that weak, high-value cards surface first, using the weakness evidence the diagnostic engine has recorded.
The **topic interleave** feature then takes that same value ranking and round-robins confusable sibling topics - children of a shared `concept::topic` parent, resolved from each note's tag via a registered topic map - within their concept block, so related cards are spaced apart instead of studied back to back.

Three genuinely distinct builds are compared on the **same cards** at an **equal study budget**:

| Arm                        | Build                                        | Operational definition in the harness                                                                                                      |
| -------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **1. Plain Anki**          | both features **OFF**                        | `get_review_order` with both toggles OFF, captured **before any Speedrun weakness evidence exists** - the unmodified default review order. |
| **2. +points-at-stake**    | points-at-stake **ON**, interleave **OFF**   | `get_review_order` with points-at-stake ON, after the shared weakness evidence is recorded; weak/high-value cards are pulled to the front. |
| **3. +points +interleave** | points-at-stake **ON** and interleave **ON** | `get_review_order` with both toggles ON: the arm-2 value ranking, then confusable sibling topics round-robined within each parent concept. |

Arms 2 and 3 see the **same recorded misses** (the diagnostic engine's output) and differ only in queue **ordering**, so arm 3 isolates the interleave feature alone.
Arm 1 is captured before any evidence is recorded, but because the OFF code path skips points-at-stake entirely and never consults weakness evidence, its order is unaffected by that evidence - so it is a clean, mastery-blind baseline for both feature arms.

## 2. Honest disclaimer - this is a simulation

You cannot run real human learners inside a script, so this is an explicit _simulated-learner_ experiment (the harness emits `"simulation": true`).
It is **not** a claim about real score gains.
What it does show, deterministically, is whether each feature's ordering change actually front-loads the cards a budget-limited learner should study first, and by how much - on a model where we control the ground truth.

Simulated learner:

- **48 due review cards** across **6 topics** (8 cards each), grouped under **2 parent concepts** (`BIOCHEM` and `PHYSSOC`) as hierarchical `concept::topic` tags and registered through a topic map, so the interleave feature has confusable sibling groups to reorder.
- Each card gets a hidden **true mastery** in `[0,1]`; some topics are weak (low mastery), some strong.
  Each topic also has a **yield weight** modelling how much it matters for the exam.
- **Yield weight is assigned independently of mastery** (some weak topics are high-yield, some low-yield), so the queue is _not_ handed a weight-aligned signal to exploit.
  Importantly, **the live queue never sees the weight at all** - it ranks purely by recorded weakness (miss rate).
  The weight lives only in our scoring metric, as a stand-in for exam yield.
- **Shared evidence:** for each card we record `round((1 - mastery) * 6)` misses out of 6 attempts, so the engine's weakness signal tracks true mastery (weaker card -> more recorded misses).
  This same evidence is present for arms 2 and 3; both feature arms consume it for ordering.
- **Creation/`due` order is shuffled independently of mastery**, so the plain-Anki default order (`ORDER BY due`) is a mastery-blind, fair baseline.
- **12 random seeds**; we report mean and range.

## 3. Pre-registered metric (fixed before results)

> **Primary metric - expected exam-score gain within the study budget.**
> With an equal budget of **K = 16** reviews (one third of the 48 due cards - a time-limited session), take each arm's ordering, "study" the first K cards, and score
>
> `expected_gain@K = sum over the first K cards of (1 - true_mastery) * yield_weight`.
>
> This rewards spending limited study time on cards the learner is weak on (high `1 - mastery`) and that matter (high weight).
> An ordering that surfaces weak, high-value cards earlier accumulates more expected gain within K.

**Direction predicted before running:**

- The **+points-at-stake** arm should score highest on this metric, because it maximises within-budget value directly by pulling weak, high-value cards to the front.
- The **+points +interleave** arm should land **between** plain and points-at-stake: interleave deliberately trades some within-budget value density for topic spacing (studying siblings apart), and this value-only metric does **not** reward spacing, so interleave is being scored on the axis it does not optimise.
  It should still beat plain Anki clearly, since it inherits the weakness-first ranking.
- **Plain Anki** is the mastery-blind baseline.
- The effect of both features should shrink as K -> N: if you review everything, ordering cannot change which cards you saw.

Supporting metrics (also fixed beforehand): **weak cards landing in the budget** (count with `mastery < 0.5` in the first K; weight-independent), the **ordering diagnostics** (positions changed per arm, weak card surfaced first, interleave differs from points-at-stake), and a **value-oracle reference** (the best possible order given the hidden truth) as an upper bound - not one of the three arms.

## 4. Results (12 seeds, K = 16 of 48)

### Primary metric - expected gain

| Arm                                           |      Mean | Range (min-max) |    Std |      vs Plain Anki |
| --------------------------------------------- | --------: | --------------: | -----: | -----------------: |
| 1. Plain Anki (default order)                 |      7.86 |     5.98 - 9.32 |   0.94 |           baseline |
| **2. +points-at-stake**                       | **15.58** |   14.59 - 17.44 |   0.81 | **+7.72 (+98.1%)** |
| 3. +points +interleave                        |     13.49 |   12.12 - 14.79 |   0.78 |     +5.63 (+71.6%) |
| _ref: value-oracle (upper bound, not an arm)_ |   _17.24_ | _16.12 - 18.11_ | _0.57_ |    _+9.38 (+119%)_ |

**Fraction of the deck's total achievable value captured in the first K:**
Plain **0.29** (0.21-0.34), +points-at-stake **0.58** (0.53-0.66), +points +interleave **0.51** (0.47-0.55).

**Weak cards (mastery < 0.5) landing in the K-card budget (of 16):**
Plain **5.25** (3-8), +points-at-stake **14.7** (13-16), +points +interleave **10.6** (9-11).

### Ordering diagnostic (supporting evidence)

| Signal                                                     | Value              |
| ---------------------------------------------------------- | ------------------ |
| Positions changed, +points-at-stake vs plain (of 48)       | 46.7 mean (43-48)  |
| Positions changed, +interleave vs +points-at-stake (of 48) | 43.75 mean (40-46) |
| **+interleave order differs from +points-at-stake order**  | **100%** of seeds  |
| First card is a weak card (`mastery < 0.5`), points arm    | **100%** of seeds  |
| First card is the single globally-weakest card, points arm | 25% of seeds       |

The middle two rows are the important ones for this rewrite: the interleave arm reorders the points-at-stake ranking on **every seed** (43.75 of 48 positions move on average), so arm 3 is a genuinely distinct build, not an alias of arm 2.

### Budget sensitivity (mean expected gain by budget K)

| Budget |  K | Plain | +points-at-stake | +points +interleave | Oracle |
| ------ | -: | ----: | ---------------: | ------------------: | -----: |
| 25%    | 12 |  5.71 |            12.54 |               10.54 |  14.78 |
| 33%    | 16 |  7.86 |            15.58 |               13.49 |  17.24 |
| 50%    | 24 | 12.56 |            20.26 |               17.76 |  21.20 |
| 75%    | 36 | 19.51 |            24.85 |               23.39 |  25.17 |
| 100%   | 48 | 26.72 |            26.72 |               26.72 |  26.72 |

Both feature arms beat plain by the largest margin at the tightest budget and converge with plain as the budget widens: +points-at-stake runs +119.5% at 25% budget down to +0.0% at full budget, and +points +interleave tracks it (+84.6% at 25%, +0.0% at full budget).

## 5. Did the features help?

**Yes - both features beat plain Anki clearly under a limited study budget, and the two feature arms are genuinely distinct.**

- At the pre-registered budget (K = 16), **+points-at-stake** surfaces **~98% more** expected exam-value than plain Anki (15.58 vs 7.86), and the separation is **clean across every seed**: the worst points-at-stake seed (14.59) still beats the best plain-Anki seed (9.32).
  It puts **~14.7 of 16** budget slots on genuinely weak cards, versus ~5.25 for plain, and reaches **~90%** of the value-oracle's gain (15.58 / 17.24).
- **+points +interleave** surfaces **~72% more** expected value than plain (13.49 vs 7.86), still a large, clean win, but it scores **below** pure points-at-stake on this metric (13.49 vs 15.58).
  That is expected: the metric rewards within-budget value only, and interleave deliberately round-robins confusable siblings to space them out, spending some value density on topic separation the metric does not credit.
  Interleave is being measured on the axis it does not optimise, and it still clears plain by a wide margin.
- **The interleave arm is measurably distinct from the points-at-stake arm** (it reorders the ranking on 100% of seeds, moving 43.75 of 48 positions on average), so arm 3 exercises a real, separate feature rather than collapsing onto arm 2.

**Honest caveats and null results:**

- **The benefit vanishes at full budget.**
  At K = N = 48 all three arms score identically (+0.0%): if you review the whole due pile, ordering cannot change which cards you saw.
  Both features help _precisely_ when study time is scarce, and their advantage grows as the budget tightens.
- **Headroom remains.**
  Neither feature arm reaches the oracle because the live queue ranks by **weakness only** and ignores topic yield - it will "spend" a budget slot on a weak-but-low-yield card ahead of a stronger high-yield one.
  Wiring topic weight into the live ranking is the obvious next improvement.
- **This metric under-credits interleave.**
  The primary metric measures within-budget value, not spacing quality, so it cannot reward the very thing interleave optimises (studying confusable siblings apart to reduce interference).
  The +71.6% here is therefore a _floor_ on interleave's usefulness, not a measure of its spacing benefit.
- **The single weakest card leads only 25% of the time**, even though a weak card _always_ leads (100%).
  This is an honest artifact of coarse evidence: with only 6 recorded attempts the miss rate is quantized to sixths, so the weakest tier ties and the tie is broken by default order.
  Finer evidence would sharpen this; it does not affect the headline metric, which scores the whole budget.

## 6. Reproduce

```bash
# self-test (original ordering check) + the 3-arm experiment, with assertions
./tools/speedrun_ablation.sh

# the 3-arm experiment only, full JSON incl. per-seed table (12 seeds default)
./tools/speedrun_ablation.sh --experiment

# choose the seed count, e.g. 24 seeds
./tools/speedrun_ablation.sh --experiment 24
```

Requires the built pylib bridge (`out/pyenv`, `out/pylib`).
Everything is deterministic given the seed list, so the numbers above reproduce exactly.

## 7. Deviations & limitations

- **Simulation, not a human trial.**
  By design (see §2).
  The metric is a proxy for "value surfaced within a limited session", not a measured MCAT delta.
- **Equal-budget design.**
  Every arm studies the same number of cards (K); the arms differ only in which cards that budget lands on, which is why they converge at K = N.
- **Interleave is scored on a value-only metric.**
  Arms 2 and 3 are genuinely distinct queue orders (arm 3 reorders arm 2 on 100% of seeds), but the primary metric measures within-budget value rather than spacing, so it under-credits interleave's actual objective.
  Interleave still beats plain here; a spacing-aware metric would be needed to credit its full benefit.
- **Topic yield weight is not read by the live queue** (it uses `topic_weight = 1.0` internally); it appears only in the scoring metric as an exam-yield proxy.
  This makes the test _harder_ for the features, not easier - they win on weakness alone.
- Per-seed values are in the `per_seed` block of `--experiment` output for full transparency.
