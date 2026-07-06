<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->

# Speedrun §7c - Topic coverage map (AI-off)

**What this is.** A coverage map over the _full_ AAMC MCAT content outline: every
topic, whether the deck covers it, the percent covered (plain **and**
weight-weighted), and a demonstration that readiness **abstains** from a score
when coverage is below the give-up line.

Everything runs through the `SpeedrunService` backend - the same Rust engine the
desktop app and the phone use. No AI is involved, and **no Rust was changed by
this tool**: the finer outline is supplied as data and loaded at runtime via
`SetTopicMap`.

- Outline data: [`speedrun_mcat_outline.json`](./speedrun_mcat_outline.json)
- Tool: [`speedrun_coverage_map.py`](./speedrun_coverage_map.py) · wrapper: [`speedrun_coverage_map.sh`](./speedrun_coverage_map.sh)

## Reproduce

```bash
# synthetic demo + self-test (31 asserts)
./tools/speedrun_coverage_map.sh

# report a real deck's coverage against the full outline (non-destructive)
./tools/speedrun_coverage_map.sh path/to/collection.anki2
```

## What counts as "covered" (updated)

A topic is "covered" only when it holds at least
**`MIN_CARDS_PER_TOPIC = 3` tagged cards** (see `rslib/src/speedrun/coverage.rs`).
A single incidental card no longer lights up a whole topic, so a thin, freshly
imported deck can no longer read as near-100% covered when only a handful of
cards are actually studyable. The bar sits low enough that a genuinely (if
lightly) studied topic still counts, yet high enough to reject topics that were
merely touched.

## The give-up rule (updated)

`MIN_COVERAGE = 0.50` (see `rslib/src/speedrun/readiness.rs`). Readiness now gates
topic coverage on the **weaker of plain vs. weighted coverage**: it abstains
(`sufficient = false`) when

```
min(coverage, weighted_coverage) < 0.50
```

and the abstain reason reports the weighted figure, e.g.
`"topic coverage 55%/50% (weighted 44%)"`. The blocking dimension is `"coverage"`
in that case. This closes the earlier honesty gap: a deck that skips a
high-weight section but has high raw coverage can no longer show ready - the
weighted floor catches it. Tightening the per-topic card bar reinforces this in
the honest direction: thin decks now clear fewer topics, so readiness abstains on
them rather than projecting a score from a deck that is not really studyable.

## The outline

The engine ships only a 10-item placeholder outline (the 10 Foundational
Concepts). This artifact supplies the finer AAMC grain: the **31 discipline
"content categories"** (ids `1A`…`10A`) under the 10 Foundational Concepts.

- **Source:** AAMC MCAT content outline (structure paraphrased). Only the
  id/name/weight structure is reproduced; category names are short paraphrases,
  not the AAMC's copyrighted outline text. CARS is a skills-based section with no
  content categories and contributes no topics.
- **Weights:** a _documented emphasis estimate_ (not an official AAMC number).
  Natural-science concepts (biochemistry/biology/chemistry/physics) are weighted
  higher because that material is tested across the two science sections; the
  psychology/sociology concepts are weighted lower. Set every weight to `1.0` to
  disable weighting.

| Section     | Full name                                                     | Topics |   Weight | % of exam weight |
| ----------- | ------------------------------------------------------------- | -----: | -------: | ---------------: |
| Bio/Biochem | Biological and Biochemical Foundations of Living Systems      |      9 |     24.5 |            37.1% |
| Chem/Phys   | Chemical and Physical Foundations of Biological Systems       |     10 |     25.0 |            37.9% |
| Psych/Soc   | Psychological, Social, and Biological Foundations of Behavior |     12 |     16.5 |            25.0% |
| **Total**   |                                                               | **31** | **66.0** |         **100%** |

## Result [1] - Coverage map for a partially-studied deck

Loaded the 31-topic outline via `SetTopicMap`, tagged a partial deck (3 cards per
covered topic, right at the `MIN_CARDS_PER_TOPIC` bar), and read back
`GetCoverageReport`:

