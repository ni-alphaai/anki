# Speedrun model descriptions

Speedrun reports three separate signals instead of one blended readiness number: memory, performance, and readiness.
Each has an explicit give-up rule and is computed with AI off; the deterministic engine is the required path, and any optional AI must beat it.
Every score ships with its point estimate, its range, the percent of the exam covered, a confidence indicator, the time it was last computed, the main reasons behind it, and the rule for when it abstains.

---

## 1. Memory model (can you recall a fact)

Question it answers: given what the student has studied, how likely are they to recall a given fact right now, and are those probabilities honest?

Inputs and method.
The memory signal reuses Anki's FSRS substrate rather than reinventing forgetting.
The reported memory number is the mature-card fraction: `mature_cards / review_cards`, where a card is mature at `ivl >= 21` days (Anki's standard mature threshold), computed in [`readiness.rs`](../../rslib/src/speedrun/readiness.rs) and [`performance.rs`](../../rslib/src/speedrun/performance.rs).
Honesty of the underlying per-item recall probabilities is checked by calibration ([`calibration.rs`](../../rslib/src/speedrun/calibration.rs)): each attempt can capture the model's pre-answer predicted probability (`sr_attempts.predicted`), and `GetCalibrationReport` scores those predictions against actual outcomes with the Brier score and log loss over ten equal-width reliability bins.

Calibration evidence (held-out).
On held-out predictions, a well-calibrated generator scores Brier 0.1594 / log-loss 0.4786, while an over-confident generator making the same average prediction scores a worse Brier 0.2634 / log-loss 0.7799 - the proper score catches the miscalibration a single accuracy number would miss.
See [`tools/speedrun_calibration_report.md`](../../tools/speedrun_calibration_report.md) and its reliability chart.

Give-up rule.
Calibration abstains below `MIN_PREDICTIONS = 20` captured predictions (`sufficient == false`, note `"not enough predictions: n/20"`).
For readiness contribution, the memory dimension is considered sufficient only at `MIN_REVIEW_CARDS = 20` review cards; below that, readiness reports memory as the blocking dimension rather than trusting a thin sample.

Known limits.
The mature-fraction proxy is a coarse recall estimate; it is deliberately simple and transparent for v1, and the calibration harness is what keeps it honest rather than a claim that the point estimate is individually precise.

---

## 2. Performance model (can you apply it on a new question)

Question it answers: can the student apply a remembered fact to a new, reworded exam-style question - not just recognise the original card?

Inputs and method.
Performance is measured from held-out `sr_question_items`: reworded questions whose source card the SRS already tracks, which are never added to the collection as cards, so answering them cannot leak into that card's scheduling (held-out discipline is structural, enforced by [`leakage.rs`](../../rslib/src/speedrun/leakage.rs) + the leakage check).
`summarize_performance` in [`performance.rs`](../../rslib/src/speedrun/performance.rs) averages, per source card, exam-style accuracy (`correct / attempts`) and the binary recall proxy (mature = 1.0), then reports the recall-vs-performance gap = recall_rate - performance_rate.
Averaging per card (not per attempt) stops a single heavily-drilled card from dominating.

What the gap means.
A positive gap (> 0.1) means recall outruns application - the student remembers the card text but cannot yet use it on a new question, so the memory-to-application bridge is weak.
A negative gap (< -0.1) is flagged as unusual and prompts a leakage check.
Near zero means recall and performance are aligned.

Give-up rule.
The gap abstains below `MIN_CARDS_FOR_GAP = 5` source cards with exam-style attempts (`sufficient == false`, note `"not enough evidence: n/5 cards with exam-style attempts"`).
For readiness, the performance dimension is sufficient only at `MIN_EXAM_ATTEMPTS = 20` exam-style attempts.

Held-out accuracy.
Performance accuracy on the held-out exam-style set is reported by the eval harness; the AI diagnosis layer is separately evaluated (78.1% accuracy vs keyword 75.0% / TF-IDF 71.9% / deterministic 59.4%) in [`tools/speedrun_ai_eval_report.md`](../../tools/speedrun_ai_eval_report.md), but performance scoring itself is AI-off.

---

## 3. Readiness model (what you would score today)

Question it answers: on the 472-528 MCAT scale, what would the student score today, with an honest range - or should the app show nothing yet?

Inputs and method.
`compute_readiness` in [`readiness.rs`](../../rslib/src/speedrun/readiness.rs) combines the three raw signals into a composite and maps it onto the MCAT scale:

- memory = `mature_cards / review_cards`
- performance = `exam_correct / exam_attempts`
- coverage = `topics_covered / topics_total` (a topic is covered when at least one note is tagged with it; see [`coverage.rs`](../../rslib/src/speedrun/coverage.rs)), with a parallel weight-weighted coverage
- composite = `0.4*memory + 0.4*performance + 0.2*coverage`
- readiness_scaled = `472 + composite * (528 - 472)`, clamped to [472, 528]

Range (how sure).
The likely range is a normal-approximation confidence band on the performance proportion, scaled to the MCAT span: half-width = `56 * 1.96 * sqrt(p(1-p)/exam_attempts)`.
With no exam evidence the band is deliberately as wide as half the scale, so uncertainty is visible rather than hidden.
This is a transparent v1 mapping, not a psychometric equating; the honesty comes from the range and the give-up rule, not from a claim that the point estimate is exam-accurate. (Validating the projected score against real practice-test scores is the spec's bonus Step 4 and is explicitly not yet claimed.)

Give-up rule (write it down).
Readiness shows no number until all of the following hold:

- graded attempts >= 30 (`MIN_GRADED_ATTEMPTS`)
- exam-style attempts >= 20 (`MIN_EXAM_ATTEMPTS`)
- review cards >= 20 (`MIN_REVIEW_CARDS`)
- effective topic coverage >= 50% (`MIN_COVERAGE`), where effective = min(raw coverage, weighted coverage)

Gating on the weaker of raw vs weighted coverage is deliberate: a deck can cover many low-weight topics by count yet skip a whole high-weight section, and readiness must abstain in that case (the "10,000-card deck that skips a high-weight topic" adversarial case).
When abstaining, the app reports exactly what is missing (e.g. `"not enough evidence: need exam-style attempts 14/20"`) and names the single blocking dimension (memory / performance / coverage / attempts), so the student knows what to do next.

Why three numbers, not one.
Blending memory, performance, and coverage into one percentage hides the failure mode that matters most for the MCAT: a student who has memorised cards (high memory) but cannot apply them (low performance), or who has drilled a narrow slice of the exam (low coverage).
Keeping them separate, each with its own give-up rule, is what lets the app say "I don't know yet" instead of inventing a confident-looking number.
