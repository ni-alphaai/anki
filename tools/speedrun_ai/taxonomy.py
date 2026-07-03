# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Failure-mode taxonomy + the deterministic / keyword baselines.

The rubric below is the *named source* the AI coach grounds its diagnosis in.
Kinds and routed actions mirror `rslib/src/speedrun/mod.rs` exactly, so the AI
layer stays interchangeable with the engine's deterministic classifier.
"""

from __future__ import annotations

from dataclasses import dataclass

# Diagnosis kinds (mirror rslib/src/speedrun/mod.rs).
MEMORY, REASONING, PASSAGE, TEST_TAKING, CORRECT = 1, 2, 3, 4, 5
KIND_NAME = {
    MEMORY: "memory",
    REASONING: "reasoning",
    PASSAGE: "passage",
    TEST_TAKING: "test_taking",
    CORRECT: "correct",
}
NAME_KIND = {v: k for k, v in KIND_NAME.items()}

# Routed actions (mirror rslib/src/speedrun/mod.rs).
ACTION_NONE, ACTION_RESURFACE, ACTION_PASSAGE, ACTION_STRATEGY, ACTION_ADVANCE = (
    0,
    1,
    2,
    3,
    4,
)
DEFAULT_ACTION = {
    MEMORY: ACTION_RESURFACE,
    REASONING: ACTION_PASSAGE,
    PASSAGE: ACTION_PASSAGE,
    TEST_TAKING: ACTION_STRATEGY,
    CORRECT: ACTION_ADVANCE,
}

# A confident, fast wrong answer on an exam-style item reads as careless
# (test-taking), not a missing concept (mirrors mod.rs TEST_TAKING_CONFIDENCE).
TEST_TAKING_CONFIDENCE = 0.75

# The rubric the coach must classify against, and cite as its source.
RUBRIC = {
    "memory": (
        "The student could not retrieve the underlying fact/definition; the "
        "chosen option reflects not knowing the fact, not misapplying it. "
        "Repair: resurface via spaced repetition."
    ),
    "reasoning": (
        "The student knew the relevant facts but applied them incorrectly (a "
        "logic/application error, often a classic distractor trap). Repair: "
        "concept-linked application practice."
    ),
    "passage": (
        "The student missed, misread, or ignored evidence given in the "
        "passage/figure/data. Repair: passage-comprehension practice."
    ),
    "test_taking": (
        "The student very likely knew the concept but answered "
        "carelessly/rushed (fast + high confidence, an avoidable slip). "
        "Repair: test-taking strategy."
    ),
}
RUBRIC_SOURCE = (
    "Speedrun failure-mode rubric v1 (rslib/src/speedrun/mod.rs; "
    "project_brainlift.md diagnostic taxonomy)"
)


@dataclass
class Signals:
    """Behavioural signals available at diagnosis time."""

    correct: bool = False
    took_ms: int = 6000
    # 0 = srs review, 1 = passage mcq, 2 = discrete mcq.
    question_type: int = 1
    # Self-reported / model-predicted pre-answer confidence, 0..1 (0 = unknown).
    confidence: float = 0.0
    # Known for SRS reviews (the student pressed "Again"); usually unknown for
    # exam-style MCQs, which is exactly where the content-aware coach earns its keep.
    recall_failed: bool = False
    passage_evidence_missed: bool = False


def deterministic_classify(s: Signals) -> int:
    """Port of `classify` in rslib/src/speedrun/mod.rs — the AI-off baseline.

    Signals only: no access to the item's text or the chosen distractor.
    """
    if s.correct:
        return CORRECT
    if s.recall_failed:
        return MEMORY
    if s.passage_evidence_missed:
        return PASSAGE
    if (
        s.question_type != 0
        and 0 < s.took_ms < 8000
        and s.confidence >= TEST_TAKING_CONFIDENCE
    ):
        return TEST_TAKING
    return REASONING


_PASSAGE_KW = (
    "passage",
    "figure",
    "graph",
    "table",
    "the data",
    "experiment",
    "the author",
    "according to",
    "results show",
    "the study",
)
_MEMORY_KW = (
    "recall",
    "memoriz",
    "definition",
    "defined as",
    "the term",
    "vocabulary",
    "name the",
    "which of the following is the",
)


def keyword_classify(item: dict, s: Signals) -> int:
    """A deliberately simple keyword+signal baseline (the 'simpler method' to beat)."""
    if s.correct:
        return CORRECT
    if (
        s.question_type != 0
        and 0 < s.took_ms < 8000
        and s.confidence >= TEST_TAKING_CONFIDENCE
    ):
        return TEST_TAKING
    text = " ".join(
        [
            item.get("stem", ""),
            item.get("explanation", ""),
            " ".join(item.get("options", [])),
        ]
    ).lower()
    if any(kw in text for kw in _PASSAGE_KW):
        return PASSAGE
    if any(kw in text for kw in _MEMORY_KW):
        return MEMORY
    return REASONING
