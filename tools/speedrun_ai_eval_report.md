# Speedrun AI Diagnosis Coach - Held-out Eval

Model `gpt-4o-mini` (temp=0, cached) | abstain cutoff 0.55 | 24 labeled misses | 24 new API calls this run


**Leakage/integrity:** training corpus = none (zero-shot; grounded on each item's explanation); gold label in prompt = False; duplicate stems = 0 -> CLEAN.


## Method comparison

| Method | Coverage | Accuracy (overall) | Accuracy (answered) | Wrong-answer rate |
| --- | --- | --- | --- | --- |
| Deterministic (signals only, baseline) | 100.0% |  58.3% |  58.3% |  41.7% |
| Keyword (baseline) | 100.0% |  75.0% |  75.0% |  25.0% |
| AI coach (source-grounded) | 100.0% |  79.2% |  79.2% |  20.8% |

**AI vs best baseline (overall accuracy):**   4.2% (AI wins).


## AI coach - per-class precision / recall

| Class | Precision | Recall |
| --- | --- | --- |
| memory | 100.0% |  66.7% |
| reasoning |  54.5% | 100.0% |
| passage | 100.0% |  83.3% |
| test_taking | 100.0% |  66.7% |

## AI coach - confusion matrix (rows = gold, cols = predicted)

| gold \ pred | memory | reasoning | passage | test_taking | abstain |
| --- | --- | --- | --- | --- | --- |
| memory | 4 | 2 | 0 | 0 | 0 |
| reasoning | 0 | 6 | 0 | 0 | 0 |
| passage | 0 | 1 | 5 | 0 | 0 |
| test_taking | 0 | 2 | 0 | 4 | 0 |

## Per-item (id | gold | deterministic | keyword | AI | AI source)

- `g01` memory | det=reasoning kw=reasoning ai=memory | src: Transaminases require pyridoxal phosphate
- `g02` memory | det=memory kw=reasoning ai=memory | src: Regulation of an enzyme by a molecule binding at a site other than the active site. Marked 'Again' on this SRS card.
- `g03` memory | det=reasoning kw=reasoning ai=reasoning <- | src: Helicase unwinds the duplex at the fork; topoisomerase relieves supercoiling ahead of it.
- `g04` memory | det=memory kw=reasoning ai=memory | src: 3 Na+ out, 2 K+ in per ATP hydrolyzed.
- `g05` memory | det=reasoning kw=reasoning ai=reasoning <- | src: HDL returns cholesterol from tissues to the liver
- `g06` memory | det=reasoning kw=reasoning ai=memory | src: Motor neurons release acetylcholine at the neuromuscular junction.
- `g07` reasoning | det=reasoning kw=reasoning ai=reasoning | src: Catalysts lower activation energy for both directions equally; the student knew catalysis speeds reactions but applied it to only the forward direction.
- `g08` reasoning | det=reasoning kw=reasoning ai=reasoning | src: High Km means low affinity
- `g09` reasoning | det=reasoning kw=reasoning ai=reasoning | src: KE = 1/2 m v^2, so KE scales with the square of speed; doubling v quadruples KE.
- `g10` reasoning | det=reasoning kw=reasoning ai=reasoning | src: Heat is a product of an exothermic reaction, so by Le Chatelier added heat shifts toward reactants.
- `g11` reasoning | det=reasoning kw=reasoning ai=reasoning | src: Energetic coupling makes an unfavorable step proceed when the summed delta-G is negative.
- `g12` reasoning | det=reasoning kw=reasoning ai=reasoning | src: Buffer capacity is maximal at pH = pKa
- `g13` passage | det=reasoning kw=passage ai=passage | src: The passage states the conclusion rests on Figure 2, where the treatment and placebo curves overlap.
- `g14` passage | det=reasoning kw=passage ai=passage | src: The passage attributes the elevation to hepatocyte damage
- `g15` passage | det=reasoning kw=passage ai=reasoning <- | src: According to the passage, the author's inference only holds if self-reports track actual behaviour; the student picked an assumption the passage does not rely on.
- `g16` passage | det=reasoning kw=passage ai=passage | src: The passage specifies the sole manipulated variable was the inhibitor; the student introduced a difference the passage rules out.
- `g17` passage | det=reasoning kw=passage ai=passage | src: Table 1 shows rate rising across every temperature step
- `g18` passage | det=reasoning kw=passage ai=passage | src: The graph plateaus at Vmax, so added substrate barely changes rate; the student read a linear trend the figure does not show.
- `g19` test_taking | det=test_taking kw=test_taking ai=reasoning <- | src: Velocity is momentarily zero at the apex, but gravity still acts: acceleration is g downward throughout.
- `g20` test_taking | det=test_taking kw=test_taking ai=test_taking | src: The peptide bond is an amide.
- `g21` test_taking | det=test_taking kw=test_taking ai=test_taking | src: Water's extensive hydrogen bonding gives the highest boiling point.
- `g22` test_taking | det=test_taking kw=test_taking ai=test_taking | src: Leucine's nonpolar side chain favors the interior; the others are charged.
- `g23` test_taking | det=test_taking kw=test_taking ai=reasoning <- | src: 0.1 M HCl is strongly acidic
- `g24` test_taking | det=test_taking kw=test_taking ai=test_taking | src: 1 kg = 1000 g, so 2.0 kg = 2000 g. The student rushed the decimal place.
