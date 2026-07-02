# Speedrun s7f - AI Card Check

Model `gpt-4o-mini` (temp=0, seed=7, cached) | 50 generated cards | CACHE (offline) | 0 new API calls this run


> A wrong card is worse than no card. The checker BLOCKS every card that is not CORRECT_USEFUL; only admitted cards would enter a deck.


## Headline counts (the three the task asks for)

- **Correct + useful (admitted):** 26
- **Wrong (factually incorrect vs source/gold):** 0
- **Correct but bad teaching (vague / trivial / duplicate / ungrounded):** 24
- **Blocked by the cutoff:** 24 of 50 (admitted 26).


## Pre-registered rubric + passing cutoff (fixed before results)

Each card is placed in exactly one class:
- **CORRECT_USEFUL** - correct per the source (and gold anchors where topics overlap), grounded in a citable source sentence, specific, non-trivial, non-duplicate.
- **WRONG** - factually incorrect vs the source or a gold anchor, or a false premise. The class we most want to catch.
- **CORRECT_BUT_BAD** - correct but poor teaching: vague/trivial, self-answering, near-duplicate, or not grounded in the source.

**Passing cutoff** - a card is ADMITTED iff ALL hold, else BLOCKED:
1. class == CORRECT_USEFUL;
2. source-grounded: citation vs source-sentence similarity >= 0.60 AND the judge agrees it is grounded;
3. not a duplicate: normalized-stem similarity to any earlier admitted card < 0.80;
4. passes triviality heuristics (non-empty answer, question has >= 2 content words, answer not contained in the question, question != answer);
5. judge confidence >= 0.60.

Checks are deterministic given the cached judge outputs, so the counts reproduce exactly from the committed cache.


## Source (the one real source; brief excerpt, attributed)

- **Title:** Cellular respiration (Wikipedia, The Free Encyclopedia)
- **URL:** https://en.wikipedia.org/wiki/Cellular_respiration
- **License:** CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)
- **Revision / retrieved:** 1355541234 / 2026-07-02
- **Excerpt SHA-256:** `32922b5ee43bcb4276ae6bfa53880dc83f139b4f74e1a9d2bea047d8acfdf444`
- **Attribution:** Wikipedia contributors, "Cellular respiration," Wikipedia, The Free Encyclopedia (revision 1355541234, retrieved 2026-07-02). Brief excerpt reused under CC BY-SA 4.0.

<details><summary>Source excerpt (verbatim, ~570 words)</summary>

```text
Cellular respiration is the process of oxidizing biological fuels using an inorganic electron acceptor, such as oxygen, to drive production of adenosine triphosphate (ATP), which stores chemical energy in a biologically accessible form. Cellular respiration may be described as a set of metabolic reactions and processes that take place in the cells to transfer chemical energy from nutrients to ATP, with the flow of electrons to an electron acceptor, and then release waste products. If the electron acceptor is oxygen, the process is more specifically known as aerobic cellular respiration. If the electron acceptor is a molecule other than oxygen, this is anaerobic cellular respiration – not to be confused with fermentation, which is also an anaerobic process, but it is not respiration, as no external electron acceptor is involved. The reactions involved in respiration are catabolic reactions, which break large molecules into smaller ones, producing ATP.

Nutrients that are commonly used by animal and plant cells in respiration include sugar, amino acids and fatty acids, and the most common oxidizing agent is molecular oxygen (O2).

Biology textbooks often state that 38 ATP molecules can be made per oxidized glucose molecule during cellular respiration (2 from glycolysis, 2 from the Krebs cycle, and about 34 from the electron transport system). However, this maximum yield is never quite reached because of losses due to leaky membranes as well as the cost of moving pyruvate and ADP into the mitochondrial matrix, and current estimates range around 29 to 30 ATP per glucose.

Aerobic metabolism is up to 15 times more efficient than anaerobic metabolism (which yields 2 molecules of ATP per 1 molecule of glucose).

The post-glycolytic reactions take place in the mitochondria in eukaryotic cells, and in the cytoplasm in prokaryotic cells.

Glycolysis is a metabolic pathway that takes place in the cytosol of cells in all living organisms. Glycolysis can be literally translated as "sugar splitting", and occurs regardless of oxygen's presence or absence. The process converts one molecule of glucose into two molecules of pyruvate (pyruvic acid), generating energy in the form of two net molecules of ATP. Four molecules of ATP per glucose are actually produced, but two are consumed as part of the preparatory phase.

Glucose + 2 NAD+ + 2 Pi + 2 ADP → 2 pyruvate + 2 NADH + 2 ATP + 2 H+ + 2 H2O + energy

An additional ATP is used to phosphorylate fructose 6-phosphate into fructose 1,6-bisphosphate by the help of phosphofructokinase.

Pyruvate is oxidized to acetyl-CoA and CO2 by the pyruvate dehydrogenase complex (PDC). The PDC contains multiple copies of three enzymes and is located in the mitochondria of eukaryotic cells and in the cytosol of prokaryotes. In the conversion of pyruvate to acetyl-CoA, one molecule of NADH and one molecule of CO2 is formed.

The citric acid cycle is also called the Krebs cycle or the tricarboxylic acid cycle.

The net gain from one cycle is 3 NADH and 1 FADH2 as hydrogen (proton plus electron) carrying compounds and 1 high-energy GTP, which may subsequently be used to produce ATP. Thus, the total yield from 1 glucose molecule (2 pyruvate molecules) is 6 NADH, 2 FADH2, and 2 ATP.

In eukaryotes, oxidative phosphorylation occurs in the mitochondrial cristae. It comprises the electron transport chain that establishes a proton gradient (chemiosmotic potential) across the boundary of the inner membrane by oxidizing the NADH produced from the Krebs cycle.
```
</details>