```text
  id     wt  cards  status    name
  ---- ---- ------  --------- ----------------------------------------
  [Bio/Biochem] Biological and Biochemical Foundations of Living Systems  -  7/9 topics, weighted 77.6%
  1A    3.0      3  COVERED   Structure and function of proteins and their amino acids
  1B    3.0      3  COVERED   Transmission of genetic information from gene to protein
  1C    3.0      0  gap       Heritable information across generations and genetic diversity
  1D    3.0      3  COVERED   Bioenergetics and fuel-molecule metabolism
  2A    2.5      3  COVERED   Assemblies of molecules and cells within organisms
  2B    2.5      0  gap       Structure, growth, physiology, and genetics of prokaryotes and viruses
  2C    2.5      3  COVERED   Cell division, differentiation, and specialization
  3A    2.5      3  COVERED   Nervous and endocrine systems coordinating the body
  3B    2.5      3  COVERED   Structure and integrated function of the major organ systems
  [Chem/Phys] Chemical and Physical Foundations of Biological Systems  -  4/10 topics, weighted 40.0%
  4A    2.5      3  COVERED   Motion, forces, energy, and equilibrium in living systems
  4B    2.5      3  COVERED   Fluids: circulation of blood, gas movement, and gas exchange
  4C    2.5      0  gap       Electrochemistry and electrical circuits
  4D    2.5      0  gap       Interaction of light and sound with matter
  4E    2.5      0  gap       Atomic structure, nuclear decay, and atomic chemical behavior
  5A    2.5      3  COVERED   Unique nature of water and its solutions
  5B    2.5      0  gap       Nature of molecules and intermolecular interactions
  5C    2.5      0  gap       Separation and purification methods
  5D    2.5      3  COVERED   Structure, function, and reactivity of biological molecules
  5E    2.5      0  gap       Principles of chemical thermodynamics and kinetics
  [Psych/Soc] Psychological, Social, and Biological Foundations of Behavior  -  7/12 topics, weighted 57.6%
  6A    1.5      3  COVERED   Sensing the environment
  6B    1.5      3  COVERED   Making sense of the environment (perception and cognition)
  6C    1.5      0  gap       Responding to the world (emotion and stress)
  7A    1.5      3  COVERED   Individual influences on behavior
  7B    1.5      0  gap       Social processes that influence behavior
  7C    1.5      0  gap       Attitude and behavior change
  8A    1.5      3  COVERED   Self-identity
  8B    1.5      3  COVERED   Social thinking
  8C    1.5      0  gap       Social interactions
  9A    1.0      3  COVERED   Understanding social structure
  9B    1.0      0  gap       Demographic characteristics and processes
  10A   1.0      3  COVERED   Social inequality

  topics_total       : 31
  topics_covered     : 18
  coverage (plain)   : 58.1%
  weighted_coverage  : 58.3%
  covered  (18) : 10A, 1A, 1B, 1D, 2A, 2C, 3A, 3B, 4A, 4B, 5A, 5D, 6A, 6B, 7A, 8A, 8B, 9A
  gaps     (13) : 1C, 2B, 4C, 4D, 4E, 5B, 5C, 5E, 6C, 7B, 7C, 8C, 9B
```

The engine's numbers are cross-checked against an independent Python computation
from the JSON (topics_covered, plain coverage, weighted coverage all match).

## Result [2] - Abstain below the line (the "10,000-card" case)

The spec case: _"A deck with 10,000 cards that skips a whole high-weight section
should not show ready."_ Built **10,000 mature review cards**, but every card
lands in the low-weight **Psych/Soc** section - the entire science half is
skipped. Memory and performance evidence were seeded so that **coverage is the
dimension on show**.

```text
built 10000 cards across 12 Psych/Soc topics (10000 mature review cards)
topics covered     : 12/31  (1A, 1B, 1C... all skipped)
coverage (plain)   : 38.7%   (< 50.0% line)
weighted_coverage  : 25.0%   (heavier skip shows up here)
readiness.sufficient : False
readiness.blocking   : coverage
readiness.reason     : not enough evidence: need topic coverage 39%/50% (weighted 25%)
```

