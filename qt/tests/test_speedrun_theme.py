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
        # honest empty gauge label (no duplicate "not enough evidence" line)
        assert "No score yet" in html
        # the engine reason is surfaced with its redundant prefix stripped
        assert "Need graded attempts 0/30" in html
        assert "not enough evidence" not in html
        # weakest-dimension shown as a compact chip ("memory" != "none")
        assert "Weakest: memory" in html


class TestResolved:
    def test_light_mode_tokens(self) -> None:
        assert theme.resolved("accent", night=False) == "#CC785C"
        assert theme.resolved("memory", night=False) == "#2E7BF6"

    def test_dark_mode_tokens(self) -> None:
        assert theme.resolved("accent", night=True) == "#D98A6B"

    def test_unknown_key_falls_back(self) -> None:
        # unknown keys fall back to the mode's accent
        assert theme.resolved("does-not-exist", night=False) == "#CC785C"


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
        assert "No score yet" in html
        assert "sr-gauge-empty" in html
        # the honest state carries no live signal arcs
        assert "var(--sr-memory)" not in html


class TestBridge:
    """The recall -> performance bridge must never present a near-zero recall as
    "strong transfer" (the original bug: 0% recall vs 21% performance rendered
    "-21 pts / strong transfer", which is nonsense)."""

    def _data(self, memory: float, performance: float) -> dict:
        return {
            "memory": memory,
            "performance": performance,
            "gap": memory - performance,
            "memory_ok": True,
            "perf_ok": True,
        }

    def test_zero_recall_is_not_strong_transfer(self) -> None:
        html = theme._bridge(self._data(0.0, 0.21))
        assert "strong transfer" not in html
        assert "-21 pts" not in html
        assert "building recall" in html

    def test_real_negative_gap_still_reads_as_transfer(self) -> None:
        # Recall well above the floor: a genuine transfer story is fine.
        html = theme._bridge(self._data(0.55, 0.75))
        assert "strong transfer" in html
        assert "-20 pts" in html

    def test_recall_ahead_of_performance_flags_the_gap(self) -> None:
        html = theme._bridge(self._data(0.80, 0.55))
        assert "+25 pts" in html
        assert "bridge to close" in html


class TestPracticeBody:
    """The Practice screen is MCAT-section organized, not one flat random list."""

    LANDING = {
        "mode": "landing",
        "total": 100,
        "sections": [
            {
                "key": "chem_phys",
                "short": "Chem/Phys",
                "full": "Chem & Phys",
                "subjects": ["general_chemistry", "physics"],
                "count": 30,
            },
            {
                "key": "cars",
                "short": "CARS",
                "full": "Reasoning",
                "subjects": ["cars"],
                "reasoning": True,
                "count": 0,
            },
            {
                "key": "bio_biochem",
                "short": "Bio/Biochem",
                "full": "Bio & Biochem",
                "subjects": ["biology", "biochemistry"],
                "count": 70,
            },
            {
                "key": "psych_soc",
                "short": "Psych/Soc",
                "full": "Psych & Soc",
                "subjects": ["psychology_sociology"],
                "count": 0,
            },
        ],
    }

    def test_landing_shows_sections_and_counts(self) -> None:
        html = theme.practice_body(self.LANDING)
        assert "Mixed diagnostic" in html
        assert "speedrun:pr:go:" in html  # the quick-start CTA
        assert "Chem/Phys" in html and "30 questions" in html
        assert "speedrun:pr:sec:bio_biochem" in html
        # CARS is a reasoning section with no imported bank yet -> it prompts to
        # import a pack rather than exposing a drill link.
        assert "speedrun:pr:sec:cars" not in html
        # a section with 0 questions routes to the library, not a drill
        assert "No questions — import a pack" in html

    def test_empty_landing_prompts_import(self) -> None:
        html = theme.practice_body({"mode": "landing", "total": 0, "sections": []})
        assert "No practice questions yet" in html
        assert "speedrun:library" in html

    def test_section_drilldown_lists_subjects(self) -> None:
        section = {
            "mode": "section",
            "section": {
                "key": "bio_biochem",
                "short": "Bio/Biochem",
                "full": "Bio & Biochem",
            },
            "count": 70,
            "subjects": [
                {"subject": "biology", "label": "Biology", "count": 50},
                {"subject": "biochemistry", "label": "Biochemistry", "count": 20},
            ],
        }
        html = theme.practice_body(section)
        assert "Practice whole section" in html
        assert "speedrun:pr:go:biology,biochemistry" in html
        assert "speedrun:pr:go:biology'" in html  # single-subject start
        assert "Biology" in html and "50 questions" in html
        assert "speedrun:pr:home" in html  # back to sections


