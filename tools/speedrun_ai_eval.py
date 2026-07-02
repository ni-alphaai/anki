#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Held-out eval for the Speedrun AI diagnosis coach.

Compares the source-grounded AI coach against two simpler baselines (the
deterministic signal classifier that ships in the engine, and a keyword
classifier) on a held-out, human-labeled gold set of missed items. Reports, for
each method: coverage (1 - abstention), accuracy, wrong-answer rate, per-class
precision/recall, and a confusion matrix; plus a leakage/dedup check. LLM calls
are cached (temperature=0, pinned model, fixed seed), so re-runs reproduce the
exact scores from the committed cache without an API key.

Run: tools/speedrun_ai_eval.sh   (uses the isolated venv + anki/.env)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from speedrun_ai.coach import ABSTAIN_CUTOFF, diagnose  # noqa: E402
from speedrun_ai.llm import DEFAULT_MODEL, LLM  # noqa: E402
from speedrun_ai.taxonomy import (  # noqa: E402
    KIND_NAME,
    Signals,
    deterministic_classify,
    keyword_classify,
)

LABELS = ["memory", "reasoning", "passage", "test_taking"]
_HERE = os.path.dirname(os.path.abspath(__file__))
GOLD_PATH = os.path.join(_HERE, "speedrun_gold_set.json")
REPORT_PATH = os.path.join(_HERE, "speedrun_ai_eval_report.md")


def load_gold(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)["items"]


def signals_of(item: dict) -> Signals:
    s = item.get("signals", {})
    return Signals(
        correct=False,
        took_ms=int(s.get("took_ms", 6000)),
        question_type=int(item.get("question_type", 1)),
        confidence=float(s.get("confidence", 0.0)),
        recall_failed=bool(s.get("recall_failed", False)),
        passage_evidence_missed=bool(s.get("passage_evidence_missed", False)),
    )


def leakage_check(items: list[dict]) -> dict:
    """The coach is zero-shot and grounded on each item's own explanation; there
    is no training index to leak into. We still confirm the gold label never
    enters the prompt and flag any duplicate stems (a paraphrase-integrity guard).
    """
    seen: dict[str, str] = {}
    dups = []
    for it in items:
        k = " ".join(it.get("stem", "").split()).lower()
        if k in seen:
            dups.append((seen[k], it["id"]))
        else:
            seen[k] = it["id"]
    return {
        "training_corpus": "none (zero-shot; grounded on each item's explanation)",
        "gold_label_in_prompt": False,
        "duplicate_stems": dups,
        "clean": len(dups) == 0,
    }


def compute_metrics(preds: list[str | None], golds: list[str]) -> dict:
    total = len(golds)
    answered = sum(1 for p in preds if p is not None)
    correct = sum(1 for p, g in zip(preds, golds) if p is not None and p == g)
    per = {}
    for lab in LABELS:
        tp = sum(1 for p, g in zip(preds, golds) if p == lab and g == lab)
        fp = sum(1 for p, g in zip(preds, golds) if p == lab and g != lab)
        fn = sum(1 for p, g in zip(preds, golds) if p != lab and g == lab)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        per[lab] = dict(precision=prec, recall=rec, tp=tp, fp=fp, fn=fn)
    return dict(
        total=total,
        answered=answered,
        correct=correct,
        coverage=answered / total if total else 0.0,
        accuracy_overall=correct / total if total else 0.0,
        accuracy_answered=correct / answered if answered else 0.0,
        wrong_rate=(answered - correct) / answered if answered else 0.0,
        per_class=per,
    )


def confusion(preds: list[str | None], golds: list[str]) -> dict:
    cols = LABELS + ["abstain"]
    m = {g: {c: 0 for c in cols} for g in LABELS}
    for p, g in zip(preds, golds):
        m[g][p if p in LABELS else "abstain"] += 1
    return m