## Grounding / duplicate / triviality summary

- Grounded in the source (cite matched, judge agreed): 33/50
- Deterministic duplicates flagged: 6
- Triviality-flagged: 2
- Factually wrong (judge vs source/gold): 0


## Block list (every blocked card + why)

| id | class | reasons | Q -> A |
| --- | --- | --- | --- |
| `c03` | CORRECT_BUT_BAD | duplicate_of_c01 | What is aerobic cellular respiration? -> If the electron acceptor is oxygen, the process is more specifically known as aerobic cellular respiration. |
| `c04` | CORRECT_BUT_BAD | duplicate_of_c01 | What is anaerobic cellular respiration? -> If the electron acceptor is a molecule other than oxygen, this is anaerobic cellular respiration. |
| `c12` | CORRECT_BUT_BAD | trivial:too_short_question | What is glycolysis? -> Glycolysis is a metabolic pathway that takes place in the cytosol of cells in all living organisms. |
| `c26` | CORRECT_BUT_BAD | ungrounded | What is the primary function of cellular respiration? -> The primary function of cellular respiration is to transfer chemical energy from nutrients to ATP. |
| `c27` | CORRECT_BUT_BAD | ungrounded | What is the significance of ATP in cellular respiration? -> ATP stores chemical energy in a biologically accessible form. |
| `c28` | CORRECT_BUT_BAD | ungrounded | What is the difference between aerobic and anaerobic respiration? -> Aerobic respiration uses oxygen as the electron acceptor, while anaerobic respiration uses a molecule other than oxygen. |
| `c30` | CORRECT_BUT_BAD | ungrounded | What is produced during glycolysis besides ATP? -> Glycolysis also produces 2 NADH and 2 pyruvate molecules. |
| `c31` | CORRECT_BUT_BAD | ungrounded | What is the fate of the high-energy GTP produced in the Krebs cycle? -> The high-energy GTP may subsequently be used to produce ATP. |
| `c32` | CORRECT_BUT_BAD | ungrounded, trivial:question_equals_answer | What is the total number of ATP produced from one glucose molecule in glycolysis? -> The total number of ATP produced from one glucose molecule in glycolysis is 2 net ATP. |
| `c33` | CORRECT_BUT_BAD | ungrounded | What is the significance of the proton gradient established during oxidative phosphorylation? -> The proton gradient (chemiosmotic potential) is essential for ATP production during oxidative phosphorylation. |
| `c34` | CORRECT_BUT_BAD | ungrounded | What is the role of the electron transport chain in cellular respiration? -> The electron transport chain oxidizes NADH and establishes a proton gradient across the inner membrane. |
| `c35` | CORRECT_BUT_BAD | ungrounded | What is the overall process of cellular respiration? -> The overall process of cellular respiration involves the oxidation of biological fuels to produce ATP and release waste products. |
| `c36` | CORRECT_BUT_BAD | ungrounded | What is the relationship between glycolysis and the Krebs cycle? -> Glycolysis converts glucose into pyruvate, which is then used in the Krebs cycle after being converted to acetyl-CoA. |
| `c37` | CORRECT_BUT_BAD | ungrounded, duplicate_of_c29 | What is the role of NAD+ in glycolysis? -> NAD+ is reduced to NADH during glycolysis, which is essential for energy production. |
| `c38` | CORRECT_BUT_BAD | ungrounded | What is the significance of the Krebs cycle in cellular respiration? -> The Krebs cycle is significant because it produces NADH and FADH2, which are crucial for the electron transport chain. |
| `c39` | CORRECT_BUT_BAD | ungrounded | What is the main product of the Krebs cycle? -> The main products of the Krebs cycle include NADH, FADH2, and GTP. |
| `c40` | CORRECT_BUT_BAD | ungrounded | How many molecules of CO2 are produced from one glucose molecule in the Krebs cycle? -> From one glucose molecule, two molecules of CO2 are produced in the Krebs cycle. |
| `c41` | CORRECT_BUT_BAD | ungrounded | What is the role of the mitochondrial cristae in cellular respiration? -> The mitochondrial cristae are the site of oxidative phosphorylation in eukaryotic cells. |
| `c42` | CORRECT_BUT_BAD | ungrounded | What is the significance of the electron acceptor in cellular respiration? -> The electron acceptor is crucial for the flow of electrons during cellular respiration, determining whether the process is aerobic or anaerobic. |
| `c43` | CORRECT_BUT_BAD | duplicate_of_c21 | What is the net gain of ATP from one cycle of the Krebs cycle? -> The net gain from one cycle is 1 high-energy GTP, which may subsequently be used to produce ATP. |
| `c44` | CORRECT_BUT_BAD | ungrounded | What is the role of oxygen in aerobic respiration? -> Oxygen acts as the final electron acceptor in aerobic respiration. |
| `c46` | CORRECT_BUT_BAD | duplicate_of_c02 | What is the process of cellular respiration? -> The process of oxidizing biological fuels using an inorganic electron acceptor, such as oxygen, to drive production of adenosine triphosphate (ATP). |
| `c47` | CORRECT_BUT_BAD | duplicate_of_c02 | What is the main purpose of cellular respiration? -> To transfer chemical energy from nutrients to ATP. |
| `c48` | CORRECT_BUT_BAD | bad_teaching | What are the waste products released during cellular respiration? -> Waste products are released after the flow of electrons to an electron acceptor. |


