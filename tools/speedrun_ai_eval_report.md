# Speedrun AI Diagnosis Coach - Held-out Eval

Model `gpt-4o-mini` (temp=0, cached) | abstain cutoff 0.55 | 32 labeled misses | 0 new API calls this run

**Leakage/integrity:** training corpus = none (zero-shot; grounded on each item's explanation); gold label in prompt = False; duplicate stems = 0 -> CLEAN.

## Method comparison

| Method                                 | Coverage | Accuracy (overall) | Accuracy (answered) | Wrong-answer rate |
| -------------------------------------- | -------- | ------------------ | ------------------- | ----------------- |
| Deterministic (signals only, baseline) | 100.0%   | 59.4%              | 59.4%               | 40.6%             |
| Keyword (baseline)                     | 100.0%   | 75.0%              | 75.0%               | 25.0%             |
| Vector (TF-IDF cosine, LOO NN)         | 100.0%   | 71.9%              | 71.9%               | 28.1%             |
| AI coach (source-grounded)             | 100.0%   | 78.1%              | 78.1%               | 21.9%             |

**AI vs best baseline (overall accuracy):** 3.1% (AI wins).

## AI coach - per-class precision / recall

| Class       | Precision | Recall |
| ----------- | --------- | ------ |
| memory      | 100.0%    | 62.5%  |
| reasoning   | 53.3%     | 100.0% |
| passage     | 100.0%    | 75.0%  |
| test_taking | 100.0%    | 75.0%  |

## AI coach - confusion matrix (rows = gold, cols = predicted)

| gold \ pred | memory | reasoning | passage | test_taking | abstain |
| ----------- | ------ | --------- | ------- | ----------- | ------- |
| memory      | 5      | 3         | 0       | 0           | 0       |
| reasoning   | 0      | 8         | 0       | 0           | 0       |
| passage     | 0      | 2         | 6       | 0           | 0       |
| test_taking | 0      | 2         | 0       | 6           | 0       |

## Per-item (id | gold | deterministic | keyword | vector | AI | AI source)

- `g01` memory | det=reasoning kw=reasoning vec=memory ai=memory | src: Transaminases require pyridoxal phosphate
- `g02` memory | det=memory kw=reasoning vec=memory ai=memory | src: Regulation of an enzyme by a molecule binding at a site other than the active site. Marked 'Again' on this SRS card.
- `g03` memory | det=reasoning kw=reasoning vec=memory ai=reasoning <- | src: Helicase unwinds the duplex at the fork; topoisomerase relieves supercoiling ahead of it.
- `g04` memory | det=memory kw=reasoning vec=memory ai=memory | src: 3 Na+ out, 2 K+ in per ATP hydrolyzed.
- `g05` memory | det=reasoning kw=reasoning vec=memory ai=reasoning <- | src: HDL returns cholesterol from tissues to the liver
- `g06` memory | det=reasoning kw=reasoning vec=memory ai=memory | src: Motor neurons release acetylcholine at the neuromuscular junction.
- `g07` reasoning | det=reasoning kw=reasoning vec=passage ai=reasoning | src: Catalysts lower activation energy for both directions equally; the student knew catalysis speeds reactions but applied it to only the forward direction.
- `g08` reasoning | det=reasoning kw=reasoning vec=passage ai=reasoning | src: High Km means low affinity
- `g09` reasoning | det=reasoning kw=reasoning vec=test_taking ai=reasoning | src: KE = 1/2 m v^2, so KE scales with the square of speed; doubling v quadruples KE.
- `g10` reasoning | det=reasoning kw=reasoning vec=reasoning ai=reasoning | src: Heat is a product of an exothermic reaction, so by Le Chatelier added heat shifts toward reactants.
- `g11` reasoning | det=reasoning kw=reasoning vec=test_taking ai=reasoning | src: Energetic coupling makes an unfavorable step proceed when the summed delta-G is negative.
- `g12` reasoning | det=reasoning kw=reasoning vec=test_taking ai=reasoning | src: Buffer capacity is maximal at pH = pKa
- `g13` passage | det=reasoning kw=passage vec=passage ai=passage | src: The passage states the conclusion rests on Figure 2, where the treatment and placebo curves overlap.
- `g14` passage | det=reasoning kw=passage vec=passage ai=passage | src: The passage attributes the elevation to hepatocyte damage
- `g15` passage | det=reasoning kw=passage vec=passage ai=reasoning <- | src: According to the passage, the author's inference only holds if self-reports track actual behaviour; the student picked an assumption the passage does not rely on.
- `g16` passage | det=reasoning kw=passage vec=passage ai=passage | src: The passage specifies the sole manipulated variable was the inhibitor; the student introduced a difference the passage rules out.
- `g17` passage | det=reasoning kw=passage vec=passage ai=passage | src: Table 1 shows rate rising across every temperature step
- `g18` passage | det=reasoning kw=passage vec=passage ai=passage | src: The graph plateaus at Vmax, so added substrate barely changes rate; the student read a linear trend the figure does not show.
- `g19` test_taking | det=test_taking kw=test_taking vec=test_taking ai=reasoning <- | src: Velocity is momentarily zero at the apex, but gravity still acts: acceleration is g downward throughout.
- `g20` test_taking | det=test_taking kw=test_taking vec=test_taking ai=test_taking | src: The peptide bond is an amide.
- `g21` test_taking | det=test_taking kw=test_taking vec=passage ai=test_taking | src: Water's extensive hydrogen bonding gives the highest boiling point.
- `g22` test_taking | det=test_taking kw=test_taking vec=test_taking ai=test_taking | src: Leucine's nonpolar side chain favors the interior; the others are charged.
- `g23` test_taking | det=test_taking kw=test_taking vec=test_taking ai=reasoning <- | src: 0.1 M HCl is strongly acidic
- `g24` test_taking | det=test_taking kw=test_taking vec=reasoning ai=test_taking | src: 1 kg = 1000 g, so 2.0 kg = 2000 g. The student rushed the decimal place.
- `g25` memory | det=memory kw=reasoning vec=memory ai=memory | src: About -70 mV. Marked 'Again' on this SRS card.
- `g26` memory | det=reasoning kw=reasoning vec=memory ai=reasoning <- | src: NADH donates electrons at Complex I, whereas FADH2 enters later at Complex II.
- `g27` reasoning | det=reasoning kw=reasoning vec=reasoning ai=reasoning | src: Resistance R = rho*L/A scales linearly with length, so doubling L doubles R. The student inverted the proportionality.
- `g28` reasoning | det=reasoning kw=reasoning vec=reasoning ai=reasoning | src: The common-ion effect shifts the dissolution equilibrium back toward the solid, lowering solubility.
- `g29` passage | det=reasoning kw=passage vec=passage ai=passage | src: Figure 1 shows accuracy rising then falling as arousal increases
- `g30` passage | det=reasoning kw=passage vec=passage ai=reasoning <- | src: The passage attributes the phenotype to nonfunctional leptin receptors; the student chose a behavioural cause the passage never mentions.
- `g31` test_taking | det=test_taking kw=test_taking vec=passage ai=test_taking | src: Atomic radius increases down a group, so helium
- `g32` test_taking | det=test_taking kw=test_taking vec=reasoning ai=test_taking | src: a = F/m = 10/2 = 5 m/s^2. The student rushed and multiplied instead of dividing.
