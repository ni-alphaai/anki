# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""The source-grounded AI diagnosis coach.

Given a missed exam-style item (stem, options, the correct answer, the student's
chosen answer, and the answer explanation) plus behavioural signals, classify
the root-cause failure mode grounded in the explanation, and abstain when the
evidence is thin. On any error it returns an abstention so the caller falls back
to the deterministic classifier (the AI-off path).
"""

from __future__ import annotations

from .llm import LLM
from .taxonomy import (
    DEFAULT_ACTION,
    KIND_NAME,
    NAME_KIND,
    RUBRIC,
    RUBRIC_SOURCE,
    Signals,
)

ABSTAIN_CUTOFF = 0.55

SYSTEM = (
    "You are Speedrun's diagnostic coach for MCAT study. A student answered an "
    "exam-style question incorrectly. Classify the single ROOT-CAUSE failure mode "
    "using ONLY these definitions:\n"
    + "\n".join(f"- {k}: {v}" for k, v in RUBRIC.items())
    + "\nGround your reasoning in the provided answer explanation, which is the "
    "named source; refer to it. Explain the failure — do not simply restate the "
    "correct answer. If the evidence is insufficient to choose one mode "
    "confidently, set \"abstain\" to true.\n"
    'Respond with JSON only: {"kind": one of '
    '["memory","reasoning","passage","test_taking"], "confidence": number 0..1, '
    '"rationale": short string grounded in the explanation, "source": short '
    'citation of what grounded the call, "abstain": boolean}.'
)


def _user(item: dict, s: Signals) -> str:
    opts = item.get("options", [])
    sel = item.get("selected_index")
    ci = item.get("correct_index")
    lines = [
        f"Topic: {item.get('topic', '?')}",
        f"Question: {item.get('stem', '')}",
    ]
    for i, o in enumerate(opts):
        tag = ""
        if i == ci:
            tag += " [correct answer]"
        if sel is not None and i == sel:
            tag += " [student chose this]"
        lines.append(f"  ({chr(65 + i)}) {o}{tag}")
    lines.append(f"Answer explanation (named source): {item.get('explanation', '')}")
    lines.append(
        f"Behaviour: took_ms={s.took_ms}, self_confidence={s.confidence:.2f}, "
        f"question_type={s.question_type}"
    )
    return "\n".join(lines)


def diagnose(
    item: dict,
    s: Signals,
    llm: LLM | None = None,
    cutoff: float = ABSTAIN_CUTOFF,
) -> dict:
    """Return the coach's diagnosis dict, or an abstention on low confidence/error."""
    llm = llm or LLM()
    try:
        out = llm.complete_json(SYSTEM, _user(item, s))
    except Exception as e:  # AI-off / transport error -> abstain, caller falls back
        return {
            "kind": None,
            "kind_name": None,
            "confidence": 0.0,
            "routed_action": 0,
            "rationale": f"AI unavailable: {e}",
            "source": RUBRIC_SOURCE,
            "abstained": True,
            "error": True,
        }
    name = str(out.get("kind", "")).lower().strip()
    kind = NAME_KIND.get(name)
    try:
        conf = float(out.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    abstained = bool(out.get("abstain", False)) or kind is None or conf < cutoff
    return {
        "kind": kind,
        "kind_name": KIND_NAME.get(kind) if kind else None,
        "confidence": conf,
        "routed_action": DEFAULT_ACTION.get(kind, 0) if kind else 0,
        "rationale": out.get("rationale", ""),
        "source": out.get("source", RUBRIC_SOURCE),
        "abstained": abstained,
        "error": False,
    }