## Examples by class


**CORRECT_USEFUL**
- `c01` Q: What is cellular respiration?
  - A: Cellular respiration is the process of oxidizing biological fuels using an inorganic electron acceptor, such as oxygen, to drive production of adenosine triphosphate (ATP), which stores chemical energy in a biologically accessible form.
  - cite (score 1.0): Cellular respiration is the process of oxidizing biological fuels using an inorganic electron acceptor, such as oxygen, to drive production of adenosine triphosphate (ATP), which stores chemical energy in a biologically accessible form.
  - judge: verdict=correct grounded=True teaching=useful conf=1.0 - The answer accurately reflects the definition of cellular respiration as stated in the source.
- `c02` Q: What are the main components of cellular respiration?
  - A: Cellular respiration may be described as a set of metabolic reactions and processes that take place in the cells to transfer chemical energy from nutrients to ATP, with the flow of electrons to an electron acceptor, and then release waste products.
  - cite (score 1.0): Cellular respiration may be described as a set of metabolic reactions and processes that take place in the cells to transfer chemical energy from nutrients to ATP, with the flow of electrons to an electron acceptor, and then release waste products.
  - judge: verdict=correct grounded=True teaching=useful conf=1.0 - The answer accurately reflects the description of cellular respiration provided in the source.
- `c05` Q: How does anaerobic cellular respiration differ from fermentation?
  - A: Anaerobic cellular respiration is not to be confused with fermentation, which is also an anaerobic process, but it is not respiration, as no external electron acceptor is involved.
  - cite (score 0.623): Anaerobic cellular respiration is not to be confused with fermentation, which is also an anaerobic process, but it is not respiration, as no external electron acceptor is involved.
  - judge: verdict=correct grounded=True teaching=useful conf=1.0 - The answer accurately reflects the distinction made in the SOURCE.

**WRONG**
- _(none)_

**CORRECT_BUT_BAD**
- `c03` Q: What is aerobic cellular respiration?
  - A: If the electron acceptor is oxygen, the process is more specifically known as aerobic cellular respiration.
  - cite (score 1.0): If the electron acceptor is oxygen, the process is more specifically known as aerobic cellular respiration.
  - judge: verdict=correct grounded=True teaching=useful conf=1.0 - The answer accurately describes aerobic cellular respiration as defined in the source.
  - blocked reasons: duplicate_of_c01
- `c04` Q: What is anaerobic cellular respiration?
  - A: If the electron acceptor is a molecule other than oxygen, this is anaerobic cellular respiration.
  - cite (score 1.0): If the electron acceptor is a molecule other than oxygen, this is anaerobic cellular respiration.
  - judge: verdict=correct grounded=True teaching=useful conf=1.0 - The answer accurately reflects the definition of anaerobic cellular respiration as stated in the source.
  - blocked reasons: duplicate_of_c01
