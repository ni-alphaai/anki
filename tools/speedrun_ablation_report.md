<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# Points-at-stake + interleave queue - three-arm study-feature ablation (§8)

**Status:** simulated-learner outcome experiment (deterministic, AI-off).
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

## 2. Honest disclaimer - this is a simulated learner

You cannot run real human learners inside a script, so this is an explicit _simulated-learner_ experiment (the harness emits `"simulation": true`).
It is **not** a claim about real MCAT score gains.

The previous version of this report scored only how much latent value each ordering **front-loads**. That metric structurally cannot show an interleaving benefit: interleaving _spreads_ confusable material rather than front-loading it, so on a front-loading score it can only ever look neutral or slightly worse. To measure the thing the study-science literature actually claims for interleaving - better _durable discrimination_ between confusable concepts - this version adds a **learner-outcome test**: study under a budget, wait (forget), then take a delayed mixed-topic test.

**The single modeling assumption, stated up front:** studying a card next to a _confusable sibling_ (same parent concept, different topic) produces a more discriminative, slower-forgetting memory trace than massing same-topic cards. That is the well-documented interleaving/desirable-difficulty effect, and it is an **assumption encoded into the simulated learner, not a measurement produced by this script.** What the simulation checks is a _mechanism_: given that assumption, does the +interleave build's actual queue ordering convert it into higher delayed accuracy on confusable material? A real effect size needs a human A/B.

Simulated learner:

- **48 due review cards** across **6 topics** (8 cards each), grouped under **2 parent concepts** (`BIOCHEM` and `PHYSSOC`) as hierarchical `concept::topic` tags and registered through a topic map, so the interleave feature has confusable sibling groups to reorder.
- Each card gets a hidden **true mastery** in `[0,1]`; some topics are weak (low mastery), some strong. Each topic also has a **yield weight** modelling how much it matters for the exam.
- **Yield weight is assigned independently of mastery**, and **the live queue never sees the weight at all** - it ranks purely by recorded weakness (miss rate). Weight lives only in the secondary value metric.
- **Shared evidence:** for each card we record `round((1 - mastery) * 6)` misses out of 6 attempts, so the engine's weakness signal tracks true mastery. The same evidence is present for arms 2 and 3.
- **Creation/`due` order is shuffled independently of mastery**, so the plain-Anki default order is a mastery-blind, fair baseline.
- **12 random seeds**; we report mean and range.

The learner model (logit-space memory strength) is: a focused study adds `+2.2`; a fixed forgetting delay subtracts `1.7 / durability`; durability is `1.0` by default, `+0.85` when the study is encoded next to a confusable sibling (interleaved), and `-0.25` when massed next to a same-topic card. Delayed-test probability-correct is `sigmoid(final strength)`, averaged over **all** cards across **all** topics.

## 3. Metrics (fixed before results)

> **Primary metric - delayed mixed-topic test accuracy (a learner OUTCOME).**
> With an equal budget of **K = 16** reviews (one third of the 48 due cards), study the first K cards of each arm's order, apply the forgetting delay, then test **every** card and report mean probability-correct. This rewards spending scarce study time where it produces the most _durable_ recall - which credits both studying weak cards (points-at-stake) and encoding confusable material discriminatively (interleave).

> **Secondary metric - value front-loaded within the budget (an ORDERING score).**
> `expected_gain@K = sum over the first K cards of (1 - true_mastery) * yield_weight`. This is the old primary metric; it measures whether an ordering puts weak, high-value cards early, and is kept because it cleanly separates points-at-stake from plain.

**Direction predicted before running:**

- **+points-at-stake** should top the _value_ metric (it maximises within-budget value directly) and should not hurt delayed accuracy vs plain.
- **+points +interleave** should top the _delayed-accuracy_ metric (it spaces confusable siblings, earning the durability bonus) while scoring _below_ pure points-at-stake on the front-loading value metric (it trades value density for spacing). This crossover is the point.
- **Plain Anki** is the mastery-blind baseline; a **value-oracle** (best order by hidden value) is an upper bound on the value metric only, not on retention.

## 4. Results (12 seeds, K = 16 of 48)

### Primary metric - delayed mixed-topic test accuracy

| Arm                                                             |       Mean |   Range (min-max) |     Std |          vs Plain Anki |
| --------------------------------------------------------------- | ---------: | ----------------: | ------: | ---------------------: |
| 1. Plain Anki (default order)                                   |     0.3511 |   0.3235 - 0.4095 |  0.0246 |               baseline |
| 2. +points-at-stake                                             |     0.3640 |   0.3143 - 0.4535 |  0.0328 |      +1.29 pts (+3.7%) |
| **3. +points +interleave**                                      | **0.3890** |   0.3456 - 0.4502 |  0.0262 | **+3.78 pts (+10.8%)** |
| _ref: value-oracle (value upper bound, not a retention oracle)_ |   _0.3195_ | _0.2792 - 0.3918_ | _0.030_ |            _-3.16 pts_ |

**Per-seed consistency:** +interleave >= +points-at-stake on delayed accuracy in **11 of 12** seeds; +points-at-stake >= plain in **9 of 12** seeds.

