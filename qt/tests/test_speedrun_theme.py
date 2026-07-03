# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for ``aqt.speedrun_theme`` (the presentation-only Speedrun layer).

This module is Qt-free (it imports only json/math/html.escape), so it imports
and runs headlessly -- exactly like ``test_mediasrv.py``. The HTML builders take
a plain ``data`` dict and return strings, so they are pure functions we can
assert on directly.
"""

from __future__ import annotations

from aqt import speedrun_theme as theme

# A fully-populated readiness dict, the shape produced by ``speedrun._collect``.
# Every key the shared stack (hero -> signals -> bridge -> mini grid -> next
# action -> actions) reads is present so the panel/dashboard builders render.
SUFFICIENT: dict = {
    "sufficient": True,
    "readiness": 508,
    "low": 500,
    "high": 516,
    "memory": 0.8,
    "performance": 0.7,
    "coverage": 0.9,
    "gap": 0.1,
    "memory_ok": True,
    "perf_ok": True,
    "blocking": "none",
    "reason": "",
    "updated": "2026-07-03 12:00",
    "cov_total": 50,
    "cov_covered": 45,
    "calibration": {"sufficient": True, "n": 60, "brier": 0.12},
    "exam": {
        "has": True,
        "days_left": 42,
        "mode": "balanced",
        "readiness_sufficient": True,
        "on_track": True,
        "needed": 0,
        "per_week": 1.5,
    },
    "next_action": {
        "title": "On track \u2014 keep going",
        "detail": "Keep your spaced reviews steady.",
        "cmd": "speedrun:practice",
        "cta": "Practice",
    },
}

ABSTAIN: dict = {
    "sufficient": False,
    "readiness": 0,
    "low": 0,
    "high": 0,
    "memory": 0.2,
    "performance": 0.0,
    "coverage": 0.1,
    "gap": 0.0,
    "memory_ok": False,
    "perf_ok": False,
    "blocking": "memory",
    "reason": "not enough evidence: need graded attempts 0/30",
    "updated": "just now",
    "cov_total": 0,
    "cov_covered": 0,
    "calibration": None,
    "exam": None,
    "next_action": {
        "title": "Study more cards",
        "detail": "Readiness needs more graded reviews.",
        "cmd": None,
        "cta": None,
    },
}


class TestPct:
    def test_bounds(self) -> None:
        assert theme._pct(0.0) == 0
        assert theme._pct(1.0) == 100

    def test_midpoint(self) -> None:
        assert theme._pct(0.5) == 50

    def test_rounds(self) -> None:
        assert theme._pct(0.126) == 13

    def test_clamps_out_of_range(self) -> None:
        assert theme._pct(1.5) == 100
        assert theme._pct(-0.2) == 0


class TestBannerHtml:
    def test_sufficient_shows_projected_score(self) -> None:
        html = theme.banner_html(SUFFICIENT)
        assert "sr-banner" in html
        assert "508" in html
        assert "Projected MCAT readiness" in html
        # low-high range + the two headline signals.
        assert "500" in html and "516" in html
        assert "memory 80%" in html
        assert "performance 70%" in html
        assert "90% covered" in html

    def test_abstain_shows_honest_empty_state(self) -> None:
        html = theme.banner_html(ABSTAIN)
        assert "No score yet" in html
        assert "Readiness withheld" in html
        assert "sr-muted" in html
        # The engine reason is surfaced (escaped) in the banner meta.
        assert "not enough evidence: need graded attempts 0/30" in html


class TestPanelHtml:
    def test_sufficient_panel(self) -> None:
        html = theme.panel_html(SUFFICIENT)
        assert "sr-panel" in html
        assert "508" in html
        # legend from the hero card
        assert "Memory 80%" in html
        assert "Performance 70%" in html
        assert "Coverage 90%" in html
        # next-best-action card
        assert "Next best action" in html
        assert "On track \u2014 keep going" in html

    def test_abstain_panel(self) -> None:
        html = theme.panel_html(ABSTAIN)
        assert "sr-panel" in html
        # honest empty gauge label + surfaced reason
        assert "not enough evidence" in html
        # weakest-dimension block line ("memory" != "none")
        assert "Weakest dimension right now" in html


class TestDashboardHtml:
    def test_dashboard_has_header_and_stack(self) -> None:
        html = theme.dashboard_html(SUFFICIENT)
        assert "sr-dash-title" in html
        assert "Speedrun" in html
        # It reuses the same stack, so the readout is present too.
        assert "508" in html


class TestResolved:
    def test_light_mode_tokens(self) -> None:
        assert theme.resolved("accent", night=False) == "#2E7BF6"
        assert theme.resolved("memory", night=False) == "#2E7BF6"

    def test_dark_mode_tokens(self) -> None:
        assert theme.resolved("accent", night=True) == "#4B93FF"

    def test_unknown_key_falls_back(self) -> None:
        assert theme.resolved("does-not-exist", night=False) == "#2E7BF6"


class TestReadinessGauge:
    def test_sufficient_renders_live_arcs_and_readout(self) -> None:
        html = theme._readiness_gauge(SUFFICIENT)
        assert "sr-gauge" in html
        assert "508" in html
        assert "projected" in html
        # two distinct signal arcs (never one blended gradient)
        assert "var(--sr-memory)" in html
        assert "var(--sr-perf)" in html

    def test_abstain_renders_neutral_empty_state(self) -> None:
        html = theme._readiness_gauge(ABSTAIN)
        assert "sr-gauge" in html
        assert "not enough evidence" in html
        assert "sr-muted" in html
        # the honest state carries no live signal arcs
        assert "var(--sr-memory)" not in html