- `c12` Q: What is glycolysis?
  - A: Glycolysis is a metabolic pathway that takes place in the cytosol of cells in all living organisms.
  - cite (score 1.0): Glycolysis is a metabolic pathway that takes place in the cytosol of cells in all living organisms.
  - judge: verdict=correct grounded=True teaching=useful conf=1.0 - The answer accurately describes glycolysis as stated in the source.
  - blocked reasons: trivial:too_short_question


## Full per-card table

| id | class | grounded | cite | dup | trivial | gold | verdict/conf |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `c01` | CORRECT_USEFUL | Y | 1.0 | - | n | -(0.538) | correct/1.0 |
| `c02` | CORRECT_USEFUL | Y | 1.0 | - | n | q11(0.578) | correct/1.0 |
| `c03` | CORRECT_BUT_BAD | Y | 1.0 | 0.875 | n | q11(0.643) | correct/1.0 |
| `c04` | CORRECT_BUT_BAD | Y | 1.0 | 0.848 | n | q11(0.667) | correct/1.0 |
| `c05` | CORRECT_USEFUL | Y | 0.623 | - | n | -(0.486) | correct/1.0 |
| `c06` | CORRECT_USEFUL | Y | 1.0 | - | n | q11(0.607) | correct/1.0 |
| `c07` | CORRECT_USEFUL | Y | 1.0 | - | n | -(0.545) | correct/1.0 |
| `c08` | CORRECT_USEFUL | Y | 1.0 | - | n | q01(0.662) | correct/1.0 |
| `c09` | CORRECT_USEFUL | Y | 1.0 | - | n | -(0.541) | correct/1.0 |
| `c10` | CORRECT_USEFUL | Y | 1.0 | - | n | -(0.434) | correct/1.0 |
| `c11` | CORRECT_USEFUL | Y | 1.0 | - | n | -(0.496) | correct/1.0 |
| `c12` | CORRECT_BUT_BAD | Y | 1.0 | - | Y | q02(0.655) | correct/1.0 |
| `c13` | CORRECT_USEFUL | Y | 1.0 | - | n | q04(0.592) | correct/1.0 |
| `c14` | CORRECT_USEFUL | Y | 1.0 | - | n | q02(0.578) | correct/1.0 |
| `c15` | CORRECT_USEFUL | Y | 1.0 | - | n | q02(0.691) | correct/1.0 |
| `c16` | CORRECT_USEFUL | Y | 1.0 | - | n | q02(0.564) | correct/1.0 |
| `c17` | CORRECT_USEFUL | Y | 1.0 | - | n | q02(0.667) | correct/1.0 |
| `c18` | CORRECT_USEFUL | Y | 1.0 | - | n | q07(0.632) | correct/1.0 |
| `c19` | CORRECT_USEFUL | Y | 1.0 | - | n | -(0.507) | correct/1.0 |
| `c20` | CORRECT_USEFUL | Y | 1.0 | - | n | q09(0.661) | correct/1.0 |
| `c21` | CORRECT_USEFUL | Y | 1.0 | - | n | q09(0.698) | correct/1.0 |
| `c22` | CORRECT_USEFUL | Y | 1.0 | - | n | q09(0.671) | correct/1.0 |
| `c23` | CORRECT_USEFUL | Y | 1.0 | - | n | q12(0.667) | correct/1.0 |
| `c24` | CORRECT_USEFUL | Y | 1.0 | - | n | q12(0.686) | correct/1.0 |
| `c25` | CORRECT_USEFUL | Y | 0.951 | - | n | q45(0.574) | correct/0.9 |
| `c26` | CORRECT_BUT_BAD | n | 0.431 | - | n | q11(0.703) | correct/1.0 |
| `c27` | CORRECT_BUT_BAD | n | 0.393 | - | n | q11(0.687) | correct/1.0 |
| `c28` | CORRECT_BUT_BAD | n | 0.443 | - | n | q11(0.614) | correct/1.0 |
| `c29` | CORRECT_USEFUL | Y | 0.664 | - | n | q02(0.622) | correct/1.0 |
| `c30` | CORRECT_BUT_BAD | n | 0.496 | - | n | q02(0.627) | correct/1.0 |
| `c31` | CORRECT_BUT_BAD | n | 0.518 | - | n | q38(0.562) | correct/1.0 |
| `c32` | CORRECT_BUT_BAD | n | 0.584 | - | Y | q02(0.564) | correct/1.0 |
| `c33` | CORRECT_BUT_BAD | n | 0.433 | - | n | -(0.47) | correct/1.0 |
| `c34` | CORRECT_BUT_BAD | n | 0.583 | - | n | q11(0.743) | correct/1.0 |
| `c35` | CORRECT_BUT_BAD | n | 0.421 | - | n | q11(0.709) | correct/1.0 |
| `c36` | CORRECT_BUT_BAD | n | 0.452 | - | n | q45(0.579) | correct/1.0 |
| `c37` | CORRECT_BUT_BAD | n | 0.384 | 0.8 | n | q02(0.757) | correct/0.9 |
| `c38` | CORRECT_BUT_BAD | n | 0.393 | - | n | q11(0.643) | correct/0.9 |
| `c39` | CORRECT_BUT_BAD | n | 0.449 | - | n | q02(0.7) | correct/1.0 |
| `c40` | CORRECT_BUT_BAD | n | 0.503 | - | n | -(0.464) | correct/0.9 |
| `c41` | CORRECT_BUT_BAD | n | 0.494 | - | n | q11(0.634) | correct/1.0 |
| `c42` | CORRECT_BUT_BAD | n | 0.502 | - | n | q11(0.792) | correct/0.9 |
| `c43` | CORRECT_BUT_BAD | Y | 0.715 | 0.826 | n | q01(0.605) | correct/1.0 |
| `c44` | CORRECT_BUT_BAD | n | 0.529 | - | n | q11(0.624) | correct/1.0 |
| `c45` | CORRECT_USEFUL | Y | 0.612 | - | n | -(0.456) | correct/1.0 |
| `c46` | CORRECT_BUT_BAD | Y | 1.0 | 0.8 | n | q11(0.655) | correct/1.0 |
| `c47` | CORRECT_BUT_BAD | Y | 1.0 | 0.84 | n | q11(0.645) | correct/1.0 |
| `c48` | CORRECT_BUT_BAD | Y | 1.0 | - | n | q11(0.614) | correct/0.9 |
| `c49` | CORRECT_USEFUL | Y | 1.0 | - | n | q11(0.604) | correct/1.0 |
| `c50` | CORRECT_USEFUL | Y | 1.0 | - | n | q01(0.627) | correct/1.0 |