class TestPracticeVoice:
    """The in-webview practice card must offer voice self-explanation everywhere a
    student can explain their reasoning, degrading to text-only when on-device
    transcription isn't available."""

    def _runner(self, voice: bool) -> dict:
        return {
            "empty": False,
            "mode": "runner",
            "index": 0,
            "total": 3,
            "answered": False,
            "selected": None,
            "q": {
                "topic": "biochemistry",
                "stem": "Which residue is most likely protonated at pH 7?",
                "options": ["Glycine", "Histidine", "Serine"],
                "correct_index": 1,
            },
            "verdict": None,
            "verdict_text": "",
            "feedback": "",
            "ai": None,
            "is_last": False,
            "voice": voice,
        }

    def test_shows_mic_when_voice_available(self) -> None:
        html = theme.practice_body(self._runner(True))
        # the mic affordance, its command, and the on-device privacy note
        assert 'id="sr-pq-voice"' in html
        assert "Speak your reasoning" in html
        assert "srVoice()" in html
        assert "Captured on-device" in html
        # text fallback is always kept alongside the mic
        assert 'id="sr-explain"' in html

    def test_text_only_when_voice_unavailable(self) -> None:
        html = theme.practice_body(self._runner(False))
        # no mic button, but the textarea (text fallback) still renders
        assert "sr-pq-voice" not in html
        assert "Speak your reasoning" not in html
        assert 'id="sr-explain"' in html


class TestDiagnosticBody:
    """The first-run placement diagnostic: an honest intro + a per-section read
    that abstains on readiness until the evidence gate is met."""

    REPORT = {
        "overall": {"correct": 9, "total": 15, "pct": 60},
        "sections": [
            {
                "short": "Chem/Phys",
                "full": "Chem & Phys",
                "correct": 3,
                "total": 5,
                "pct": 60,
            },
            {
                "short": "Bio/Biochem",
                "full": "Bio",
                "correct": 4,
                "total": 5,
                "pct": 80,
            },
            {
                "short": "Psych/Soc",
                "full": "Psych",
                "correct": 2,
                "total": 5,
                "pct": 40,
            },
        ],
    }

    def test_intro_offers_start_and_skip(self) -> None:
        html = theme.diagnostic_intro_body(15)
        assert "Quick placement check" in html
        assert "15 questions" in html
        assert "speedrun:diag:start" in html
        assert "speedrun:diag:skip" in html

    def test_report_abstains_honestly(self) -> None:
        data = dict(self.REPORT)
        data["readiness"] = {
            "sufficient": False,
            "reason": "not enough evidence: need graded attempts 9/30",
        }
        html = theme.diagnostic_report_body(data)
        assert "Your placement read" in html
        assert "9 / 15 correct (60%)" in html
        assert "Chem/Phys" in html and "Psych/Soc" in html
        assert "building evidence" in html
        assert "not enough evidence: need graded attempts 9/30" in html
        assert "speedrun:dashboard" in html  # Go to dashboard

    def test_report_shows_score_when_sufficient(self) -> None:
        data = dict(self.REPORT)
        data["readiness"] = {"sufficient": True, "scaled": 512, "low": 505, "high": 520}
        html = theme.diagnostic_report_body(data)
        assert "Initial readiness: 512" in html
        assert "505" in html and "520" in html


