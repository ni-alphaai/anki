# Speedrun AI note: what we built, why, and what we skipped

This is the required Friday note on the AI layer.
It covers what the AI does, why it exists, what we deliberately left out, and how to reproduce the evaluation.
The short version: the AI is an optional, source-grounded diagnosis coach that must beat a simpler baseline and abstain when unsure, and the whole app still produces every score with AI switched off.

## What we built

We built one AI feature: a **source-grounded diagnosis coach**.
When a student misses an exam-style question, the coach labels the single root-cause failure mode and routes it to a repair.
The four modes are `memory`, `reasoning`, `passage`, and `test_taking`, mirroring the engine's deterministic classifier in [rslib/src/speedrun/mod.rs](../../rslib/src/speedrun/mod.rs).
Each mode maps to a concrete next action (resurface via spaced repetition, concept-linked application practice, passage-comprehension practice, or test-taking strategy).

The coach lives in [anki/tools/speedrun_ai/](../../tools/speedrun_ai/): `coach.py` (the prompt and grounding), `llm.py` (a thin, cached OpenAI client), `taxonomy.py` (the rubric plus the two baselines), and `diagnose_cli.py` (the stdin/stdout entry point).
The desktop app calls it through [qt/aqt/speedrun_ai.py](../../qt/aqt/speedrun_ai.py), which runs the coach as a short-lived subprocess in an isolated venv (`out/ai-venv`), off the UI thread.
The model is `gpt-4o-mini` at `temperature=0` with a fixed `seed=7` and a JSON-only response format.

## Why we built it (and why it beats the baseline)

Anki's FSRS already models memory, and the engine already has a deterministic, signals-only classifier that is the AI-off path.
That classifier only sees behavioural signals: time taken, self-confidence, question type, and (for SRS reviews) whether the student pressed "Again".
On a real exam-style multiple-choice question there is no "Again" signal and no view of which distractor was chosen, so signals alone cannot tell a genuine reasoning slip from a missing fact or a misread passage.

The coach reads the item content and the answer explanation, so it can distinguish those cases.
On our held-out gold set the payoff is clear: the deterministic baseline scores 59.4% and the coach scores 78.1%, and the coach also beats both "simpler methods" we were asked to compare against (keyword 75.0%, TF-IDF vector nearest-neighbour 71.9%).
The AI is strictly enrichment layered on the required deterministic path; it does not replace it.

## Every output traces to a named source

The coach must ground its call in the question's answer explanation, which is the named source, and it returns that citation in a `source` field.
The eval prints the grounding source for every single item (the `src:` column in [tools/speedrun_ai_eval_report.md](../../tools/speedrun_ai_eval_report.md)).
The failure-mode definitions themselves are a fixed, versioned rubric ("Speedrun failure-mode rubric v1", from `mod.rs` and the project brainlift), so the classification criteria are also traceable rather than improvised.

## The held-out eval, the cutoff, and the baseline comparison

The eval runs before any student sees a coach output and is one command: `./tools/speedrun_ai_eval.sh`.
It scores 32 labeled misses with an abstain cutoff of **0.55** and reports accuracy and wrong-answer rate for the coach against all three baselines.

| Method                                 | Accuracy (overall) | Wrong-answer rate |
| -------------------------------------- | ------------------ | ----------------- |
| Deterministic (signals only, baseline) | 59.4%              | 40.6%             |
| Keyword (baseline)                     | 75.0%              | 25.0%             |
| Vector (TF-IDF cosine, LOO NN)         | 71.9%              | 28.1%             |
| AI coach (source-grounded)             | 78.1%              | 21.9%             |

Runs are deterministic: every model response is cached on disk keyed by (model, params, prompt), so a grader reproduces the exact scores from the committed cache without an API key.
The held-out data is verified clean by a separate leakage scan (`./tools/speedrun_leakage_check.sh`, verdict CLEAN with a 7/7 detector self-test), which matters because a leaked test item would zero this model in grading.

## The app still scores with AI off

AI is off by default; the deterministic classifier is the required path and the eval baseline.
On any error, missing venv, missing key, or low confidence, the coach returns an abstention and the caller falls back to the deterministic classifier, so the reviewer and practice flows never depend on the model.
The full readiness pipeline runs AI-off end to end (`./tools/speedrun_e2e_full.sh`): it abstains on thin data and then commits to a real in-range score (517, likely range 506-527) with three separate signals, entirely without the AI layer.

## What we deliberately skipped

We kept the AI off the review hot path.
The coach is asynchronous and never blocks grading or the next-card transition, and it has a hard 30-second subprocess timeout that degrades to abstain, so it cannot threaten the latency budgets.

We do not transmit the student's private self-explanation transcript to the model.
The coach receives only the question content (stem, options, correct answer, chosen answer, explanation) and coarse behavioural signals; the self-explanation stays on-device.

We did not ship generative features as a dependency.
There is no always-on card generator and no freeform chatbot tutor; the AI card-check gold set exists as a safety gate (challenge 7f) but generation is not part of the shipping default path.
We also did not fine-tune anything: the coach is zero-shot and grounded per item, so there is no training corpus and therefore nothing to leak from one.

## Reproduce

```bash
cd anki
./tools/speedrun_ai_eval.sh        # accuracy + wrong-answer rate + baseline side-by-side (cutoff 0.55)
./tools/speedrun_leakage_check.sh  # held-out data is CLEAN; detector self-test 7/7
./tools/speedrun_e2e_full.sh       # the three signals compute with AI off
```
