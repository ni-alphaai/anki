# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for the coach's abstain / degrade / source contract.

These use the `diagnose(item, s, llm=...)` dependency-injection seam with a fake
LLM, so they need no network, no API key, and never import openai. Run offline:

    PYTHONPATH=tools out/ai-venv/bin/python -m pytest tools/speedrun_ai/test_coach.py -q
"""

from __future__ import annotations

from speedrun_ai.coach import diagnose
from speedrun_ai.taxonomy import RUBRIC_SOURCE, Signals


class _Fake:
    """A stand-in LLM whose complete_json(system, user) returns a canned dict or
    raises, exercising the coach's success and degrade paths without a network."""

    def __init__(self, resp=None, raise_exc=False):
        self._resp, self._raise = resp, raise_exc

    def complete_json(self, system, user):
        if self._raise:
            raise RuntimeError("AI unavailable")
        return self._resp


_ITEM = {
    "stem": "q",
    "options": ["a", "b"],
    "correct_index": 0,
    "selected_index": 1,
    "explanation": "because a",
}


def _sig() -> Signals:
    return Signals(
        correct=False,
        took_ms=9000,
        confidence=0.3,
        recall_failed=False,
        passage_evidence_missed=False,
    )


def test_abstains_below_cutoff():
    d = diagnose(_ITEM, _sig(), llm=_Fake({"kind": "memory", "confidence": 0.10}))
    assert d["abstained"] is True and d["error"] is False


def test_abstains_on_unknown_label():
    d = diagnose(_ITEM, _sig(), llm=_Fake({"kind": "nonsense", "confidence": 0.99}))
    assert d["abstained"] is True


def test_degrades_to_deterministic_on_error():
    d = diagnose(_ITEM, _sig(), llm=_Fake(raise_exc=True))
    assert (
        d["abstained"] is True and d["error"] is True and d["source"] == RUBRIC_SOURCE
    )


def test_source_attribution_present_on_success():
    d = diagnose(
        _ITEM,
        _sig(),
        llm=_Fake(
            {"kind": "memory", "confidence": 0.9, "source": "answer explanation"}
        ),
    )
    assert (
        d["abstained"] is False
        and d["kind_name"] == "memory"
        and d["source"] == "answer explanation"
    )


def _selftest() -> int:
    """Run every test_* in this module with plain asserts, so the contract is
    verifiable even when pytest is not installed in the (gitignored, regenerated)
    eval venv. Mirrors speedrun_ablation.py's self-test pattern.

    Run: PYTHONPATH=tools out/ai-venv/bin/python tools/speedrun_ai/test_coach.py
    """
    tests = sorted(
        (name, fn)
        for name, fn in globals().items()
        if name.startswith("test_") and callable(fn)
    )
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {name}: {e!r}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
