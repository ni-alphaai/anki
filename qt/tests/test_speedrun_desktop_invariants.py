# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Code-level invariant tests for the in-place Speedrun desktop integration.

These assert the two design guarantees of the desktop reskin without a live GUI:

1. The deck-browser home carries **no** injected readiness banner (its white
   block clashed with the deck list, so the banner was removed).
2. The Speedrun **Dashboard** toolbar link is inserted at the FRONT of the link
   list, so it renders leftmost among the primary destinations.

They import ``aqt.speedrun`` (which pulls in Qt) but never construct a
QApplication, so they run headlessly like ``test_mediasrv.py``. ``aqt.mw`` and
``toolbar.create_link`` are stubbed minimally.
"""

from __future__ import annotations

import inspect

from aqt import speedrun

# --- fakes ------------------------------------------------------------------


class _FakeBackend:
    """Snapshot lookup fails in tests; ``_on_toolbar_links`` swallows it and
    falls back to the default tooltip, which is fine for this invariant."""

    def get_readiness_snapshot(self):
        raise RuntimeError("no backend in a headless test")


class _FakeCol:
    def __init__(self) -> None:
        self._backend = _FakeBackend()


class _FakeMw:
    def __init__(self) -> None:
        self.col = _FakeCol()


class _FakeToolbar:
    """Records create_link calls and returns a distinguishable sentinel."""

    def create_link(self, cmd, label, func, tip=None, id=None):  # noqa: A002
        return {
            "cmd": cmd,
            "label": label,
            "tip": tip,
            "id": id,
            "sentinel": "dashboard-link",
        }


# --- invariant 1: no deck-browser banner ------------------------------------


class TestNoDeckBrowserBanner:
    def test_no_deck_browser_content_handler_exists(self) -> None:
        # The banner handler was deleted entirely; readiness now lives in the
        # per-deck overview panel and the toolbar Dashboard instead.
        assert not hasattr(speedrun, "_on_deck_browser_content")

    def test_setup_does_not_register_deck_browser_hook(self) -> None:
        # Robust complement: even if a differently-named handler were added,
        # setup() must not append anything to deck_browser_will_render_content.
        src = inspect.getsource(speedrun.setup)
        assert "deck_browser_will_render_content.append" not in src


# --- invariant 2: Dashboard is the leftmost toolbar link --------------------


class TestToolbarDashboardLeftmost:
    def test_dashboard_inserted_at_front(self, monkeypatch) -> None:
        import aqt

        monkeypatch.setattr(aqt, "mw", _FakeMw())

        # The default primary destinations, already in order when the hook fires.
        links = ["decks", "add", "browse", "stats", "sync"]
        speedrun._on_toolbar_links(links, _FakeToolbar())

        assert len(links) == 6
        assert links[0]["sentinel"] == "dashboard-link"
        assert links[0]["id"] == "speedrun"
        assert links[0]["label"] == "Dashboard"
        # The pre-existing links keep their relative order behind Dashboard.
        assert links[1:] == ["decks", "add", "browse", "stats", "sync"]

    def test_noop_without_collection(self, monkeypatch) -> None:
        import aqt

        class _NoColMw:
            col = None

        monkeypatch.setattr(aqt, "mw", _NoColMw())
        links: list = []
        speedrun._on_toolbar_links(links, _FakeToolbar())
        assert links == []


# --- next-action routing (pure dict-in/dict-out) ----------------------------


class TestNextActionRouting:
    """``speedrun._next_action`` maps a collected ``data`` dict to the single
    recommended step (title/detail/cmd/cta). Pure routing, no Qt/backend."""

    def test_abstain_memory_has_no_command(self) -> None:
        na = speedrun._next_action(
            {"sufficient": False, "blocking": "memory", "cov_total": 10}
        )
        assert na["title"] == "Study more cards"
        assert na["cmd"] is None

    def test_abstain_performance_routes_to_practice(self) -> None:
        na = speedrun._next_action(
            {"sufficient": False, "blocking": "performance", "cov_total": 10}
        )
        assert na["title"] == "Answer held-out questions"
        assert na["cmd"] == "speedrun:practice"

    def test_abstain_coverage_offers_seed_when_empty(self) -> None:
        na = speedrun._next_action(
            {"sufficient": False, "blocking": "coverage", "cov_total": 0}
        )
        assert na["cmd"] == "speedrun:seed"
        assert na["cta"] == "Seed topics"

    def test_sufficient_large_gap_bridges_to_application(self) -> None:
        na = speedrun._next_action({"sufficient": True, "gap": 0.2})
        assert na["title"] == "Bridge recall to application"
        assert na["cmd"] == "speedrun:practice"

    def test_sufficient_on_track(self) -> None:
        na = speedrun._next_action(
            {
                "sufficient": True,
                "gap": 0.0,
                "exam": {"has": True, "readiness_sufficient": True, "on_track": True},
            }
        )
        assert na["title"] == "On track \u2014 keep going"


# --- D1: reasoning-round top-up merge (pure) --------------------------------


class TestMergeQuestions:
    """``speedrun._merge_questions`` blends the session round with the engine's
    scheduled due-reasoning items, de-duping by (card_id, stem) and capping."""

    def test_dedupes_and_keeps_primary_first(self) -> None:
        primary = [{"card_id": 1, "stem": "a"}]
        extra = [
            {"card_id": 1, "stem": "a"},  # dup of primary -> dropped
            {"card_id": 2, "stem": "b"},
        ]
        merged = speedrun._merge_questions(primary, extra, 5)
        assert [(q["card_id"], q["stem"]) for q in merged] == [(1, "a"), (2, "b")]

    def test_respects_target_cap(self) -> None:
        primary = [{"card_id": 1, "stem": "a"}]
        extra = [{"card_id": 2, "stem": "b"}, {"card_id": 3, "stem": "c"}]
        merged = speedrun._merge_questions(primary, extra, 2)
        assert len(merged) == 2
        assert merged[0]["stem"] == "a"

    def test_empty_primary_uses_extra(self) -> None:
        merged = speedrun._merge_questions([], [{"card_id": 9, "stem": "z"}], 5)
        assert merged == [{"card_id": 9, "stem": "z"}]


# --- D2: feedback-report formatting (pure) ----------------------------------


class TestFeedbackLines:
    """``speedrun._feedback_lines`` renders the D2 report dict for display."""

    def test_empty_report(self) -> None:
        assert speedrun._feedback_lines({"total": 0}) == [
            "No exam-style attempts recorded yet."
        ]

    def test_counts_and_weak_topics(self) -> None:
        lines = speedrun._feedback_lines(
            {
                "total": 4,
                "correct": 2,
                "memory": 1,
                "reasoning": 1,
                "passage": 0,
                "test_taking": 0,
                "weak_topics": ["biology", "physics"],
            }
        )
        assert lines[0] == "Answered 4 exam-style question(s), 2 correct."
        assert any("Memory: 1" in ln and "Reasoning: 1" in ln for ln in lines)
        assert lines[-1] == "Weakest topics: biology, physics."

    def test_omits_empty_sections(self) -> None:
        lines = speedrun._feedback_lines({"total": 1, "correct": 1, "weak_topics": []})
        # all-correct, no weak topics: only the summary line
        assert lines == ["Answered 1 exam-style question(s), 1 correct."]


# --- D7: withhold-by-proficiency decision (pure) ----------------------------


class TestShouldWithholdFeedback:
    """``speedrun._should_withhold_feedback`` gates the delayed-feedback
    experiment: only when enabled AND the student is proficient."""

    def test_disabled_never_withholds(self) -> None:
        assert speedrun._should_withhold_feedback(1.0, False) is False
        assert speedrun._should_withhold_feedback(0.0, False) is False

    def test_enabled_withholds_only_for_proficient(self) -> None:
        assert speedrun._should_withhold_feedback(0.8, True) is True
        assert speedrun._should_withhold_feedback(0.95, True) is True
        assert speedrun._should_withhold_feedback(0.79, True) is False
        assert speedrun._should_withhold_feedback(0.0, True) is False