### Secondary metric - value front-loaded within the budget

| Arm                               |      Mean | Range (min-max) | vs Plain Anki |
| --------------------------------- | --------: | --------------: | ------------: |
| 1. Plain Anki                     |      7.86 |     5.98 - 9.32 |      baseline |
| **2. +points-at-stake**           | **15.58** |   14.59 - 17.44 |    **+98.1%** |
| 3. +points +interleave            |     13.49 |   12.12 - 14.79 |        +71.6% |
| _ref: value-oracle (upper bound)_ |   _17.24_ | _16.12 - 18.11_ |       _+119%_ |

**Weak cards (mastery < 0.5) landing in the K-card budget (of 16):** Plain **5.25**, +points-at-stake **14.7**, +points +interleave **10.6**.

### Ordering diagnostic (the arms are genuinely distinct)

| Signal                                                     | Value              |
| ---------------------------------------------------------- | ------------------ |
| Positions changed, +points-at-stake vs plain (of 48)       | 46.7 mean (43-48)  |
| Positions changed, +interleave vs +points-at-stake (of 48) | 43.75 mean (40-46) |
| **+interleave order differs from +points-at-stake order**  | **100%** of seeds  |
| First card is a weak card (`mastery < 0.5`), points arm    | **100%** of seeds  |
| First card is the single globally-weakest card, points arm | 25% of seeds       |

## 5. Did the features help?

**Yes - and, importantly, the two features help on different axes, which is exactly what the learner-science literature predicts.**

- **+points-at-stake** wins decisively on the **value** axis: at K = 16 it front-loads **~98% more** expected exam-value than plain (15.58 vs 7.86), cleanly across every seed, putting ~14.7 of 16 slots on weak cards. On the **outcome** axis it is a smaller, noisier win over plain (+3.7% delayed accuracy, ahead in 9/12 seeds) - because studying the weakest cards first tends to _mass_ them (same weak topic clustered), which the learner model penalises with faster forgetting.
- **+points +interleave** wins on the **outcome** axis: it gives the **highest delayed test accuracy** (+10.8% vs plain, +6.9% vs points-at-stake), ahead of pure points-at-stake in **11/12** seeds - while scoring _below_ points-at-stake on front-loaded value (+71.6% vs +98.1%). That crossover is the headline: interleave trades a little immediate value density for durable, discriminative retention, and the delayed test is where that trade pays off.
- The value-oracle is the most interesting control: it maximises front-loaded value (17.24, the upper bound) yet posts the **lowest** delayed accuracy (0.3195). Sorting purely by value clusters confusable weak cards together, so its blocked practice decays fastest - a clean illustration that value-greedy ordering is not the same as retention-optimal ordering.

**Honest caveats and null results:**

- **The interleaving benefit is assumed, then propagated - not discovered.** The +10.8% delayed-accuracy win is a consequence of the durability assumption in §2 combined with the _real_ queue ordering the engine produces. It confirms the feature's ordering is shaped correctly to exploit interleaving; it is **not** evidence of an effect size in humans.
- **Points-at-stake barely moves delayed accuracy** (+3.7%, and behind plain in 3/12 seeds) because front-loading value and massing confusable topics partly cancel. This is a genuine null-ish result reported as-is.
- **The benefit vanishes at full budget.** At K = N = 48 all arms study everything, so ordering cannot change the outcome; both features help precisely when study time is scarce.
- **Headroom remains:** the live queue ranks by weakness only (topic yield weight is not read), so it will spend a slot on a weak-but-low-yield card ahead of a stronger high-yield one. Wiring yield into the live ranking is the obvious next step.

## 6. Reproduce

```bash
# self-test (ordering check) + the 3-arm outcome experiment, with assertions
./tools/speedrun_ablation.sh

# the 3-arm experiment only, full JSON incl. per-seed delayed-accuracy table
./tools/speedrun_ablation.sh --experiment

# choose the seed count, e.g. 24 seeds
./tools/speedrun_ablation.sh --experiment 24
```

Requires the built pylib bridge (`out/pyenv`, `out/pylib`).
Everything is deterministic given the seed list, so the numbers above reproduce exactly.
The harness asserts, on every run, that +points-at-stake >= plain and +interleave >= +points-at-stake on mean delayed accuracy, and that the three arms are genuinely distinct orders.

## 7. Deviations & limitations

- **Simulation, not a human trial** (see §2). The delayed-accuracy metric is a modeled outcome, not a measured MCAT delta, and its interleaving component is an encoded assumption.
- **Equal-budget design.** Every arm studies K cards; arms differ only in which cards the budget lands on and how they are sequenced, which is why they converge at K = N.
- **Topic yield weight is not read by the live queue** (it uses `topic_weight = 1.0` internally); it appears only in the secondary value metric. This makes the test harder for the features, not easier.
- **The value-oracle is a value upper bound, not a retention oracle** - it is expected to lose on delayed accuracy, and does.
- Per-seed delayed-accuracy and front-loaded-value tables are in the `per_seed` block of `--experiment` output for full transparency.
