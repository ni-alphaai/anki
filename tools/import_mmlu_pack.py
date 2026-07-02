#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Convert MMLU (MIT) MCAT-relevant subsets into a Speedrun question pack.

MMLU: Hendrycks et al., "Measuring Massive Multitask Language Understanding"
(ICLR 2021), distributed under the MIT License (https://github.com/hendrycks/test).

This downloads the official MMLU data (CSV) once, selects the MCAT-relevant
subjects, maps each to a broad MCAT topic, and writes a bundled question pack in
Speedrun's pack schema (provenance=1, open_licensed). The resulting JSON is
committed so importing it later is fully offline; import it with:

    ./tools/import_question_pack.sh tools/speedrun_mmlu_pack.json <collection.anki2>

Usage:
    python tools/import_mmlu_pack.py                      # download + write the pack
    python tools/import_mmlu_pack.py --data-dir DIR       # use an extracted MMLU data/ dir
    python tools/import_mmlu_pack.py --per-subject 150 --out path.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tarfile
import tempfile
import urllib.request

MMLU_URL = "https://people.eecs.berkeley.edu/~hendrycks/data.tar"

# MCAT-relevant MMLU subjects -> broad MCAT topic. Covers Bio/Biochem,
# Gen Chem, Physics, and Psych/Soc (CARS has no open MCQ analog).
SUBJECT_TOPIC = {
    "college_biology": "biology",
    "high_school_biology": "biology",
    "anatomy": "biology",
    "medical_genetics": "biology",
    "virology": "biology",
    "clinical_knowledge": "biology",
    "college_medicine": "biology",
    "professional_medicine": "biology",
    "nutrition": "biochemistry",
    "college_chemistry": "general_chemistry",
    "high_school_chemistry": "general_chemistry",
    "college_physics": "physics",
    "high_school_physics": "physics",
    "conceptual_physics": "physics",
    "high_school_psychology": "psychology_sociology",
    "sociology": "psychology_sociology",
}

_LETTERS = "ABCD"


def ensure_data(cache_dir: str, data_dir: str | None) -> str:
    """Return a path to an extracted MMLU `data/` dir, downloading if needed."""
    if data_dir and os.path.isdir(os.path.join(data_dir, "test")):
        return data_dir
    os.makedirs(cache_dir, exist_ok=True)
    extracted = os.path.join(cache_dir, "data")
    if os.path.isdir(os.path.join(extracted, "test")):
        return extracted
    tar_path = os.path.join(cache_dir, "mmlu_data.tar")
    if not os.path.exists(tar_path):
        print(f"Downloading MMLU data from {MMLU_URL} ...")
        urllib.request.urlretrieve(MMLU_URL, tar_path)
    print("Extracting ...")
    with tarfile.open(tar_path) as tf:
        try:
            tf.extractall(cache_dir, filter="data")  # py3.12+
        except TypeError:
            tf.extractall(cache_dir)
    if not os.path.isdir(os.path.join(extracted, "test")):
        raise SystemExit(f"unexpected MMLU archive layout under {cache_dir}")
    return extracted


def load_subject(data_dir: str, subject: str, per_subject: int) -> list[dict]:
    path = os.path.join(data_dir, "test", f"{subject}_test.csv")
    if not os.path.exists(path):
        print(f"  (skip, missing) {subject}", file=sys.stderr)
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if len(row) < 6:
                continue
            stem = row[0].strip()
            options = [c.strip() for c in row[1:5]]
            letter = row[5].strip().upper()
            if letter not in _LETTERS or not stem or any(not o for o in options):
                continue
            out.append(
                {
                    "topic": SUBJECT_TOPIC[subject],
                    "stem": stem,
                    "options": options,
                    "correct_index": _LETTERS.index(letter),
                    "explanation": "",
                    "provenance": 1,
                }
            )
            if per_subject and len(out) >= per_subject:
                break
    return out


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", help="path to an already-extracted MMLU data/ dir")
    ap.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "speedrun_mmlu"))
    ap.add_argument("--per-subject", type=int, default=150, help="cap questions per subject (0 = all)")
    ap.add_argument("--out", default=os.path.join(here, "speedrun_mmlu_pack.json"))
    args = ap.parse_args()

    data_dir = ensure_data(args.cache, args.data_dir)

    questions: list[dict] = []
    for subject in SUBJECT_TOPIC:
        items = load_subject(data_dir, subject, args.per_subject)
        print(f"  {subject:24s} {len(items)}")
        questions.extend(items)

    pack = {
        "name": "Speedrun MMLU MCAT-relevant pack",
        "license": "MIT",
        "attribution": (
            "Questions from MMLU (Hendrycks et al., ICLR 2021), MIT License: "
            "https://github.com/hendrycks/test"
        ),
        "note": (
            "Open-licensed college-level science/medicine MCQs mapped to broad MCAT "
            "topics. provenance=1 (open_licensed). No card_tag: these seed the "
            "performance signal globally."
        ),
        "questions": questions,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(pack, handle, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {len(questions)} questions -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