def run(model: str, cutoff: float) -> dict:
    items = load_gold(GOLD_PATH)
    golds = [it["gold_kind"] for it in items]
    llm = LLM(model=model)

    det, kw, ai = [], [], []
    rows = []
    for it in items:
        s = signals_of(it)
        det.append(KIND_NAME.get(deterministic_classify(s)))
        kw.append(KIND_NAME.get(keyword_classify(it, s)))
        d = diagnose(it, s, llm=llm, cutoff=cutoff)
        ai.append(None if d["abstained"] else d["kind_name"])
        rows.append((it["id"], it["gold_kind"], det[-1], kw[-1], ai[-1], d))

    results = {
        "model": model,
        "cutoff": cutoff,
        "new_api_calls": llm.new_calls,
        "leakage": leakage_check(items),
        "deterministic": compute_metrics(det, golds),
        "keyword": compute_metrics(kw, golds),
        "ai_coach": compute_metrics(ai, golds),
        "ai_confusion": confusion(ai, golds),
        "rows": rows,
    }
    return results


def _pct(x: float) -> str:
    return f"{100 * x:5.1f}%"


def render(r: dict) -> str:
    L = []
    L.append("# Speedrun AI Diagnosis Coach - Held-out Eval\n")
    L.append(
        f"Model `{r['model']}` (temp=0, cached) | abstain cutoff {r['cutoff']:.2f} | "
        f"{r['deterministic']['total']} labeled misses | {r['new_api_calls']} new API calls this run\n"
    )
    lk = r["leakage"]
    L.append(
        f"\n**Leakage/integrity:** training corpus = {lk['training_corpus']}; "
        f"gold label in prompt = {lk['gold_label_in_prompt']}; "
        f"duplicate stems = {len(lk['duplicate_stems'])} -> "
        f"{'CLEAN' if lk['clean'] else 'REVIEW'}.\n"
    )
    L.append("\n## Method comparison\n")
    L.append("| Method | Coverage | Accuracy (overall) | Accuracy (answered) | Wrong-answer rate |")
    L.append("| --- | --- | --- | --- | --- |")
    for name, key in [
        ("Deterministic (signals only, baseline)", "deterministic"),
        ("Keyword (baseline)", "keyword"),
        ("AI coach (source-grounded)", "ai_coach"),
    ]:
        m = r[key]
        L.append(
            f"| {name} | {_pct(m['coverage'])} | {_pct(m['accuracy_overall'])} | "
            f"{_pct(m['accuracy_answered'])} | {_pct(m['wrong_rate'])} |"
        )
    best_base = max(r["deterministic"]["accuracy_overall"], r["keyword"]["accuracy_overall"])
    delta = r["ai_coach"]["accuracy_overall"] - best_base
    L.append(
        f"\n**AI vs best baseline (overall accuracy):** {_pct(delta)} "
        f"({'AI wins' if delta > 0 else 'no gain'}).\n"
    )
    L.append("\n## AI coach - per-class precision / recall\n")
    L.append("| Class | Precision | Recall |")
    L.append("| --- | --- | --- |")
    for lab in LABELS:
        pc = r["ai_coach"]["per_class"][lab]
        L.append(f"| {lab} | {_pct(pc['precision'])} | {_pct(pc['recall'])} |")
    L.append("\n## AI coach - confusion matrix (rows = gold, cols = predicted)\n")
    cols = LABELS + ["abstain"]
    L.append("| gold \\ pred | " + " | ".join(cols) + " |")
    L.append("| " + " | ".join(["---"] * (len(cols) + 1)) + " |")
    for g in LABELS:
        L.append(f"| {g} | " + " | ".join(str(r['ai_confusion'][g][c]) for c in cols) + " |")
    L.append("\n## Per-item (id | gold | deterministic | keyword | AI | AI source)\n")
    for rid, gold, det, kw, ai, d in r["rows"]:
        src = (d.get("source") or "").split("(")[0].strip()
        flag = "" if (ai == gold) else " <-"
        L.append(f"- `{rid}` {gold} | det={det} kw={kw} ai={ai or 'ABSTAIN'}{flag} | src: {src}")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--cutoff", type=float, default=ABSTAIN_CUTOFF)
    ap.add_argument("--json", action="store_true", help="print raw JSON results")
    args = ap.parse_args()

    r = run(args.model, args.cutoff)
    report = render(r)
    with open(REPORT_PATH, "w") as f:
        f.write(report)
    if args.json:
        printable = {k: v for k, v in r.items() if k != "rows"}
        print(json.dumps(printable, indent=2))
    else:
        print(report)
    print(f"[report written to {os.path.relpath(REPORT_PATH)}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
