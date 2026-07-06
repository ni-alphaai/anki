# Speedrun AI Diagnosis Coach - Held-out Eval

Model `gpt-4o` (temp=0, cached) | abstain cutoff 0.55 | 32 labeled misses | 0 new API calls this run


**Leakage/integrity:** training corpus = none (zero-shot; grounded on each item's explanation); gold label in prompt = False; duplicate stems = 0 -> CLEAN.


## Method comparison

| Method | Coverage | Accuracy (overall) | Accuracy (answered) | Wrong-answer rate |
| --- | --- | --- | --- | --- |
| Deterministic (signals only, baseline) | 100.0% |  59.4% |  59.4% |  40.6% |
| Keyword (baseline) | 100.0% |  75.0% |  75.0% |  25.0% |
| Vector (TF-IDF cosine, LOO NN) | 100.0% |  71.9% |  71.9% |  28.1% |
| AI coach (source-grounded) | 100.0% |  87.5% |  87.5% |  12.5% |

**AI vs best baseline (overall accuracy):**  12.5% (AI wins).


## AI coach - per-class precision / recall

| Class | Precision | Recall |
| --- | --- | --- |
| memory |  80.0% | 100.0% |
| reasoning |  77.8% |  87.5% |
| passage | 100.0% | 100.0% |
| test_taking | 100.0% |  62.5% |

## AI coach - confusion matrix (rows = gold, cols = predicted)

| gold \ pred | memory | reasoning | passage | test_taking | abstain |
| --- | --- | --- | --- | --- | --- |
| memory | 8 | 0 | 0 | 0 | 0 |
| reasoning | 1 | 7 | 0 | 0 | 0 |
| passage | 0 | 0 | 8 | 0 | 0 |
| test_taking | 1 | 2 | 0 | 5 | 0 |

## Per-item (id | gold | deterministic | keyword | vector | AI | AI source)

- `g01` memory | det=reasoning kw=reasoning vec=memory ai=memory | src: Answer explanation
- `g02` memory | det=memory kw=reasoning vec=memory ai=memory | src: Answer explanation and SRS behavior
- `g03` memory | det=reasoning kw=reasoning vec=memory ai=memory | src: Answer explanation
- `g04` memory | det=memory kw=reasoning vec=memory ai=memory | src: answer explanation
- `g05` memory | det=reasoning kw=reasoning vec=memory ai=memory | src: Answer explanation
- `g06` memory | det=reasoning kw=reasoning vec=memory ai=memory | src: Answer explanation
- `g07` reasoning | det=reasoning kw=reasoning vec=passage ai=reasoning | src: Answer explanation
- `g08` reasoning | det=reasoning kw=reasoning vec=passage ai=memory <- | src: Answer explanation
- `g09` reasoning | det=reasoning kw=reasoning vec=test_taking ai=reasoning | src: Answer explanation
- `g10` reasoning | det=reasoning kw=reasoning vec=reasoning ai=reasoning | src: Answer explanation
- `g11` reasoning | det=reasoning kw=reasoning vec=test_taking ai=reasoning | src: answer explanation
- `g12` reasoning | det=reasoning kw=reasoning vec=test_taking ai=reasoning | src: Answer explanation
- `g13` passage | det=reasoning kw=passage vec=passage ai=passage | src: Answer explanation
- `g14` passage | det=reasoning kw=passage vec=passage ai=passage | src: Answer explanation
- `g15` passage | det=reasoning kw=passage vec=passage ai=passage | src: Answer explanation
- `g16` passage | det=reasoning kw=passage vec=passage ai=passage | src: Answer explanation
- `g17` passage | det=reasoning kw=passage vec=passage ai=passage | src: Answer explanation
- `g18` passage | det=reasoning kw=passage vec=passage ai=passage | src: Answer explanation
- `g19` test_taking | det=test_taking kw=test_taking vec=test_taking ai=reasoning <- | src: Answer explanation
- `g20` test_taking | det=test_taking kw=test_taking vec=test_taking ai=memory <- | src: Answer explanation
- `g21` test_taking | det=test_taking kw=test_taking vec=passage ai=test_taking | src: Answer explanation
- `g22` test_taking | det=test_taking kw=test_taking vec=test_taking ai=test_taking | src: Answer explanation
- `g23` test_taking | det=test_taking kw=test_taking vec=test_taking ai=reasoning <- | src: answer explanation
- `g24` test_taking | det=test_taking kw=test_taking vec=reasoning ai=test_taking | src: Answer explanation
- `g25` memory | det=memory kw=reasoning vec=memory ai=memory | src: Answer explanation
- `g26` memory | det=reasoning kw=reasoning vec=memory ai=memory | src: Answer explanation
- `g27` reasoning | det=reasoning kw=reasoning vec=reasoning ai=reasoning | src: Answer explanation
- `g28` reasoning | det=reasoning kw=reasoning vec=reasoning ai=reasoning | src: answer explanation
- `g29` passage | det=reasoning kw=passage vec=passage ai=passage | src: Answer explanation
- `g30` passage | det=reasoning kw=passage vec=passage ai=passage | src: Answer explanation
- `g31` test_taking | det=test_taking kw=test_taking vec=passage ai=test_taking | src: Answer explanation
- `g32` test_taking | det=test_taking kw=test_taking vec=reasoning ai=test_taking | src: answer explanation