## Detector self-test (planted cards; NOT part of the 50)

Proof the gates fire, since a clean generator may yield few or zero wrong cards. Each planted card has a known expected class.

| id | expected | detected | match | reasons |
| --- | --- | --- | --- | --- |
| `p1` | WRONG | WRONG | PASS | factually_wrong |
| `p2` | WRONG | WRONG | PASS | factually_wrong |
| `p3` | CORRECT_BUT_BAD | CORRECT_BUT_BAD | PASS | trivial:answer_in_question |
| `p4` | CORRECT_BUT_BAD | CORRECT_BUT_BAD | PASS | ungrounded |
| `p5` | CORRECT_USEFUL | CORRECT_USEFUL | PASS | - |
| `p6` | CORRECT_BUT_BAD | CORRECT_BUT_BAD | PASS | duplicate_of_p5 |

Self-test: 6/6 planted cards classified as expected.


## Judge reliability & limitations (honest)

- **Conservative by design.** Factual correctness is decided by an LLM judge, which is noisy. The deterministic gates (grounding, dedup, triviality) back it up, and the cutoff BLOCKS on any doubt, so a judge slip costs a useful card (blocked) rather than admitting a wrong one - the safe direction when a wrong card is worse than no card.
- **Verdict scope.** 'WRONG' means *contradicts the source/gold*; a correct-but-ungrounded or correct-but-vague card is CORRECT_BUT_BAD, not WRONG. Ungrounded cards dominate the block list because the generator, pushed past what ~570 words can support, restates facts without a verbatim citation.
- **Single-source risk.** Grounding guarantees faithfulness to THIS source, not to current consensus. The passage repeats the classic textbook "38 ATP per glucose" figure (while noting it is never actually reached; real yield ~29-30). Cards echoing "38 ATP" are graded correct *because they match the source*, yet are pedagogically dated - a reason a production deck should ground on vetted, current material.
- **Determinism.** The judge uses the same cached, temp=0, pinned-seed client as generation, so these counts reproduce exactly from the committed cache.


## Reproduce

```bash
# offline, from the committed cache (no API key needed):
tools/speedrun_card_check.sh

# re-generate cards + refresh the cache (needs OPENAI_API_KEY):
tools/speedrun_card_check.sh --generate
```

_LLM calls are cached in `speedrun_cardcheck_cache.json` (keyed by model+params+prompt); the default run is deterministic and network-free._