class TestSyncPairBody:
    """The Sync-with-phone screen: one primary action, and the directional
    "use phone / use desktop" choice surfaces only on a real conflict (via a
    prompt in speedrun.py), never as always-visible buttons. The persisted
    on-conflict preference shows as a segmented control while hosting."""

    def _running(self, policy: str = "ask") -> dict:
        return {
            "running": True,
            "conflict_policy": policy,
            "qr_svg": "<svg></svg>",
            "url": "http://192.168.1.5:27701",
            "usb_url": "http://127.0.0.1:27701/",
            "user": "speedrun",
            "token": "abc123",
            "usb_ready": True,
            "usb_status": "USB ready",
            "exp": 1234567890,
        }

    def test_single_primary_action_no_directional_buttons(self) -> None:
        html = theme.sync_pair_body(self._running())
        assert "speedrun:syncnow" in html
        assert "Sync now" in html
        # The clutter the phone removed must be gone on the default screen.
        assert "Use phone data" not in html
        assert "Use desktop data" not in html
        assert "speedrun:syncpull" not in html
        assert "speedrun:syncpush" not in html

    def test_shows_on_conflict_selector_with_all_options(self) -> None:
        html = theme.sync_pair_body(self._running("ask"))
        assert "On conflict" in html
        assert "speedrun:syncpolicy:ask" in html
        assert "speedrun:syncpolicy:phone" in html
        assert "speedrun:syncpolicy:desktop" in html
        # the apostrophe is HTML-escaped in the rendered caption
        assert "be asked which copy to keep." in html

    def test_selector_marks_the_active_preference(self) -> None:
        html = theme.sync_pair_body(self._running("phone"))
        # exactly one option is active, and it is the saved one
        assert html.count("sr-seg-btn active") == 1
        assert (
            'class="sr-seg-btn active" onclick="pycmd(\'speedrun:syncpolicy:phone\')"'
            in html
        )
        assert "Auto-keeps the phone; overwrites the desktop copy." in html

    def test_not_hosting_state_has_no_selector(self) -> None:
        html = theme.sync_pair_body({"running": False, "conflict_policy": "ask"})
        assert "Start" in html
        assert "On conflict" not in html
        assert "speedrun:syncpolicy" not in html

    def test_ankiweb_is_the_primary_sync_option(self) -> None:
        html = theme.sync_pair_body(self._running())
        # AnkiWeb leads...
        assert "Sync with AnkiWeb (recommended)" in html
        # ...and phone pairing is demoted into a collapsible section below it.
        assert "Sync with phone (offline" in html
        assert "<details" in html
        assert html.index("Sync with AnkiWeb (recommended)") < html.index("<details")

    def test_ankiweb_sign_in_and_sync_are_distinct(self) -> None:
        # Signed out: a Sign-in action, no Sync/Sign-out.
        out = theme.sync_pair_body(self._running())
        assert "Not signed in yet" in out
        assert "speedrun:ankiwebsignin" in out
        assert "Sign in to AnkiWeb" in out
        assert "speedrun:syncankiweb" not in out
        assert "speedrun:ankiwebsignout" not in out
        # Signed in: Sync + Sign-out, no Sign-in (session persists).
        signed = self._running()
        signed["ankiweb_signed_in"] = True
        html = theme.sync_pair_body(signed)
        assert "stay signed in" in html
        assert "speedrun:syncankiweb" in html and "Sync now" in html
        assert "speedrun:ankiwebsignout" in html and "Sign out" in html
        assert "speedrun:ankiwebsignin" not in html


class TestLibraryBody:
    def test_library_offers_the_content_library(self) -> None:
        html = theme.library_body("2 decks · 186 cards", [])
        assert "Open-licensed MCAT library" in html
        assert "speedrun:lib:content" in html

    def test_library_offers_the_sample_history_seeder(self) -> None:
        html = theme.library_body("2 decks · 186 cards", [])
        assert "Load sample study history" in html
        assert "speedrun:lib:sample" in html