→ **10,000 cards do not buy readiness.** `sufficient == False`, the blocking
dimension is `coverage`, and the reason reports both figures (plain 39% and
weighted 25%).

## Result [3] - Cross the line (both plain _and_ weighted must clear 50%)

Because the gate is on `min(plain, weighted)`, adding a few low-weight topics is
not enough. Covering the missing **high-weight sciences** (all Bio/Biochem +
Chem/Phys topics) takes both metrics over the line:

```text
added coverage of  : the 19 Bio/Biochem + Chem/Phys science topics
coverage (plain)   : 100.0%   (>= 50.0% line)
weighted_coverage  : 100.0%   (>= 50.0% line)
effective coverage : min(plain, weighted) = 100.0%
readiness.sufficient : True
readiness.blocking   : none
readiness.reason     : enough evidence to estimate readiness
```

→ Once **both** plain and weighted coverage clear 50% (and the other gates are
satisfied), readiness stops abstaining on the coverage dimension
(`blocking = none`) and becomes **sufficient**.

> Note: under the _old_ plain-only gate, covering just Foundational Concept 1
> (→ 51.6% plain / 43.2% weighted) was enough to "cross". Under the new gate that
> deck still abstains (`weighted 43% < 50%`), which is the point of the fix.

## Result [4] - The engine abstains on a skipped high-weight section

A deck can touch a _majority_ of topics yet still be short on exam weight. Here
the deck covers **all** of Psych/Soc plus all non-biochemistry Biology, but skips
biomolecules (FC1) and the **entire Chem/Phys section**. Memory + performance are
seeded (51 mature review cards, 30 exam attempts) so coverage is the binding
dimension:

```text
seeded             : 51 mature review cards (>= 20), 30 exam attempts
topics covered     : 17/31
coverage (plain)   : 54.8%   (looks above the 50.0% line)
weighted_coverage  : 43.9%   (below it - the heavy skip shows)
effective coverage : min(plain, weighted) = 43.9%   <- engine gates here
skipped (heavy)    : 1A, 1B, 1C, 1D, 4A, 4B, 4C, 4D, 4E, 5A, 5B, 5C, 5D, 5E
readiness.sufficient : False
readiness.blocking   : coverage
readiness.reason     : not enough evidence: need topic coverage 55%/50% (weighted 44%)
```

→ By raw topic count the deck looks ready (54.8% ≥ 50%), but weighted coverage
(43.9%) is below the line, so `min(plain, weighted)` is below the line and **the
engine abstains** - `sufficient == False`, `blocking == "coverage"`, and the
reason reports the weighted figure. This is the honesty fix, now **enforced** by
the give-up rule rather than merely reported.

## Interpretation

- **Custom outline really loads.** `topics_total == 31` (not the placeholder 10),
  and `GetTopicMap` reads back the `1A…10A` ids - proof the finer outline is
  driving the engine via `SetTopicMap`, with no Rust change.
- **Abstain works below the line.** Below 50% (plain or weighted) the engine
  refuses a score and names the coverage dimension, reporting both figures
  (Results [2]/[4]).
- **The weighted gate is enforced.** The give-up rule blocks on
  `min(plain, weighted)`, so a deck that skips a high-weight section
  (Result [4]: 54.8% plain but 43.9% weighted) is caught even though its raw
  count clears the floor. Crossing requires covering the heavy sections so that
  **both** metrics clear 50% (Result [3]).
- **Real decks.** Coverage requires notes tagged with the outline ids
  (`1A…10A`). Running against an untagged deck reports near-0% and says so; the
  tool operates on a **temp copy**, so a real collection's topic map and
  snapshots are never mutated.

_Self-test:_ `./tools/speedrun_coverage_map.sh` → **PASS (31 checks)**.
