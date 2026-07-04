# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Stdin/stdout CLI that classifies flashcards into MCAT content categories.

Reads ``{"items": [{"id","text"}], "categories": [{"id","name","concept"}]}``
from stdin and writes ``{"assignments": {item_id: category_id}}`` to stdout.
Only ids that map to a known category are returned; anything the model is unsure
about is simply omitted, so the desktop side leaves those notes for the
deterministic keyword classifier / untagged. Reuses the shared LLM response
cache, so repeated runs are deterministic and free.
"""

from __future__ import annotations

import json
import sys

from speedrun_ai.llm import LLM

_BATCH = 25

_SYSTEM = (
    "You classify MCAT study flashcards into AAMC content categories. "
    "For each item, choose the single best category id from the provided list. "
    "Respond with ONLY a JSON object mapping each item id (as a string) to a "
    "category id. Omit an item entirely if none of the categories fit."
)


def _prompt(categories: list[dict], batch: list[dict]) -> str:
    cats = "\n".join(
        f"{c['id']}: {c.get('name', '')} — {c.get('concept', '')}".strip()
        for c in categories
    )
    items = "\n".join(f"{it['id']}: {it.get('text', '')}" for it in batch)
    return f"Categories:\n{cats}\n\nItems:\n{items}\n\nReturn JSON {{item_id: category_id}}."


def main() -> int:
    data = json.load(sys.stdin)
    items = data.get("items", [])
    categories = data.get("categories", [])
    valid = {str(c["id"]) for c in categories}

    llm = LLM()
    assignments: dict[str, str] = {}
    for i in range(0, len(items), _BATCH):
        batch = items[i : i + _BATCH]
        try:
            resp = llm.complete_json(_SYSTEM, _prompt(categories, batch))
        except Exception:
            resp = {}
        for item_id, cid in (resp or {}).items():
            if isinstance(cid, str) and cid in valid:
                assignments[str(item_id)] = cid

    json.dump({"assignments": assignments}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