class TestProgressBody:
    """The Progress screen renders diverse charts from a plain dict."""

    PROG = {
        "memory": 1.0,
        "performance": 0.66,
        "coverage": 1.0,
        "memory_ok": True,
        "perf_ok": True,
        "cov_total": 31,
        "cov_covered": 28,
        "weighted": 0.9,
        "topics": [
            {"label": "1A Proteins", "covered": True, "weight": 3.0},
            {"label": "2B Viruses", "covered": False, "weight": 2.5},
        ],
        "calibration": {
            "sufficient": True,
            "n": 40,
            "brier": 0.13,
            "logloss": 0.41,
            "bins": [{"mean_predicted": 0.5, "mean_outcome": 0.52, "count": 10}],
        },
        "feedback": {
            "total": 30,
            "correct": 20,
            "memory": 2,
            "reasoning": 5,
            "passage": 2,
            "test_taking": 1,
            "weak_topics": ["1D Bioenergetics"],
        },
    }

    def test_renders_all_chart_sections(self) -> None:
        html = theme.progress_body(self.PROG)
        assert "Progress" in html
        assert "reliability curve" in html.lower()
        assert "<svg" in html  # the calibration scatter
        assert "Misses by cause" in html
        assert "Reasoning" in html and ">5<" in html  # a miss-count bar
        assert "Coverage map" in html
        assert "28/31" in html  # covered/total
        assert "1D Bioenergetics" in html  # weak-topic chip
        assert "0.130" in html  # brier

    def test_empty_calibration_is_honest(self) -> None:
        data = dict(self.PROG)
        data["calibration"] = {"sufficient": False, "n": 0, "bins": []}
        html = theme.progress_body(data)
        assert "No graded predictions yet" in html

    def test_calibration_graph_is_labeled_and_readable(self) -> None:
        html = theme.progress_body(self.PROG)
        # numbered, titled axes
        assert "Predicted confidence (%)" in html
        assert "Actual accuracy (%)" in html
        assert ">100<" in html  # a 0/50/100 axis tick label
        # the diagonal is labeled and the caption explains above/below the line
        assert "perfectly calibrated" in html
        assert "under-confident" in html
        assert "over-confident" in html

    def test_misses_count_never_wraps(self) -> None:
        html = theme.progress_body(self.PROG)
        # two-digit miss counts must not wrap onto two lines
        assert "white-space:nowrap" in html
        assert "min-width:32px" in html


class TestTopicDashboard:
    DASH = {
        "has_topics": True,
        "sections": [
            {
                "key": "bio_biochem",
                "short": "Bio/Biochem",
                "full": "Biological & Biochemical Foundations",
                "disabled": False,
                "total": 1,
                "covered": 1,
                "coverage": 1.0,
                "memory": 0.8,
                "performance": 0.7,
                "topics": [
                    {
                        "id": "1A",
                        "name": "Proteins",
                        "section": "Bio/Biochem",
                        "subject": "biochemistry",
                        "weight": 5,
                        "cards": 18,
                        "covered": True,
                        "review": 16,
                        "mature": 14,
                        "attempts": 12,
                        "correct": 10,
                        "memory": 0.875,
                        "performance": 0.833,
                        "status": "Strong",
                        "kind": "perf",
                    }
                ],
            },
            {
                "key": "cars",
                "short": "CARS",
                "full": "Critical Analysis & Reasoning Skills",
                "reasoning": True,
                "disabled": True,
                "total": 0,
                "covered": 0,
                "coverage": 0.0,
                "memory": None,
                "performance": None,
                "topics": [],
            },
        ],
    }

    def test_dashboard_shows_collapsed_section_cards(self) -> None:
        html = theme.topic_dashboard_html(self.DASH)
        assert "MCAT topics" in html
        assert "Bio/Biochem" in html  # section card
        assert "speedrun:section:bio_biochem" in html  # tap drills into the section
        assert "Memory N/A" in html  # CARS is a reasoning section: memory is N/A
        # subtopics are NOT inlined on the dashboard (they live in the drill-in)
        assert "speedrun:topic:1A" not in html

    def test_section_detail_lists_subtopics(self) -> None:
        html = theme.section_detail_body(self.DASH["sections"][0])
        assert "Bio/Biochem" in html
        assert "Proteins" in html
        assert "speedrun:topic:1A" in html

    def test_dashboard_empty_when_no_topics(self) -> None:
        assert theme.topic_dashboard_html({"has_topics": False, "sections": []}) == ""

    def test_decks_body_shows_group_banner_when_ungrouped(self) -> None:
        html = theme.decks_topic_body(self.DASH, 127)
        assert "Decks by MCAT topic" in html
        assert "127 cards not sorted" in html
        assert "speedrun:group" in html
        assert "speedrun:section:bio_biochem" in html  # section cards, not raw rows

    def test_decks_body_hides_banner_when_all_grouped(self) -> None:
        html = theme.decks_topic_body(self.DASH, 0)
        assert "not sorted into MCAT topics" not in html

    def test_topic_detail_shows_signals_when_present(self) -> None:
        html = theme.topic_detail_body(self.DASH["sections"][0]["topics"][0])
        assert "88%" in html  # memory 0.875
        assert "83%" in html  # performance 0.833
        assert "14 mature of 16 review cards" in html
        # both paths from a topic: review the cards + practice questions
        assert "speedrun:topic:review:1A" in html
        assert "Review memory cards" in html
        assert "speedrun:topic:practice:1A" in html
        assert "Practice questions" in html

    def test_topic_detail_honest_empty_states(self) -> None:
        topic = {
            "id": "5E",
            "name": "Thermodynamics",
            "section": "Chem/Phys",
            "subject": "general_chemistry",
            "weight": 2,
            "cards": 8,
            "covered": True,
            "review": 0,
            "mature": 0,
            "attempts": 0,
            "correct": 0,
            "memory": None,
            "performance": None,
            "status": "Not started",
            "kind": "muted",
        }
        html = theme.topic_detail_body(topic)
        assert "No review cards yet" in html
        assert "No questions answered yet" in html


