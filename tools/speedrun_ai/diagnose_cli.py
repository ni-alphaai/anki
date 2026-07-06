# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Stdin/stdout CLI around the diagnosis coach.

Reads a JSON object {"item": {...}, "signals": {...}, "cutoff": float?} from
stdin and writes the diagnosis dict as JSON to stdout. The desktop adapter
(qt/aqt/speedrun_ai.py) invokes this with the isolated venv's Python so the
Anki runtime needs no extra dependencies. Reuses the shared response cache, so
repeated calls are deterministic and free.
"""

from __future__ import annotations

import json
import sys

from speedrun_ai.coach import ABSTAIN_CUTOFF, diagnose
from speedrun_ai.taxonomy import Signals


def main() -> int:
    data = json.load(sys.stdin)
    item = data.get("item", {})
    s = data.get("signals", {})
    sig = Signals(
        correct=False,
        took_ms=int(s.get("took_ms", 6000)),
        question_type=int(s.get("question_type", 1)),
        confidence=float(s.get("confidence", 0.0)),
        self_explanation=str(s.get("self_explanation", "")),
        recall_failed=bool(s.get("recall_failed", False)),
        passage_evidence_missed=bool(s.get("passage_evidence_missed", False)),
    )
    out = diagnose(item, sig, cutoff=float(data.get("cutoff", ABSTAIN_CUTOFF)))
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