def _card_inner(html: str) -> str:
    """Return the inner HTML of the single ``.sr-card`` element, walking div
    nesting to find its matching close tag. Used to assert that content is
    actually *contained* by the card element, not merely present on the page."""
    open_tag = '<div class="sr-card">'
    start = html.index(open_tag)
    i = start + len(open_tag)
    depth = 1
    while depth:
        nxt_open = html.find("<div", i)
        nxt_close = html.find("</div>", i)
        assert nxt_close != -1, "unbalanced .sr-card"
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open + 4
        else:
            depth -= 1
            i = nxt_close + 6
    return html[start + len(open_tag) : i - len("</div>")]


class TestDeckListBody:
    """The all-decks list must line the New/Learn/Due counts up under their
    column headers. The header is a ``<div>`` and each row is a ``<button>``;
    without a shared box model the button's padding lands outside its width and
    the flexible name column pushes the counts a column to the right."""

    DATA = {
        "studied": "Your decks, by New / Learn / Due - tap to study.",
        "decks": [
            {
                "id": 1,
                "name": "MCAT Content Library",
                "new": 20,
                "learn": 0,
                "review": 0,
            },
            {
                "id": 2,
                "name": "MileDown's MCAT Decks",
                "new": 20,
                "learn": 0,
                "review": 6,
            },
        ],
    }

    def test_header_and_rows_share_identical_grid_geometry(self) -> None:
        html = theme.deck_list_body(self.DATA)
        # header + one <button> row per deck, all four-column grids
        assert '<div class="sr-deck-head">' in html
        assert html.count('class="sr-deck-row"') == 2
        # the shared rule defines the columns once for BOTH selectors, and pins
        # the box model so the <div> head and <button> rows measure identically
        rule = theme._WORKSPACE_CSS.split(".sr-deck-head, .sr-deck-row {")[1].split(
            "}"
        )[0]
        assert "grid-template-columns:1fr 52px 52px 52px" in rule
        assert "box-sizing:border-box" in rule
        assert "width:100%" in rule
        assert "margin:0" in rule

    def test_header_and_row_have_matching_cell_counts(self) -> None:
        html = theme.deck_list_body(self.DATA)
        # header: Deck + New + Learn + Due = 4 cells
        head = html.split('<div class="sr-deck-head">')[1].split("</div>")[0]
        assert head.count("<span") == 4
        # each row: one name cell + three count cells = 4 cells, matching the
        # four grid tracks so a count sits under each header
        row = html.split('class="sr-deck-row"')[1].split("</button>")[0]
        assert 'class="sr-deck-name"' in row
        assert row.count('class="sr-deck-c') == 3

    def test_name_cell_can_shrink_so_it_never_blows_out_the_track(self) -> None:
        # min-width:0 lets the 1fr name track ellipsize instead of forcing the
        # fixed count columns rightward on a long deck name.
        assert (
            "min-width:0"
            in theme._WORKSPACE_CSS.split(".sr-deck-name {")[1].split("}")[0]
        )


class TestPracticeCarsContainment:
    """A CARS/passage question renders the passage, stem, options, confidence and
    self-explain inside ONE ``.sr-card``. The scrollable passage must not share
    the card's own elevated fill, or the card's boundary visually collapses onto
    the passage and the stem below reads as if it spilled onto the page."""

    def _cars(self, answered: bool = False) -> dict:
        return {
            "empty": False,
            "mode": "runner",
            "index": 0,
            "total": 9,
            "answered": answered,
            "selected": (1 if answered else None),
            "q": {
                "topic": "cars",
                "passage_title": "A Vindication of the Rights of Woman (1792)",
                "passage": "First paragraph of the passage.\n\nSecond paragraph.",
                "stem": "The author's argument depends most on which assumption?",
                "options": [
                    "Roles are identical",
                    "Reason grounds dignity",
                    "Strength",
                    "Sentiment",
                ],
                "correct_index": 1,
                "explanation": "Reason is the stated basis of dignity.",
            },
            "verdict": ("good" if answered else None),
            "verdict_text": ("Correct" if answered else ""),
            "feedback": ("Answer: B" if answered else ""),
            "ai": None,
            "is_last": False,
            "voice": True,
        }

    def test_whole_question_is_inside_one_card(self) -> None:
        html = theme.practice_body(self._cars())
        assert html.count('class="sr-card"') == 1
        inner = _card_inner(html)
        # passage + stem + every option + confidence + self-explain live in the card
        assert "sr-pq-passage" in inner
        assert "sr-pq-stem" in inner
        # one lettered option button per choice (the JS also mentions .sr-pq-opt,
        # so count the per-option letter badge instead)
        assert inner.count("sr-pq-letter") == 4
        assert 'id="sr-conf-row"' in inner
        assert 'id="sr-explain"' in inner

    def test_self_explain_and_confidence_are_mandatory(self) -> None:
        html = theme.practice_body(self._cars())
        # No opt-outs: confidence is a forced Low/Medium/High (no "(skip)") and
        # self-explain is no longer "(optional)".
        assert "(skip)" not in html
        assert "(optional)" not in html
        assert 'data-conf="1"' in html and 'data-conf="3"' in html
        # The textarea drives the progressive reveal + submit gate, and submit
        # starts disabled until answer + explanation + confidence are all present.
        assert 'oninput="srUpdate()"' in html
        assert 'disabled onclick="srSubmit()"' in html

    def test_passage_is_recessed_not_the_card_fill(self) -> None:
        passage_rule = theme._WORKSPACE_CSS.split(".sr-pq-passage {")[1].split("}")[0]
        # recessed page surface, never the card's own elevated fill
        assert "background:var(--sr-canvas)" in passage_rule
        assert "var(--sr-elevated)" not in passage_rule


class TestPracticeQuit:
    """An in-progress practice session offers an unobtrusive Quit control so a
    student can leave without answering every question."""

    def _runner(self, answered: bool) -> dict:
        return {
            "empty": False,
            "mode": "runner",
            "index": 1,
            "total": 5,
            "answered": answered,
            "selected": (0 if answered else None),
            "q": {
                "topic": "biochemistry",
                "stem": "Which residue is most likely protonated at pH 7?",
                "options": ["Glycine", "Histidine", "Serine"],
                "correct_index": 1,
                "explanation": "Histidine's imidazole pKa is near 6.",
            },
            "verdict": ("bad" if answered else None),
            "verdict_text": ("Not quite" if answered else ""),
            "feedback": ("Answer: B" if answered else ""),
            "ai": None,
            "is_last": False,
            "voice": False,
        }

    def test_quit_present_and_wired_in_both_states(self) -> None:
        for answered in (False, True):
            html = theme.practice_body(self._runner(answered))
            assert "speedrun:pq:quit" in html
            assert ">Quit<" in html
            # the control sits inside the question card, next to the progress
            inner = _card_inner(html)
            assert "speedrun:pq:quit" in inner
            assert "sr-pq-head" in inner
