# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun desktop integration.

Speedrun is redesigned *in place* within Anki's native screens rather than as a
separate app (see project_brainlift.md, "Session 3: Interface, Discoverability,
and Trust"):

- an opt-in Apple-style reskin of Anki's deck browser / overview / toolbar,
  following the maintainer-endorsed Anki 3.0 direction (gated by the
  ``speedrunModernUi`` config toggle; the reviewer card is left untouched),
- Speedrun's three signals + routed next-action embedded where the student
  already studies: a compact readiness banner on the deck-list home and a full
  readiness panel on the per-deck overview (rendered by ``speedrun_theme``),
- a top-toolbar entry showing the cached readiness at a glance,
- a pre-reveal self-explanation captured by voice (on-device faster-whisper) or
  text, plus a quiet post-miss diagnosis in the reviewer.

Everything runs through the existing protobuf ``SpeedrunService`` boundary; there
is no AI in this path (voice transcription is on-device and never transmitted).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, TypeVar

import aqt
from anki import speedrun_pb2
from anki.cards import Card
from aqt import gui_hooks
from aqt import speedrun_ai as srai
from aqt import speedrun_library as library
from aqt import speedrun_sync as srsync
from aqt import speedrun_theme as theme
from aqt import speedrun_voice as voice
from aqt.qt import (
    QAction,
    QButtonGroup,
    QComboBox,
    QDate,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QObject,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextCursor,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
    qconnect,
)
from aqt.utils import disable_help_button, tooltip, tr

# Config keys -- camelCase matches BoolKey serialization in rslib/config/bool.rs.
_CFG_POINTS = "speedrunPointsAtStake"
_CFG_INTERLEAVE = "speedrunInterleaveTopics"
_CFG_MODERN = "speedrunModernUi"  # opt-in reskin (default on for Speedrun builds)
# Client-only setting (plain JSON config): auto-launch the end-of-session
# reasoning round instead of offering it. Default off.
_CFG_AUTO_ROUND = "speedrunAutoReasoningRound"
# Design 2 / D7 experiment (default off, explicitly not evidence-established):
# for a proficient student, withhold immediate correctness on reasoning
# questions and reveal it later in the feedback report.
_CFG_DELAYED_FB = "speedrunDelayedFeedbackExperiment"
# A student at/above this overall exam-style accuracy counts as proficient for
# the withhold experiment (mirrors the engine's PROFICIENT_THRESHOLD).
_PROFICIENT_THRESHOLD = 0.8
_REASONING_ROUND_SIZE = 5


def _should_withhold_feedback(performance: float, enabled: bool) -> bool:
    """D7: withhold immediate correctness only when the experiment is enabled AND
    the student is already proficient. Mirrors the engine's
    ``should_withhold_correctness``; pure so it can be unit-tested."""
    return enabled and performance >= _PROFICIENT_THRESHOLD


_QUESTION_TYPE_SRS = 0

# Diagnosis.kind / routed_action -> human text (see proto/anki/speedrun.proto).
_DIAGNOSIS_LABEL = {
    1: "Memory gap",
    2: "Reasoning gap",
    3: "Passage-comprehension gap",
    4: "Test-taking gap",
}
_ACTION_LABEL = {
    1: "It will resurface sooner via spaced repetition.",
    2: "Next: concept-linked passage practice.",
    3: "Next: review your test-taking strategy.",
}

_EXPLAIN_CMD = "speedrun:explain"

# Question-side self-explain button, injected into the card web view and removed
# on reveal (self-explanation is pre-reveal). Retokened to the Speedrun accent +
# Geist (dropping the orphan indigo #5E5CE6); the colour is baked in per-mode
# since the card page may not carry the --sr-* custom properties. Single-quoted
# font family so it is safe inside the double-quoted JS cssText.
_EXPLAIN_FONT = (
    "'Geist',-apple-system,'SF Pro Text',system-ui,'Segoe UI',Roboto,sans-serif"
)

_EXPLAIN_BUTTON_TMPL = r"""
(function () {
  var id = "speedrun-explain-btn";
  var old = document.getElementById(id);
  if (old) { old.remove(); }
  var btn = document.createElement("button");
  btn.id = id;
  btn.textContent = "\uD83C\uDF99 Speak your reasoning";
  btn.title = "Say your reasoning out loud before revealing (Ctrl+Shift+E)";
  btn.style.cssText =
    "position:fixed;bottom:16px;left:50%;transform:translateX(-50%);z-index:2147483647;" +
    "padding:10px 18px;border-radius:999px;border:none;" +
    "background:__ACCENT__;color:#fff;box-shadow:0 6px 18px rgba(0,0,0,.18);" +
    "font:600 14px __FONT__;cursor:pointer;";
  btn.addEventListener("click", function () { pycmd("__CMD__"); });
  document.body.appendChild(btn);
})();
"""


def _explain_button_js() -> str:
    """The pre-reveal self-explain button JS, with the accent colour resolved for
    the current light/dark mode."""
    return (
        _EXPLAIN_BUTTON_TMPL.replace("__CMD__", _EXPLAIN_CMD)
        .replace("__ACCENT__", theme.resolved("accent", _night()))
        .replace("__FONT__", _EXPLAIN_FONT)
    )


_REMOVE_BUTTON_JS = r"""
(function () {
  var el = document.getElementById("speedrun-explain-btn");
  if (el) { el.remove(); }
})();
"""


class _SessionState:
    def __init__(self) -> None:
        self.session_id = uuid.uuid4().hex
        self.shown_at: float | None = None
        self.pending_explanation: str = ""
        # Cards reviewed in the current session, for the end-of-session
        # reasoning round (memory -> reasoning). Cleared when the reviewer ends.
        self.reviewed_card_ids: list[int] = []


_state = _SessionState()

# Guards the one-time monkeypatch of Overview._show_finished_screen.
_finished_screen_patched = False


# --- setup ------------------------------------------------------------------


def _register_fonts() -> None:
    """Register the bundled Geist/Fraunces faces with Qt so the Speedrun dialogs
    match the mobile identity (the webviews load them via @font-face instead)."""
    try:
        from aqt.qt import QFontDatabase
        from aqt.utils import aqt_data_path

        dirs = [
            str(aqt_data_path() / "web" / "imgs"),
            os.path.join(os.path.dirname(aqt.__file__), "data", "web", "imgs"),
        ]
        # Static instances first (maximally compatible with Qt's font engine);
        # the variable files are the webview @font-face source and a fallback.
        names = (
            "speedrun-geist-static.ttf",
            "speedrun-fraunces-static.ttf",
            "speedrun-geist.ttf",
            "speedrun-fraunces.ttf",
        )
        loaded: set[str] = set()
        for base in dirs:
            for name in names:
                if name in loaded:
                    continue
                path = os.path.join(str(base), name)
                if os.path.exists(path):
                    if QFontDatabase.addApplicationFont(path) != -1:
                        loaded.add(name)
    except Exception as exc:  # pragma: no cover - fonts are cosmetic
        print(f"speedrun: font registration skipped: {exc}")


def setup(mw: aqt.AnkiQt) -> None:
    """Register the reviewer hooks, native-screen hooks, and a slim menu."""
    _register_fonts()
    _install_finished_screen_override()
    _install_deckbrowser_import_button()
    _install_sync_button_override(mw)
    gui_hooks.reviewer_did_show_question.append(_on_show_question)
    gui_hooks.reviewer_did_show_answer.append(_on_show_answer)
    gui_hooks.reviewer_did_answer_card.append(_on_answer_card)
    gui_hooks.reviewer_will_end.append(_on_reviewer_will_end)
    gui_hooks.webview_did_receive_js_message.append(_on_js_message)
    # Any real Anki state move (Decks/overview/review) exits the in-place workspace.
    gui_hooks.state_did_change.append(_on_state_changed)

    # In-place native integration.
    gui_hooks.webview_will_set_content.append(_on_will_set_content)
    # The deck-browser home intentionally carries no readiness banner: its white
    # block clashed with the deck UI below it. Readiness now lives in the per-deck
    # overview panel and the Home screen in the app-shell sidebar.
    gui_hooks.overview_will_render_content.append(_on_overview_content)
    gui_hooks.reviewer_will_init_answer_buttons.append(_on_answer_buttons)
    # Theme Anki's Svelte pages (graphs, deck options, congrats fallback) and the
    # global Qt chrome (menus, tables, inputs) from the same tokens, re-applying
    # on night-mode toggles so the whole app reads as one product.
    gui_hooks.webview_did_inject_style_into_page.append(_on_style_injected)
    gui_hooks.style_did_init.append(_on_style_did_init)
    gui_hooks.theme_did_change.append(_on_theme_did_change)

    gui_hooks.collection_did_load.append(_on_collection_loaded)
    # Tear the embedded sync server down when the profile closes (atexit is a
    # backstop for hard exits).
    gui_hooks.profile_will_close.append(srsync.stop_server)

    # Home / Practice / Library are primary destinations in the app-shell
    # sidebar, so the Tools menu keeps only the card-context and setup actions.
    menu = QMenu("&Speedrun", mw)
    mw.form.menuTools.addMenu(menu)

    explain = QAction("Self-explain current card", mw)
    explain.setShortcut("Ctrl+Shift+E")
    qconnect(explain.triggered, lambda: _start_self_explanation(mw))
    menu.addAction(explain)

    exam = QAction("Set exam target…", mw)
    qconnect(exam.triggered, lambda: _set_exam_target(mw))
    menu.addAction(exam)

    setup_action = QAction("Set up Speedrun…", mw)
    qconnect(setup_action.triggered, lambda: library.open_onboarding(mw))
    menu.addAction(setup_action)


def _on_collection_loaded(_col) -> None:
    """First-run setup, deferred so it never blocks collection load: seed the
    bundled example deck on an empty collection, then offer onboarding."""
    from aqt.qt import QTimer

    mw = aqt.mw
    if mw is None:
        return

    # Anki's first setupStyle() ran before our style_did_init hook registered, so
    # rebuild the app stylesheet now that the collection (and toggle) are loaded.
    _reapply_app_style()

    def first_run() -> None:
        library.maybe_load_example_deck(mw)
        library.maybe_show_onboarding(mw)
        # Reveal the app-shell sidebar and land on Home (the readiness
        # dashboard), so the app opens on its value surface rather than a bare
        # deck list. Deferred with the rest of first-run so Anki's own startup
        # state has settled first.
        _apply_shell_visibility(mw)
        if mw.col is not None and _modern_on(mw.col):
            _show_workspace(mw, "dashboard")
        # If this collection was already paired with a phone, bring the local
        # sync server up and sync so the phone can reach fresh data immediately.
        if mw.col is not None and srsync.is_paired(mw.col):
            _sync_to_local(mw)

    QTimer.singleShot(600, first_run)


def _install_finished_screen_override() -> None:
    """Replace Anki's bare congrats page with a themed finished screen that keeps
    the readiness panel visible and offers clear paths back (dashboard / decks /
    practice). Falls back to the original when the reskin is off or on error."""
    global _finished_screen_patched
    from aqt.overview import Overview

    if _finished_screen_patched:
        return
    _finished_screen_patched = True
    original = Overview._show_finished_screen

    def patched(self) -> None:
        mw = self.mw
        try:
            if mw is None or mw.col is None or not _modern_on(mw.col):
                return original(self)
            data = _collect(mw.col, fresh=True)
            deck = mw.col.decks.current().get("name", "This deck")
            self.web.stdHtml(theme.finished_html(data, deck), context=self)
        except Exception as exc:  # pragma: no cover - always fall back safely
            print(f"speedrun: finished-screen override fell back: {exc}")
            return original(self)

    Overview._show_finished_screen = patched  # type: ignore[method-assign]


def _install_deckbrowser_import_button() -> None:
    """Surface a prominent 'Import MCAT / popular decks' button on the deck-list
    bottom bar (next to Get Shared / Create / Import File) so onboarding + the
    content library aren't buried under Tools > Speedrun. Routes to the library
    via the global speedrun: bridge handler."""
    from aqt.deckbrowser import DeckBrowser

    entry = ["", "speedrun:library", "Import MCAT / popular decks"]
    if entry not in DeckBrowser.drawLinks:
        DeckBrowser.drawLinks = DeckBrowser.drawLinks + [entry]


def _modern_on(col) -> bool:
    try:
        return bool(col.get_config(_CFG_MODERN, True))
    except Exception:
        return True


# Unified dialog content margins (spec spacing scale) so every Speedrun dialog
# shares the same generous gutter; radii are unified via ``theme.dialog_qss``.
_DIALOG_MARGINS = (24, 22, 24, 20)


def _night() -> bool:
    """Current Anki dark-mode state (defaults to light if unavailable)."""
    try:
        from aqt.theme import theme_manager

        return bool(theme_manager.night_mode)
    except Exception:
        return False


def _style_dialog(dialog: QDialog) -> None:
    """Apply the Speedrun token palette to a native Qt dialog (light/dark aware)."""
    dialog.setStyleSheet(theme.dialog_qss(_night()))


_W = TypeVar("_W", bound=QWidget)


def _mark(widget: _W, *, role: str | None = None, primary: bool = False) -> _W:
    """Tag a widget with Speedrun QSS roles (see ``theme.dialog_qss``)."""
    if role is not None:
        widget.setProperty("srRole", role)
    if primary:
        widget.setProperty("srPrimary", "1")
    return widget


def _open_home() -> None:
    mw = aqt.mw
    if mw is not None and mw.col is not None:
        mw.moveToState("deckBrowser")


# --- native reskin + embeds -------------------------------------------------


def _on_will_set_content(web_content, context) -> None:
    """Inject Speedrun tokens/components + the opt-in native reskin into Anki's
    own chrome. The reviewer *card* content is never touched (only the calm
    background behind it), and the token+component sheet is injected exactly once
    per page here rather than by each embed."""
    mw = aqt.mw
    if mw is None or mw.col is None:
        return
    from aqt.deckbrowser import DeckBrowser
    from aqt.overview import Overview
    from aqt.reviewer import Reviewer, ReviewerBottomBar
    from aqt.toolbar import TopToolbar

    modern = _modern_on(mw.col)
    if isinstance(context, (DeckBrowser, Overview)):
        # The banner / panel / finished embeds always render, so their tokens +
        # component styles must always be present -- injected once per page.
        web_content.head += theme.page_style()
        if modern:
            web_content.head += theme.screen_reskin()
        return
    # Everything below is pure chrome polish, so it stays behind the toggle.
    if not modern:
        return
    if isinstance(context, TopToolbar):
        web_content.head += theme.toolbar_reskin()
    elif isinstance(context, ReviewerBottomBar):
        # The answer-bar surface + color-coded answer buttons (chrome, not card).
        web_content.head += theme.bottombar_reskin()
    elif isinstance(context, Reviewer):
        # Calm neutral behind the card only; NON-!important so any note/template
        # styling wins the cascade and the card layout is never shifted.
        web_content.head += theme.reviewer_chrome_css()


def _on_answer_buttons(buttons, reviewer, card):
    """Relabel Again/Hard/Good/Easy with intuitive words (locale-safe, opt-in)."""
    col = reviewer.mw.col
    if col is None or not _modern_on(col):
        return buttons
    remap = {
        tr.studying_again(): "Forgot",
        tr.studying_hard(): "Hard",
        tr.studying_good(): "Got it",
        tr.studying_easy(): "Easy",
    }
    return tuple((ease, remap.get(label, label)) for ease, label in buttons)


def _on_overview_content(overview, content) -> None:
    """Embed the full readiness panel on the per-deck overview."""
    mw = aqt.mw
    if mw is None or mw.col is None:
        return
    try:
        content.table += theme.panel_html(_collect(mw.col, fresh=True))
    except Exception as exc:  # pragma: no cover - never break the overview
        print(f"speedrun: overview render failed: {exc}")


# --- Svelte pages + global Qt chrome ----------------------------------------


def _on_style_injected(webview) -> None:
    """Theme Anki's Svelte/SvelteKit pages (graphs, deck options, and the
    congrats fallback) with the Speedrun tokens by appending a <style> after
    their own styling. We never edit the Svelte source; keyed on webview kind."""
    mw = aqt.mw
    if mw is None or mw.col is None or not _modern_on(mw.col):
        return
    try:
        from aqt.webview import AnkiWebViewKind

        kind = webview.kind
    except Exception:  # pragma: no cover - defensive
        return
    if kind == AnkiWebViewKind.MAIN:
        # The main webview fires this for the congrats SvelteKit page; guard on
        # the URL so the stdHtml deck browser/overview (themed elsewhere, and
        # which leave this webview's dynamic-styling flag set) are never touched.
        guard = "if(!location.href.includes('congrats'))return;"
    elif kind in (AnkiWebViewKind.DECK_STATS, AnkiWebViewKind.DECK_OPTIONS):
        guard = ""
    else:
        return
    js = (
        "(function(){%s var id='speedrun-svelte-style';"
        "var s=document.getElementById(id);"
        "if(!s){s=document.createElement('style');s.id=id;document.head.appendChild(s);}"
        "s.textContent=%s;})();" % (guard, json.dumps(theme.svelte_page_css()))
    )
    try:
        webview.eval(js)
    except Exception as exc:  # pragma: no cover - cosmetic only
        print(f"speedrun: svelte page theming skipped: {exc}")


def _global_reskin_on() -> bool:
    """Whether to apply the global Qt chrome (needs a loaded collection so the
    per-profile toggle is readable; defaults off until then)."""
    mw = aqt.mw
    return mw is not None and mw.col is not None and _modern_on(mw.col)


def _on_style_did_init(style: str) -> str:
    """Append Speedrun's global Qt chrome (menus, tooltips, tables, inputs) to
    Anki's app stylesheet, built from the same tokens. Returns the style
    unchanged when the collection isn't ready or the toggle is off."""
    if not _global_reskin_on():
        return style
    try:
        from aqt.theme import theme_manager

        return style + theme.global_qss(bool(theme_manager.night_mode))
    except Exception:  # pragma: no cover - never break app styling
        return style


def _reapply_app_style() -> None:
    """Force Anki to rebuild its app stylesheet so our ``style_did_init`` hook
    (registered after Anki's first ``setupStyle``) and the current night value
    are included. ``apply_style`` early-returns when nothing changed, so we call
    the private builder directly. Never fires ``theme_did_change`` (no recursion)."""
    mw = aqt.mw
    if mw is None:
        return
    try:
        from aqt.theme import theme_manager

        theme_manager._apply_style(mw.app)
    except Exception as exc:  # pragma: no cover - cosmetic only
        print(f"speedrun: could not reapply app style: {exc}")


def _on_theme_did_change() -> None:
    """Re-apply the global chrome on a night-mode toggle so already-open
    Speedrun surfaces track the theme."""
    _reapply_app_style()
    _render_sidebar(aqt.mw)


# --- data collection --------------------------------------------------------


def _fmt_ms(ms: int) -> str:
    if not ms or ms <= 0:
        return "just now"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000))


def _feedback_report(col) -> dict | None:
    """The end-of-session feedback report (Design 2 / D2): miss counts by cause
    plus weakest topics, from the engine's GetFeedbackReport. None on error."""
    try:
        r = col._backend.get_feedback_report()
    except Exception:
        return None
    return {
        "total": r.total,
        "correct": r.correct,
        "memory": r.memory_misses,
        "reasoning": r.reasoning_misses,
        "passage": r.passage_misses,
        "test_taking": r.test_taking_misses,
        "weak_topics": list(r.weak_topics),
    }


def _feedback_lines(fb: dict) -> list[str]:
    """Human-readable feedback-report lines (pure; unit-tested)."""
    total = fb.get("total", 0)
    if total == 0:
        return ["No exam-style attempts recorded yet."]
    lines = [
        f"Answered {total} exam-style question(s), {fb.get('correct', 0)} correct."
    ]
    kinds = [
        ("Memory", fb.get("memory", 0)),
        ("Reasoning", fb.get("reasoning", 0)),
        ("Passage", fb.get("passage", 0)),
        ("Test-taking", fb.get("test_taking", 0)),
    ]
    misses = [f"{name}: {n}" for name, n in kinds if n]
    if misses:
        lines.append("Misses by cause \u2014 " + ", ".join(misses) + ".")
    weak = fb.get("weak_topics") or []
    if weak:
        lines.append("Weakest topics: " + ", ".join(weak[:5]) + ".")
    return lines


def _show_feedback_report(mw: aqt.AnkiQt) -> None:
    """Show the end-of-session feedback report in a simple info dialog."""
    if mw.col is None:
        return
    fb = _feedback_report(mw.col)
    if fb is None:
        tooltip("Feedback report unavailable.")
        return
    from aqt.utils import showInfo

    showInfo("\n".join(_feedback_lines(fb)), title="Speedrun feedback report")


def _collect(col, fresh: bool) -> dict:
    """Gather every Speedrun signal into a plain dict for the theme builders."""
    backend = col._backend
    snap = backend.compute_readiness() if fresh else backend.get_readiness_snapshot()
    cov = backend.get_coverage_report()

    cal = None
    try:
        c = backend.get_calibration_report()
        cal = {"sufficient": c.sufficient, "n": c.n, "brier": c.brier}
    except Exception:
        pass

    exam = None
    try:
        p = backend.get_exam_plan()
        exam = {
            "has": p.has_profile,
            "days_left": p.days_left,
            "mode": p.study_mode,
            "target": p.target_score,
            "on_track": p.on_track,
            "needed": p.needed_points,
            "per_week": p.points_per_week_needed,
            "tier": p.recommended_tier,
            "readiness_sufficient": p.readiness_sufficient,
        }
    except Exception:
        pass

    data = {
        "sufficient": snap.sufficient,
        "readiness": snap.readiness_scaled,
        "low": snap.low_scaled,
        "high": snap.high_scaled,
        "memory": snap.memory,
        "performance": snap.performance,
        "coverage": snap.coverage,
        "gap": snap.recall_perf_gap,
        "memory_ok": snap.memory_sufficient,
        "perf_ok": snap.performance_sufficient,
        "blocking": snap.blocking_dimension,
        "reason": snap.reason,
        "updated": _fmt_ms(snap.computed_at_ms),
        "cov_total": cov.topics_total,
        "cov_covered": cov.topics_covered,
        "calibration": cal,
        "exam": exam,
        "points_at_stake": bool(col.get_config(_CFG_POINTS, False)),
        "interleave": bool(col.get_config(_CFG_INTERLEAVE, False)),
        "modern_ui": _modern_on(col),
        "auto_round": bool(col.get_config(_CFG_AUTO_ROUND, False)),
    }
    data["feedback"] = _feedback_report(col)
    data["next_action"] = _next_action(data)
    return data


def _next_action(data: dict) -> dict:
    """The single recommended step, with the command that performs it so the
    panel's card can be a real button (not inert text)."""
    if not data["sufficient"]:
        seed_cmd = "speedrun:seed" if data.get("cov_total", 0) == 0 else None
        by = {
            "memory": (
                "Study more cards",
                "Readiness needs more graded reviews before it can estimate your memory signal.",
                None,
                None,
            ),
            "performance": (
                "Answer held-out questions",
                "Register and answer exam-style questions so performance is measured separately from recall.",
                "speedrun:practice",
                "Practice now",
            ),
            "coverage": (
                "Cover more of the outline",
                "Seed the MCAT outline and tag cards by topic; readiness abstains below 50% coverage.",
                seed_cmd,
                "Seed topics" if seed_cmd else None,
            ),
            "attempts": (
                "Record a few more attempts",
                "Keep reviewing; a handful more graded attempts will unlock your readiness estimate.",
                None,
                None,
            ),
        }
        title, detail, cmd, cta = by.get(
            data.get("blocking", ""),
            ("Build more evidence", data.get("reason", ""), None, None),
        )
        return {"title": title, "detail": detail, "cmd": cmd, "cta": cta}

    if data["gap"] >= 0.15:
        return {
            "title": "Bridge recall to application",
            "detail": "Your recall outruns your exam-style performance. Practice concept-linked questions to close the gap.",
            "cmd": "speedrun:practice",
            "cta": "Practice now",
        }
    exam = data.get("exam") or {}
    if (
        exam.get("has")
        and exam.get("readiness_sufficient")
        and not exam.get("on_track")
    ):
        return {
            "title": "Pick up the pace",
            "detail": f"You need about +{exam.get('needed', 0)} points "
            f"(~{exam.get('per_week', 0):.1f}/week) to reach your target by exam day.",
            "cmd": "speedrun:exam",
            "cta": "Adjust plan",
        }
    return {
        "title": "On track — keep going",
        "detail": "Memory, performance, and coverage all look healthy. Keep your spaced reviews steady.",
        "cmd": "speedrun:practice",
        "cta": "Practice",
    }


# --- reviewer hooks ---------------------------------------------------------


def _on_show_question(card: Card) -> None:
    _state.shown_at = time.monotonic()
    _state.pending_explanation = ""
    mw = aqt.mw
    if mw is None or mw.reviewer is None:
        return
    web = mw.reviewer.web
    if web is not None:
        try:
            # Clear any prior post-miss cue, then offer pre-reveal self-explain.
            web.eval(theme.REMOVE_DIAGNOSIS_JS)
            web.eval(_explain_button_js())
        except Exception as exc:  # pragma: no cover - never break the review loop
            print(f"speedrun: failed to inject self-explain button: {exc}")


def _on_show_answer(card: Card) -> None:
    mw = aqt.mw
    if mw is None or mw.reviewer is None:
        return
    web = mw.reviewer.web
    if web is not None:
        try:
            web.eval(_REMOVE_BUTTON_JS)
        except Exception:  # pragma: no cover - never break the review loop
            pass


def _on_answer_card(reviewer, card: Card, ease: int) -> None:
    mw = aqt.mw
    if mw is None or mw.col is None:
        return

    took_ms = 0
    if _state.shown_at is not None:
        took_ms = max(int((time.monotonic() - _state.shown_at) * 1000), 0)

    correct = ease > 1
    recall_failed = ease == 1

    _state.reviewed_card_ids.append(card.id)

    req = speedrun_pb2.RecordAttemptRequest(
        card_id=card.id,
        note_id=card.nid,
        session_id=_state.session_id,
        answered_at_ms=int(time.time() * 1000),
        took_ms=took_ms,
        question_type=_QUESTION_TYPE_SRS,
        correct=correct,
        signals=speedrun_pb2.ClassifyAttemptRequest(
            correct=correct,
            took_ms=took_ms,
            recall_failed=recall_failed,
            passage_evidence_missed=False,
            question_type=_QUESTION_TYPE_SRS,
        ),
        data=json.dumps({"self_explanation": _state.pending_explanation}),
    )

    try:
        resp = mw.col._backend.record_attempt(req)
        _surface_diagnosis(resp.diagnosis, correct)
    except Exception as exc:  # pragma: no cover - never break the review loop
        print(f"speedrun: failed to record attempt: {exc}")
    finally:
        _state.pending_explanation = ""


def _surface_diagnosis(diagnosis, correct: bool) -> None:
    """Themed, kind-aware post-miss cue rendered in the reviewer webview.

    Names the failure mode with its per-kind colour + icon (spec: "Diagnosis cue
    (kind-aware)"), shows the routed action, and offers an inline "Practice this"
    affordance. It is a fixed overlay (never shifts card layout) that stays until
    dismissed or the next question, so it can be read. Falls back to a plain
    tooltip if the webview injection is unavailable, so the cue is never lost."""
    if correct or diagnosis.kind not in _DIAGNOSIS_LABEL:
        return
    label = _DIAGNOSIS_LABEL[diagnosis.kind]
    action = _ACTION_LABEL.get(diagnosis.routed_action, "")
    mw = aqt.mw
    web = mw.reviewer.web if (mw is not None and mw.reviewer is not None) else None
    kind_key, icon = theme.DIAGNOSIS_STYLE.get(diagnosis.kind, ("accent", "\U0001f4a1"))
    if web is not None:
        try:
            web.eval(
                theme.diagnosis_cue_js(
                    kind_key=kind_key,
                    icon=icon,
                    title=label,
                    action=action,
                    night=_night(),
                )
            )
            return
        except Exception as exc:  # pragma: no cover - fall back to a tooltip
            print(f"speedrun: diagnosis cue injection failed: {exc}")
    msg = f"Speedrun: {label}"
    if action:
        msg += f"\n{action}"
    tooltip(msg, period=4000)


# --- pycmd bridge -----------------------------------------------------------


def _on_js_message(handled: tuple[bool, object], message: str, context: object):
    """Route Speedrun panel/button actions; leave all other messages alone."""
    if not message.startswith("speedrun:"):
        return handled
    mw = aqt.mw
    if mw is None or mw.col is None:
        return (True, None)

    if message == _EXPLAIN_CMD:
        _start_self_explanation(mw)
    elif message == "speedrun:seed":
        try:
            mw.col._backend.seed_mcat_topic_outline()
            _refresh(mw, "Seeded MCAT topics. Tag cards with a topic to cover it.")
        except Exception as exc:
            tooltip(f"Could not seed outline: {exc}")
    elif message == "speedrun:exam":
        _set_exam_target(mw)
    elif message == "speedrun:practice":
        _show_workspace(mw, "practice")
    elif message == "speedrun:report":
        _show_feedback_report(mw)
    elif message == "speedrun:library":
        library.open_library(mw)
    elif message == "speedrun:settings":
        _show_workspace(mw, "settings")
    elif message == "speedrun:dashboard":
        _show_workspace(mw, "dashboard")
    elif message == "speedrun:decks":
        _leave_workspace(mw)
    elif message.startswith("speedrun:ws:"):
        _show_workspace(mw, message.rsplit(":", 1)[-1])
    elif message.startswith("speedrun:nav:"):
        _navigate(mw, message.rsplit(":", 1)[-1])
    elif message == "speedrun:back":
        _leave_workspace(mw)
    elif message.startswith("speedrun:set:"):
        _ws_set(mw, message.split(":", 2)[2])
    elif message == "speedrun:syncnow":
        _sync_now_from_screen(mw)
    elif message.startswith("speedrun:sync:"):
        _ws_sync(mw, message[len("speedrun:sync:") :])
    elif message.startswith("speedrun:pq:submit:"):
        _ws_practice_submit(message[len("speedrun:pq:submit:") :])
    elif message == "speedrun:pq:next":
        if _ws_practice is not None:
            _ws_practice.advance()
    elif message == "speedrun:customstudy":
        try:
            from aqt.customstudy import CustomStudy

            CustomStudy.fetch_data_and_show(mw)
        except Exception:
            _open_home()
    elif message == "speedrun:refresh":
        try:
            mw.col._backend.compute_readiness()
        except Exception:
            pass
        _refresh(mw)
    elif message.startswith("speedrun:toggle:"):
        _toggle(mw, message.rsplit(":", 1)[-1])

    return (True, None)


def _toggle(mw: aqt.AnkiQt, key: str) -> None:
    col = mw.col
    if col is None:
        return
    cfg = {
        "points": _CFG_POINTS,
        "interleave": _CFG_INTERLEAVE,
        "modern": _CFG_MODERN,
        "autoround": _CFG_AUTO_ROUND,
    }.get(key)
    if cfg is None:
        return
    current = bool(col.get_config(cfg, cfg == _CFG_MODERN))
    col.set_config(cfg, not current)
    # Config affects the queue and/or the reskin, so rebuild + re-render.
    mw.reset()
    if cfg == _CFG_MODERN:
        # The global Qt chrome + the app-shell sidebar are gated on the toggle.
        _reapply_app_style()
        _apply_shell_visibility(mw)
    tooltip("On." if not current else "Off.")


def _refresh(mw: aqt.AnkiQt, msg: str | None = None) -> None:
    # If a Speedrun workspace tab is showing in the main webview, re-render it in
    # place rather than letting Anki paint the deck list over it.
    if _ws_active is not None:
        try:
            _show_workspace(mw, _ws_active)
            mw.toolbar.redraw()
        except Exception:
            pass
        if msg:
            tooltip(msg)
        return
    try:
        if mw.state == "overview":
            mw.overview.refresh()
        elif mw.state == "deckBrowser":
            mw.deckBrowser.refresh()
        else:
            mw.reset()
        # Keep the toolbar's cached readiness number in sync with the panel.
        mw.toolbar.redraw()
    except Exception:
        pass
    if msg:
        tooltip(msg)


# --- Speedrun screens rendered into the main webview ------------------------
#
# Home (dashboard), Practice, and Settings render into Anki's main webview as
# full screens. Navigation is driven by the persistent app-shell sidebar (see
# above); _ws_active tracks which screen (if any) currently owns the webview so
# a refresh re-renders it instead of letting Anki paint the deck list over it.

_ws_active: str | None = None
_ws_practice: "_WsPractice | None" = None

_CFG_DEFAULTS = {
    _CFG_POINTS: False,
    _CFG_INTERLEAVE: False,
    _CFG_AUTO_ROUND: False,
    _CFG_DELAYED_FB: False,
    _CFG_MODERN: True,
    srai._CFG_AI_DIAGNOSIS: False,
}


class _WsContext:
    """Sentinel render/bridge context for the workspace webview, so the
    will_set_content hook doesn't treat it as the deck browser."""


_ws_ctx = _WsContext()


def _render_ws(mw: aqt.AnkiQt, body: str) -> None:
    """Render a Speedrun screen into the main webview and empty the deck-list
    bottom bar, so it reads as a full in-window screen. Navigation lives in the
    persistent left sidebar (see the app-shell section below)."""
    mw.web.stdHtml(theme.screen_html(body), head=theme.page_style(), context=_ws_ctx)
    try:
        mw.bottomWeb.stdHtml("")
    except Exception:
        pass


# --- app shell: persistent left sidebar -------------------------------------
#
# Speedrun's primary chrome is a persistent left rail (Home / Study / Practice /
# Library / Settings) rendered into its own webview, so it is never clobbered by
# an Anki state change the way content painted into the main webview is. Home /
# Practice / Settings render as Speedrun screens in mw.web; Study hands off to
# Anki's native deck -> overview -> reviewer flow.

_shell_web: Any = None
_shell_holder: Any = None


class _ShellContext:
    """Render/bridge context for the sidebar webview, so the will_set_content
    hook doesn't treat it as the deck browser."""


_shell_ctx = _ShellContext()

# Map the active Speedrun screen (_ws_active) to the sidebar item to highlight.
# None (no Speedrun screen showing) means a native Anki state is active = Study.
_SECTION_FOR_WS = {
    "dashboard": "home",
    "practice": "practice",
    "settings": "settings",
    "sync": "sync",  # the sync screen; no nav item matches, so none highlights
}


def install_app_shell(mw: aqt.AnkiQt) -> bool:
    """Wrap the main window's vertical stack (toolbar / web / bottom) in a
    horizontal layout with a persistent left sidebar. Called from
    setupMainWindow before the plain layout is applied; returns True if the shell
    took over the layout, or False on any failure so the caller falls back to the
    vanilla vertical layout. The sidebar starts hidden and is shown once a
    collection is loaded and the modern UI is enabled."""
    global _shell_web, _shell_holder
    try:
        from aqt.qt import QHBoxLayout, QVBoxLayout, QWidget
        from aqt.webview import AnkiWebView

        holder = QWidget()
        holder.setObjectName("speedrunSidebar")
        holder.setFixedWidth(216)
        hlay = QVBoxLayout(holder)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(0)
        web = AnkiWebView(mw)
        web.set_bridge_command(_sidebar_bridge, _shell_ctx)
        hlay.addWidget(web)
        holder.setVisible(False)

        inner = QWidget()
        inner.setLayout(mw.mainLayout)
        outer = QHBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(holder)
        outer.addWidget(inner, 1)
        mw.form.centralwidget.setLayout(outer)

        _shell_web = web
        _shell_holder = holder
        return True
    except Exception as exc:  # pragma: no cover - never block window setup
        print(f"speedrun: app shell install failed: {exc}")
        return False


def _sidebar_bridge(message: object) -> object:
    """Route a pycmd from the sidebar webview through the shared handler."""
    _on_js_message((False, None), str(message), _shell_ctx)
    return None


def _shell_active_section() -> str:
    return _SECTION_FOR_WS.get(_ws_active or "", "study")


def _sync_chip_state(mw: aqt.AnkiQt) -> dict:
    """Status for the sidebar's sync chip (delegates to the sync module)."""
    try:
        return srsync.status(mw)
    except Exception:
        return {"state": "idle", "label": "Sync with phone", "detail": ""}


def _render_sidebar(mw: aqt.AnkiQt | None) -> None:
    """Re-render the sidebar to reflect the active section + sync state. Safe
    no-op before the shell is installed or a collection is loaded."""
    web = _shell_web
    if web is None or mw is None or mw.col is None:
        return
    try:
        html = theme.sidebar_html(_shell_active_section(), _sync_chip_state(mw))
        web.stdHtml(html, head=theme.page_style(), context=_shell_ctx)
    except Exception as exc:  # pragma: no cover - never break navigation
        print(f"speedrun: sidebar render failed: {exc}")


def _apply_shell_visibility(mw: aqt.AnkiQt) -> None:
    """Show the sidebar when the modern UI is on, hide it otherwise (reverting to
    vanilla Anki chrome). Re-renders it when shown."""
    holder = _shell_holder
    if holder is None:
        return
    on = _modern_on(mw.col) if mw.col is not None else False
    holder.setVisible(on)
    if on:
        _render_sidebar(mw)


def _navigate(mw: aqt.AnkiQt, section: str) -> None:
    """Handle a sidebar nav click. Study hands off to Anki's native flow, the
    Speedrun screens render in the main webview, and Library opens its dialog."""
    global _ws_active
    if mw.col is None:
        return
    if section == "study":
        _ws_active = None
        mw.moveToState("deckBrowser")
        _render_sidebar(mw)
    elif section == "library":
        library.open_library(mw)
    elif section == "sync":
        _show_sync_pair(mw)
    elif section in ("practice", "settings"):
        _show_workspace(mw, section)
    else:  # home / dashboard / anything unrecognized
        _show_workspace(mw, "dashboard")


# --- one-button phone sync (Sync with phone) --------------------------------
#
# The desktop hosts the sync server (speedrun_sync manages the child process +
# credentials); this section owns the client-side orchestration: point Anki's
# own sync at the local server, run the native sync, and drive the pairing UI.

_original_sync_button = None


def _qr_svg(text: str) -> str:
    """Render a payload string as an inline SVG QR (dark modules on white, so it
    scans in both light and dark themes)."""
    try:
        import segno

        return segno.make(text, error="m").svg_inline(
            scale=5, border=2, dark="#16181D", light="#FFFFFF"
        )
    except Exception as exc:  # pragma: no cover - QR is best-effort
        print(f"speedrun: qr generation failed: {exc}")
        return ""


def _sync_pair_data(mw: aqt.AnkiQt) -> dict:
    running = srsync.is_running()
    data: dict = {"running": running}
    if running:
        payload = srsync.pairing_payload(mw)
        data.update(
            {
                "url": payload.get("url", ""),
                "user": payload.get("user", ""),
                "token": payload.get("token", ""),
                "qr_svg": _qr_svg(json.dumps(payload)),
            }
        )
    return data


def _show_sync_pair(
    mw: aqt.AnkiQt, *, start: bool = True, status_msg: str = ""
) -> None:
    """Render the Sync-with-phone screen. When ``start`` is set, bring the local
    server up first so the QR reflects a live, reachable address."""
    global _ws_active
    if mw.col is None:
        return
    started = srsync.start_server(mw) if start else False
    _ws_active = "sync"
    data = _sync_pair_data(mw)
    if status_msg:
        data["status"] = status_msg
    _render_ws(mw, theme.sync_pair_body(data))
    _render_sidebar(mw)
    # On the first pairing, seed the server with this device's collection so the
    # phone receives current data the moment it scans.
    if started and not srsync.is_paired(mw.col):
        _sync_to_local(mw)


def _run_native_sync(mw: aqt.AnkiQt) -> None:
    """Run Anki's real sync via the original (unpatched) toolbar handler."""
    if _original_sync_button is not None:
        _original_sync_button()


def _sync_to_local(mw: aqt.AnkiQt, on_done=None) -> None:
    """Start the local server, sign Anki's own sync in against it, and sync. This
    is the shared path for the toolbar Sync button, the chip, and auto-sync."""
    if mw.col is None or not srsync.start_server(mw):
        return
    user, token = srsync.creds(mw.col)
    url = srsync.local_url()
    if not url:
        return
    mw.pm.set_custom_sync_url(url)

    def do_login():
        return mw.col.sync_login(username=user, password=token, endpoint=url)

    def after_login(fut) -> None:
        try:
            auth = fut.result()
        except Exception as exc:
            tooltip(f"Local sync sign-in failed: {exc}")
            return
        mw.pm.set_sync_key(auth.hkey)
        try:
            mw.pm.set_sync_username(user)
        except Exception:
            pass
        srsync.mark_paired(mw.col)
        _run_native_sync(mw)
        if on_done is not None:
            on_done()

    mw.taskman.with_progress(do_login, after_login, parent=mw)


def _sync_now_from_screen(mw: aqt.AnkiQt) -> None:
    """The Sync screen's primary button: ensure the server is up, then sync."""
    if not srsync.start_server(mw):
        _show_sync_pair(mw, start=False)
        return
    _show_sync_pair(mw, start=False, status_msg="Syncing…")
    _sync_to_local(
        mw, on_done=lambda: _show_sync_pair(mw, start=False, status_msg="Synced.")
    )


def _settings_items(col) -> list[dict]:
    def on(key: str, default: bool) -> bool:
        return bool(col.get_config(key, default))

    return [
        {
            "key": _CFG_POINTS,
            "title": "Points-at-stake queue",
            "on": on(_CFG_POINTS, False),
            "desc": "Order due cards by weakness \u00d7 topic weight, so the "
            "highest-value cards come first.",
        },
        {
            "key": _CFG_INTERLEAVE,
            "title": "Spaced + interleaved practice",
            "on": on(_CFG_INTERLEAVE, False),
            "desc": "Interleave confusable sibling topics (same parent tag) "
            "across reviews and new cards.",
        },
        {
            "key": _CFG_AUTO_ROUND,
            "title": "Auto reasoning round",
            "on": on(_CFG_AUTO_ROUND, False),
            "desc": "After you finish a deck's reviews, jump straight into a "
            "reasoning check (otherwise it's offered).",
        },
        {
            "key": _CFG_DELAYED_FB,
            "title": "Delayed feedback (experiment)",
            "on": on(_CFG_DELAYED_FB, False),
            "desc": "Experimental, not established: once you're proficient, "
            "reasoning questions hold back whether you were right until your "
            "feedback report.",
        },
        {
            "key": _CFG_MODERN,
            "title": "Modern UI",
            "on": on(_CFG_MODERN, True),
            "desc": "Apple-style reskin of Anki's deck list, overview, and toolbar.",
        },
        {
            "key": srai._CFG_AI_DIAGNOSIS,
            "title": "AI diagnosis (beta)",
            "on": on(srai._CFG_AI_DIAGNOSIS, False),
            "desc": "Explain misses with the source-grounded AI coach. Falls back "
            "to the built-in classifier when off; every AI diagnosis cites its source.",
        },
    ]


def _install_sync_button_override(mw: aqt.AnkiQt) -> None:
    """Route the toolbar Sync button to the one-button phone-sync flow instead of
    AnkiWeb (this fork's modified schema can't sync to AnkiWeb). When already
    paired it syncs to the local server directly; otherwise it opens the
    Sync-with-phone screen (start server + show QR). The original handler is
    saved so _run_native_sync can run the real Anki sync without recursing."""
    global _original_sync_button
    _original_sync_button = mw.on_sync_button_clicked

    def patched() -> None:
        col = mw.col
        if col is not None and srsync.is_paired(col):
            _sync_to_local(mw)
        else:
            _show_sync_pair(mw)

    mw.on_sync_button_clicked = patched  # type: ignore[method-assign]


def _sync_info(mw: aqt.AnkiQt) -> dict:
    """Prefill + status for the in-place self-hosted sync section."""
    col = mw.col
    url = str(col.get_config("speedrunSyncUrl", "") or "") if col else ""
    user = str(col.get_config("speedrunSyncUser", "") or "") if col else ""
    status = ""
    try:
        if mw.pm.sync_auth() is not None:
            who = user or "your server"
            status = (
                f"Signed in to {who}. The toolbar Sync button now uses this "
                "self-hosted server \u2014 no AnkiWeb account."
            )
    except Exception:
        pass
    return {"url": url, "username": user, "status": status}


def _ws_sync(mw: aqt.AnkiQt, raw: str) -> None:
    """Sign in to a self-hosted Anki sync server and sync, mirroring the phone.
    Stores the auth + custom URL so Anki's own Sync button then works silently
    (no AnkiWeb 'Account Required')."""
    from urllib.parse import unquote

    col = mw.col
    if col is None:
        return
    try:
        data = json.loads(unquote(raw))
    except Exception:
        return
    url = str(data.get("url", "")).strip()
    user = str(data.get("user", "")).strip()
    password = str(data.get("pass", ""))
    if not url or not user:
        tooltip("Enter a server URL and username.")
        return
    col.set_config("speedrunSyncUrl", url)
    col.set_config("speedrunSyncUser", user)
    mw.pm.set_custom_sync_url(url)

    def do_login():
        return mw.col.sync_login(username=user, password=password, endpoint=url)

    def on_done(fut) -> None:
        try:
            auth = fut.result()
        except Exception as exc:
            tooltip(f"Sync sign-in failed: {exc}")
            _show_workspace(mw, "settings")
            return
        mw.pm.set_sync_key(auth.hkey)
        try:
            mw.pm.set_sync_username(user)
        except Exception:
            pass
        tooltip("Signed in \u2014 syncing\u2026")
        try:
            mw.on_sync_button_clicked()
        except Exception as exc:
            tooltip(f"Sync failed: {exc}")
        _show_workspace(mw, "settings")

    mw.taskman.with_progress(do_login, on_done, parent=mw)


def _ws_set(mw: aqt.AnkiQt, key: str) -> None:
    """Flip a settings toggle from the in-place settings page and re-render it."""
    col = mw.col
    if col is None:
        return
    default = _CFG_DEFAULTS.get(key, False)
    col.set_config(key, not bool(col.get_config(key, default)))
    if key == _CFG_MODERN:
        _reapply_app_style()
        _apply_shell_visibility(mw)
    _show_workspace(mw, "settings")


def _fetch_practice_questions(mw: aqt.AnkiQt) -> list[dict]:
    try:
        raw = list(mw.col._backend.get_practice_questions(limit=20, topic=""))
    except Exception as exc:
        tooltip(f"Could not load practice questions: {exc}")
        return []
    return _parse_question_items(raw)


def _show_workspace(mw: aqt.AnkiQt, tab: str) -> None:
    """Render a Speedrun screen (home/dashboard, practice, or settings) into the
    main webview and sync the sidebar highlight."""
    global _ws_active, _ws_practice
    if mw.col is None:
        return
    if tab == "practice":
        _ws_active = "practice"
        if _ws_practice is None or _ws_practice.done():
            _ws_practice = _WsPractice(mw, _fetch_practice_questions(mw))
        _ws_practice.render()
    elif tab == "settings":
        _ws_active = "settings"
        _render_ws(mw, theme.settings_body(_settings_items(mw.col), _sync_info(mw)))
    else:
        _ws_active = "dashboard"
        _render_ws(mw, theme._stack(_collect(mw.col, fresh=True)))
    _render_sidebar(mw)


def _leave_workspace(mw: aqt.AnkiQt) -> None:
    global _ws_active
    _ws_active = None
    _open_home()
    _render_sidebar(mw)


def _on_state_changed(new_state: str, old_state: str) -> None:
    """A native Anki state move (Decks/overview/review) leaves the Speedrun
    screen; reflect that as the Study section in the sidebar."""
    global _ws_active
    _ws_active = None
    _render_sidebar(aqt.mw)


def _ws_practice_submit(raw: str) -> None:
    from urllib.parse import unquote

    if _ws_practice is None:
        return
    try:
        data = json.loads(unquote(raw))
        sel = int(data.get("sel", -1))
    except Exception:
        return
    if sel < 0:
        return
    _ws_practice.submit(sel, int(data.get("conf", 0)), str(data.get("explain", "")))


class _WsPractice:
    """In-place counterpart of _PracticeDialog: records each answer as an
    exam-style attempt (question_type=2) and renders question/verdict/AI into
    the main webview instead of a popup dialog."""

    _CONFIDENCE = {1: 0.35, 2: 0.6, 3: 0.85}

    def __init__(self, mw: aqt.AnkiQt, questions: list[dict]) -> None:
        self.mw = mw
        self.questions = questions
        self.index = 0
        self.answered = False
        self.correct_count = 0
        self.shown_at = time.monotonic()
        self.selected: int | None = None
        self.verdict: str | None = None
        self.verdict_text = ""
        self.feedback = ""
        self.ai: dict | None = None
        self._ai_index: int | None = None
        self._withhold = False
        if questions and bool(mw.col.get_config(_CFG_DELAYED_FB, False)):
            try:
                perf = mw.col._backend.get_performance_report().performance_rate
                self._withhold = _should_withhold_feedback(perf, True)
            except Exception:
                self._withhold = False

    def done(self) -> bool:
        return not self.questions or self.index >= len(self.questions)

    def _view(self) -> dict:
        if not self.questions:
            return {"empty": True}
        return {
            "empty": False,
            "index": self.index,
            "total": len(self.questions),
            "answered": self.answered,
            "selected": self.selected,
            "q": self.questions[self.index],
            "verdict": self.verdict,
            "verdict_text": self.verdict_text,
            "feedback": self.feedback,
            "ai": self.ai,
            "is_last": self.index + 1 >= len(self.questions),
        }

    def render(self) -> None:
        _render_ws(self.mw, theme.practice_body(self._view()))

    def submit(self, sel: int, conf_index: int, explanation: str) -> None:
        if self.answered or self.done():
            return
        q = self.questions[self.index]
        correct = sel == q["correct_index"]
        if correct:
            self.correct_count += 1
        self.selected = sel
        took_ms = max(int((time.monotonic() - self.shown_at) * 1000), 0)
        predicted = self._CONFIDENCE.get(conf_index)
        req = speedrun_pb2.RecordAttemptRequest(
            card_id=q["card_id"],
            note_id=0,
            session_id=_state.session_id,
            answered_at_ms=int(time.time() * 1000),
            took_ms=took_ms,
            question_type=2,
            selected=sel,
            correct=correct,
            signals=speedrun_pb2.ClassifyAttemptRequest(
                correct=correct,
                took_ms=took_ms,
                recall_failed=False,
                passage_evidence_missed=False,
                question_type=2,
            ),
            data=json.dumps({"self_explanation": explanation}),
        )
        if predicted is not None:
            req.predicted = predicted
        diagnosis = None
        try:
            diagnosis = self.mw.col._backend.record_attempt(req).diagnosis
        except Exception as exc:
            tooltip(f"Could not record attempt: {exc}")
        self.answered = True
        if self._withhold:
            self.verdict = "muted"
            self.verdict_text = "Answer banked"
            self.feedback = (
                "Delayed feedback is on. You'll find out whether you were right in "
                "your feedback report \u2014 try to re-derive it before then."
            )
            self.render()
            return
        ci = q["correct_index"]
        self.verdict = "good" if correct else "bad"
        self.verdict_text = "Correct" if correct else "Not quite"
        parts = [f"Answer: {chr(65 + ci)}. {q['options'][ci]}"]
        if q["explanation"]:
            parts.append(q["explanation"])
        if diagnosis is not None and not correct and diagnosis.kind in _DIAGNOSIS_LABEL:
            parts.append(_DIAGNOSIS_LABEL[diagnosis.kind])
            action = _ACTION_LABEL.get(diagnosis.routed_action, "")
            if action:
                parts.append(action)
        self.feedback = "\n".join(parts)
        self.render()
        self._maybe_ai(q, sel, took_ms, predicted, correct)

    def _maybe_ai(self, q, selected, took_ms, predicted, correct) -> None:
        if correct or self.mw.col is None:
            return
        if not (srai.enabled(self.mw.col) and srai.available()):
            return
        item = {
            "topic": q["topic"],
            "stem": q["stem"],
            "options": q["options"],
            "correct_index": q["correct_index"],
            "selected_index": selected,
            "explanation": q["explanation"],
        }
        signals = {
            "took_ms": took_ms,
            "confidence": float(predicted or 0.0),
            "question_type": 2,
        }
        self._ai_index = self.index
        self.ai = {"status": "Analyzing your miss against the source\u2026"}
        self.render()
        srai.diagnose_in_background(self.mw, item, signals, self._on_ai)

    def _on_ai(self, result) -> None:
        if self._ai_index != self.index or not self.answered:
            return
        if not result or result.get("error"):
            self.ai = {
                "status": "AI coach unavailable \u2014 using the rule-based diagnosis."
            }
        elif result.get("abstained"):
            self.ai = {
                "status": "Not confident enough \u2014 deferring to the rule-based diagnosis."
            }
        else:
            name = (result.get("kind_name") or "").replace("_", " ").strip()
            rationale = (result.get("rationale") or "").strip()
            body = (
                f"{name}: {rationale}"
                if name
                else (rationale or "No rationale returned.")
            )
            self.ai = {"body": body, "source": (result.get("source") or "").strip()}
        if _ws_active == "practice":
            self.render()

    def advance(self) -> None:
        self.index += 1
        if self.index >= len(self.questions):
            tooltip(
                f"Practice complete: {self.correct_count}/{len(self.questions)} correct. "
                "These now feed your performance signal and calibration."
            )
            try:
                self.mw.col._backend.compute_readiness()
            except Exception:
                pass
            _show_workspace(self.mw, "dashboard")
            return
        self.answered = False
        self.selected = None
        self.verdict = None
        self.verdict_text = ""
        self.feedback = ""
        self.ai = None
        self._ai_index = None
        self.shown_at = time.monotonic()
        self.render()


# --- self-explanation (voice or text) ---------------------------------------


def _start_self_explanation(mw: aqt.AnkiQt) -> None:
    if mw.state != "review" or mw.reviewer.card is None:
        tooltip("Self-explanation is only available while reviewing a card.")
        return
    dialog = _ExplainDialog(mw)
    if dialog.exec():
        _state.pending_explanation = dialog.text()
        tooltip("Self-explanation captured.")


class _SignalBridge(QObject):
    """Marshals RealtimeSTT worker-thread callbacks onto the Qt main thread."""

    interim = pyqtSignal(str)
    final = pyqtSignal(str)
    ready = pyqtSignal()
    error = pyqtSignal(str)


class _ExplainDialog(QDialog):
    """Voice-first pre-reveal self-explanation with live transcription.

    Opens listening immediately (when available), streams interim text into the
    field as you speak, and finalizes on Stop. Typing is always the fallback:
    batch faster-whisper if RealtimeSTT is missing, or text-only if neither.
    """

    def __init__(self, mw: aqt.AnkiQt) -> None:
        super().__init__(mw)
        self.mw = mw
        self._live: voice.LiveTranscriber | None = None
        self._finalized = False
        self.setWindowTitle("Speedrun self-explanation")
        disable_help_button(self)
        self.resize(500, 340)

        title = _mark(QLabel("Explain your reasoning"), role="display")

        self.status = _mark(QLabel(), role="muted")
        self.status.setWordWrap(True)

        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(
            "Your spoken reasoning appears here — or just type."
        )
        self.edit.setPlainText(_state.pending_explanation)

        self.action_btn = QPushButton()
        qconnect(self.action_btn.clicked, self._on_action)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        qconnect(buttons.accepted, self.accept)
        qconnect(buttons.rejected, self.reject)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            _mark(ok_btn, primary=True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_DIALOG_MARGINS)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(self.status)
        layout.addWidget(self.edit)
        layout.addWidget(self.action_btn)
        layout.addWidget(buttons)
        _style_dialog(self)

        self._bridge = _SignalBridge()
        qconnect(self._bridge.interim, self._on_interim)
        qconnect(self._bridge.final, self._on_final)
        qconnect(self._bridge.ready, self._on_ready)
        qconnect(self._bridge.error, self._on_error)

        if voice.LiveTranscriber.available():
            self._start_live()
        elif voice.is_available():
            self._mode_batch()
        else:
            self._mode_text_only()

    def text(self) -> str:
        return self.edit.toPlainText()

    # --- live streaming mode ---

    def _start_live(self) -> None:
        self._finalized = False
        self.edit.setReadOnly(True)
        self.status.setText("● Starting microphone…")
        self.action_btn.setText("Stop")
        self.action_btn.setEnabled(False)
        self._live = voice.LiveTranscriber()
        try:
            self._live.start(
                on_interim=self._bridge.interim.emit,
                on_final=self._bridge.final.emit,
                on_ready=self._bridge.ready.emit,
                on_error=self._bridge.error.emit,
            )
        except Exception as exc:
            self._on_error(str(exc))

    def _on_ready(self) -> None:
        self.status.setText("● Listening — say your reasoning, then press Stop.")
        self.action_btn.setEnabled(True)

    def _on_interim(self, text: str) -> None:
        if self._finalized:
            return
        self.edit.setPlainText(text)
        self.edit.moveCursor(QTextCursor.MoveOperation.End)

    def _on_final(self, text: str) -> None:
        self._finalized = True
        if text:
            self.edit.setPlainText(text)
            self.edit.moveCursor(QTextCursor.MoveOperation.End)
        self.edit.setReadOnly(False)
        self.status.setText("Captured — edit if needed, or record again.")
        self.action_btn.setText("Record again")
        self.action_btn.setEnabled(True)

    def _on_error(self, msg: str) -> None:
        self._shutdown_live()
        self.edit.setReadOnly(False)
        if voice.is_available():
            self._mode_batch()
        else:
            self._mode_text_only()
        self.status.setText("Voice unavailable — type your reasoning.")

    def _on_action(self) -> None:
        if self._live is not None and not self._finalized:
            self.action_btn.setEnabled(False)
            self.action_btn.setText("Finishing…")
            self._live.stop()
        elif self._live is not None and self._finalized:
            self._shutdown_live()
            self._start_live()
        else:
            self._record_batch()

    # --- batch / text fallback ---

    def _mode_batch(self) -> None:
        self._live = None
        self.edit.setReadOnly(False)
        self.status.setText("Record your reasoning, or type it below.")
        self.action_btn.setText("Record voice")
        self.action_btn.setEnabled(True)

    def _mode_text_only(self) -> None:
        self._live = None
        self.edit.setReadOnly(False)
        self.status.setText("Type your reasoning before revealing.")
        self.action_btn.setVisible(False)

    def _record_batch(self) -> None:
        from aqt.sound import record_audio

        def on_done(path: str) -> None:
            self.action_btn.setText("Transcribing…")
            self.action_btn.setEnabled(False)

            def task() -> str:
                return voice.transcribe(path)

            def done(future) -> None:
                self.action_btn.setText("Record voice")
                self.action_btn.setEnabled(True)
                try:
                    text = future.result()
                except Exception as exc:
                    tooltip(f"Transcription failed: {exc}")
                    return
                if text:
                    current = self.edit.toPlainText().strip()
                    self.edit.setPlainText(
                        (current + " " + text).strip() if current else text
                    )

            self.mw.taskman.run_in_background(task, done)

        record_audio(self, self.mw, False, on_done)

    # --- lifecycle ---

    def _shutdown_live(self) -> None:
        if self._live is not None:
            live, self._live = self._live, None
            live.shutdown()

    def accept(self) -> None:
        self._shutdown_live()
        super().accept()

    def reject(self) -> None:
        self._shutdown_live()
        super().reject()


# --- question practice (performance / reasoning loop) -----------------------


def _parse_question_items(raw) -> list[dict]:
    """Turn backend QuestionItems into the dicts the practice dialog expects."""
    questions = []
    for item in raw:
        try:
            payload = json.loads(item.payload)
        except Exception:
            continue
        options = payload.get("options") or []
        if len(options) < 2:
            continue
        questions.append(
            {
                "card_id": item.card_id,
                "topic": item.topic,
                "stem": payload.get("stem", ""),
                "options": options,
                "correct_index": int(payload.get("correct_index", 0)),
                "explanation": payload.get("explanation", ""),
            }
        )
    return questions


def _merge_questions(primary: list[dict], extra: list[dict], target: int) -> list[dict]:
    """Blend the session round with the engine's scheduled due-reasoning items,
    de-duplicating by (card_id, stem) and capping at ``target``. Pure so it can
    be unit-tested without Qt/backend."""
    seen = {(q.get("card_id", 0), q.get("stem", "")) for q in primary}
    merged = list(primary)
    for q in extra:
        if len(merged) >= target:
            break
        key = (q.get("card_id", 0), q.get("stem", ""))
        if key not in seen:
            seen.add(key)
            merged.append(q)
    return merged[:target]


# --- end-of-session reasoning round (memory -> reasoning) --------------------


def _on_reviewer_will_end() -> None:
    """After a review session, offer a reasoning round on the concepts just
    reviewed. Fires only when the deck's due cards actually ran out (not on a
    manual mid-session exit), and is deferred so the transition finishes first."""
    mw = aqt.mw
    if mw is None or mw.col is None:
        return
    card_ids = list(_state.reviewed_card_ids)
    _state.reviewed_card_ids = []
    if not card_ids:
        return
    try:
        finished = sum(mw.col.sched.counts()) == 0
    except Exception:
        finished = False
    if not finished:
        return
    from aqt.qt import QTimer

    QTimer.singleShot(500, lambda: _launch_reasoning_round(mw, card_ids))


def _launch_reasoning_round(mw: aqt.AnkiQt, card_ids: list[int]) -> None:
    if mw.col is None:
        return
    try:
        raw = list(
            mw.col._backend.get_session_reasoning_round(
                reviewed_card_ids=card_ids, limit=_REASONING_ROUND_SIZE
            )
        )
    except Exception as exc:  # pragma: no cover - never break the review flow
        print(f"speedrun: reasoning round fetch failed: {exc}")
        return
    questions = _parse_question_items(raw)
    # Top up the session round with the engine's scheduled reasoning-due queue
    # (Design 2 / D1): weak-bridge topics the student hasn't practiced recently.
    if len(questions) < _REASONING_ROUND_SIZE:
        try:
            due_raw = list(
                mw.col._backend.get_due_reasoning(limit=_REASONING_ROUND_SIZE)
            )
            questions = _merge_questions(
                questions, _parse_question_items(due_raw), _REASONING_ROUND_SIZE
            )
        except Exception as exc:  # pragma: no cover - never break the review flow
            print(f"speedrun: due-reasoning top-up failed: {exc}")
    if not questions:
        return
    if bool(mw.col.get_config(_CFG_AUTO_ROUND, False)):
        _PracticeDialog(mw, questions).exec()
    else:
        _offer_reasoning_round(mw, questions)


def _offer_reasoning_round(mw: aqt.AnkiQt, questions: list[dict]) -> None:
    """A themed Start/Skip card bridging the memory phase to the reasoning phase."""
    dialog = QDialog(mw)
    dialog.setWindowTitle("Speedrun reasoning check")
    disable_help_button(dialog)

    title = _mark(QLabel("Reasoning check"), role="display")
    body = _mark(
        QLabel(
            f"You finished your review. Try {len(questions)} exam-style "
            "question(s) on today's concepts — to see whether recall has become "
            "application."
        ),
        role="muted",
    )
    body.setWordWrap(True)

    skip = QPushButton("Skip")
    qconnect(skip.clicked, dialog.reject)
    start = _mark(QPushButton("Start reasoning check"), primary=True)
    qconnect(start.clicked, dialog.accept)

    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(skip)
    row.addWidget(start)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(*_DIALOG_MARGINS)
    layout.setSpacing(14)
    layout.addWidget(title)
    layout.addWidget(body)
    layout.addLayout(row)
    _style_dialog(dialog)

    if dialog.exec():
        _PracticeDialog(mw, questions).exec()


class _PracticeDialog(QDialog):
    """Answer held-out exam-style questions; each answer is recorded as an
    exam attempt (question_type=2) so it feeds the performance signal and
    calibration -- the same evidence the mobile practice screen records."""

    _CONFIDENCE = {1: 0.35, 2: 0.6, 3: 0.85}

    def __init__(self, mw: aqt.AnkiQt, questions: list[dict]) -> None:
        super().__init__(mw)
        self.mw = mw
        self.questions = questions
        self.index = 0
        self.answered = False
        self.correct_count = 0
        self.shown_at = time.monotonic()
        self.pending_explanation = ""
        self.setWindowTitle("Speedrun practice")
        disable_help_button(self)
        self.resize(560, 480)

        self.progress = _mark(QLabel(), role="eyebrow")
        self.stem = _mark(QLabel(), role="title")
        self.stem.setWordWrap(True)

        self.group = QButtonGroup(self)
        self.options_box = QVBoxLayout()
        options_container = QWidget()
        options_container.setLayout(self.options_box)

        conf_row = QHBoxLayout()
        conf_row.addWidget(_mark(QLabel("Confidence:"), role="muted"))
        self.confidence = QComboBox()
        self.confidence.addItems(["(skip)", "Low", "Medium", "High"])
        conf_row.addWidget(self.confidence)
        conf_row.addStretch(1)

        self.explain_btn = QPushButton("Self-explain (optional)")
        qconnect(self.explain_btn.clicked, self._self_explain)

        # Verdict headline (coloured performance-green / danger-red on submit).
        self.verdict = _mark(QLabel(), role="title")
        self.verdict.setVisible(False)
        self.feedback = _mark(QLabel(), role="muted")
        self.feedback.setWordWrap(True)

        # AI coach gets its own card region (spinner while analysing, then the
        # rationale + a "Source: …" citation chip) instead of appended text.
        self.ai_card = QFrame()
        self.ai_card.setProperty("srCard", "1")
        self.ai_card.setVisible(False)
        ai_layout = QVBoxLayout(self.ai_card)
        ai_layout.setContentsMargins(14, 12, 14, 12)
        ai_layout.setSpacing(6)
        ai_layout.addWidget(_mark(QLabel("AI coach"), role="eyebrow"))
        self.ai_spinner = QProgressBar()
        self.ai_spinner.setRange(0, 0)  # indeterminate = spinner/skeleton
        self.ai_spinner.setTextVisible(False)
        self.ai_spinner.setVisible(False)
        ai_layout.addWidget(self.ai_spinner)
        self.ai_status = _mark(QLabel(), role="muted")
        self.ai_status.setWordWrap(True)
        ai_layout.addWidget(self.ai_status)
        self.ai_body = QLabel()
        self.ai_body.setWordWrap(True)
        self.ai_body.setVisible(False)
        ai_layout.addWidget(self.ai_body)
        self.ai_source = _mark(QLabel(), role="chip")
        self.ai_source.setVisible(False)
        self.ai_source.setWordWrap(True)
        ai_source_row = QHBoxLayout()
        ai_source_row.addWidget(self.ai_source)
        ai_source_row.addStretch(1)
        ai_layout.addLayout(ai_source_row)

        self.action_btn = _mark(QPushButton("Submit answer"), primary=True)
        qconnect(self.action_btn.clicked, self._on_action)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        qconnect(close_box.rejected, self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_DIALOG_MARGINS)
        layout.setSpacing(12)
        layout.addWidget(self.progress)
        layout.addWidget(self.stem)
        layout.addWidget(options_container)
        layout.addLayout(conf_row)
        layout.addWidget(self.explain_btn)
        layout.addWidget(self.verdict)
        layout.addWidget(self.feedback)
        layout.addWidget(self.ai_card)
        layout.addWidget(self.action_btn)
        layout.addWidget(close_box)
        _style_dialog(self)

        # D7 experiment: decide once whether to withhold immediate correctness
        # (only when the flag is on AND the student is already proficient).
        self._withhold_feedback = False
        if bool(mw.col.get_config(_CFG_DELAYED_FB, False)):
            try:
                perf = mw.col._backend.get_performance_report().performance_rate
                self._withhold_feedback = _should_withhold_feedback(perf, True)
            except Exception:
                self._withhold_feedback = False

        self._load()

    def _clear_options(self) -> None:
        for button in self.group.buttons():
            self.group.removeButton(button)
            button.deleteLater()
        while self.options_box.count():
            item = self.options_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        """Re-evaluate QSS after a dynamic property (srState/srRole) changed."""
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
        widget.update()

    def _reset_ai_card(self) -> None:
        self.ai_card.setVisible(False)
        self.ai_spinner.setVisible(False)
        self.ai_status.setVisible(True)
        self.ai_status.setText("")
        self.ai_body.setVisible(False)
        self.ai_body.setText("")
        self.ai_source.setVisible(False)
        self.ai_source.setText("")

    def _load(self) -> None:
        q = self.questions[self.index]
        self.answered = False
        self.pending_explanation = ""
        self._ai_index: int | None = None
        self.shown_at = time.monotonic()
        self.progress.setText(
            f"Question {self.index + 1} of {len(self.questions)}"
            f"  ·  {q['topic'].replace('_', ' ')}"
        )
        self.stem.setText(q["stem"])
        self.verdict.setVisible(False)
        self.feedback.setText("")
        self._reset_ai_card()
        self.confidence.setCurrentIndex(0)
        self.confidence.setEnabled(True)
        self.explain_btn.setEnabled(True)
        self.explain_btn.setText("Self-explain (optional)")
        self.action_btn.setText("Submit answer")
        self._clear_options()
        for i, opt in enumerate(q["options"]):
            radio = QRadioButton(f"{chr(65 + i)}.  {opt}")
            self.group.addButton(radio, i)
            self.options_box.addWidget(radio)

    def _self_explain(self) -> None:
        dialog = _ExplainDialog(self.mw)
        if dialog.exec():
            self.pending_explanation = dialog.text()
            self.explain_btn.setText("Reasoning captured — edit")

    def _on_action(self) -> None:
        if not self.answered:
            self._submit()
        else:
            self._next()

    def _submit(self) -> None:
        selected = self.group.checkedId()
        if selected < 0:
            tooltip("Pick an answer first.")
            return
        q = self.questions[self.index]
        correct = selected == q["correct_index"]
        if correct:
            self.correct_count += 1
        took_ms = max(int((time.monotonic() - self.shown_at) * 1000), 0)
        predicted = self._CONFIDENCE.get(self.confidence.currentIndex())

        req = speedrun_pb2.RecordAttemptRequest(
            card_id=q["card_id"],
            note_id=0,
            session_id=_state.session_id,
            answered_at_ms=int(time.time() * 1000),
            took_ms=took_ms,
            question_type=2,  # discrete exam-style question
            selected=selected,
            correct=correct,
            signals=speedrun_pb2.ClassifyAttemptRequest(
                correct=correct,
                took_ms=took_ms,
                recall_failed=False,
                passage_evidence_missed=False,
                question_type=2,
            ),
            data=json.dumps({"self_explanation": self.pending_explanation}),
        )
        if predicted is not None:
            req.predicted = predicted

        diagnosis = None
        try:
            diagnosis = self.mw.col._backend.record_attempt(req).diagnosis
        except Exception as exc:
            tooltip(f"Could not record attempt: {exc}")

        self.answered = True
        for button in self.group.buttons():
            button.setEnabled(False)
        self.confidence.setEnabled(False)
        self.explain_btn.setEnabled(False)

        # D7 experiment: hold back correctness (and the answer/diagnosis) until the
        # feedback report; the attempt is still recorded above so the report can
        # reveal it later. Novices and the AI-off default are unaffected.
        if self._withhold_feedback:
            _mark(self.verdict, role="muted")
            self.verdict.setText("Answer banked")
            self.verdict.setVisible(True)
            self._repolish(self.verdict)
            self.feedback.setText(
                "Delayed feedback is on. You'll find out whether you were right in "
                "your feedback report \u2014 try to re-derive it before then."
            )
            self.action_btn.setText(
                "Finish" if self.index + 1 >= len(self.questions) else "Next question"
            )
            return

        ci = q["correct_index"]
        # Highlight the correct option (green) and, on a miss, the user's pick (red).
        correct_btn = self.group.button(ci)
        if correct_btn is not None:
            correct_btn.setProperty("srState", "correct")
            self._repolish(correct_btn)
        if not correct:
            wrong_btn = self.group.button(selected)
            if wrong_btn is not None:
                wrong_btn.setProperty("srState", "wrong")
                self._repolish(wrong_btn)

        # Coloured verdict headline (performance-green / danger-red).
        _mark(self.verdict, role=("good" if correct else "bad"))
        self.verdict.setText("Correct" if correct else "Not quite")
        self.verdict.setVisible(True)
        self._repolish(self.verdict)

        parts = [f"Answer: {chr(65 + ci)}. {q['options'][ci]}"]
        if q["explanation"]:
            parts.append(q["explanation"])
        if diagnosis is not None and not correct and diagnosis.kind in _DIAGNOSIS_LABEL:
            parts.append(_DIAGNOSIS_LABEL[diagnosis.kind])
            action = _ACTION_LABEL.get(diagnosis.routed_action, "")
            if action:
                parts.append(action)
        self.feedback.setText("\n".join(parts))
        self.action_btn.setText(
            "Finish" if self.index + 1 >= len(self.questions) else "Next question"
        )
        self._maybe_ai_diagnose(q, selected, took_ms, predicted, correct)

    def _maybe_ai_diagnose(self, q, selected, took_ms, predicted, correct) -> None:
        """Non-blocking: enrich a miss with the source-grounded AI coach if it's
        enabled + available. The deterministic diagnosis is already shown; the AI
        result lands in its own card region (spinner while it runs, then rationale
        + a Source citation), so it never overwrites the rule-based feedback."""
        if correct or self.mw.col is None:
            return
        if not (srai.enabled(self.mw.col) and srai.available()):
            return
        item = {
            "topic": q["topic"],
            "stem": q["stem"],
            "options": q["options"],
            "correct_index": q["correct_index"],
            "selected_index": selected,
            "explanation": q["explanation"],
        }
        signals = {
            "took_ms": took_ms,
            "confidence": float(predicted or 0.0),
            "question_type": 2,
        }
        self._ai_index = self.index
        # Show the AI card with a spinner/skeleton while the coach runs.
        self.ai_card.setVisible(True)
        self.ai_spinner.setVisible(True)
        self.ai_status.setVisible(True)
        self.ai_status.setText("Analyzing your miss against the source\u2026")
        self.ai_body.setVisible(False)
        self.ai_source.setVisible(False)
        srai.diagnose_in_background(self.mw, item, signals, self._on_ai_diagnosis)

    def _on_ai_diagnosis(self, result) -> None:
        # Ignore stale callbacks (the user advanced or closed the dialog).
        if getattr(self, "_ai_index", None) != self.index or not self.answered:
            return
        self.ai_spinner.setVisible(False)
        if not result or result.get("error"):
            self.ai_status.setText(
                "AI coach unavailable \u2014 using the rule-based diagnosis."
            )
            return
        if result.get("abstained"):
            self.ai_status.setText(
                "Not confident enough \u2014 deferring to the rule-based diagnosis."
            )
            return
        name = (result.get("kind_name") or "").replace("_", " ").strip()
        rationale = (result.get("rationale") or "").strip()
        source = (result.get("source") or "").strip()
        self.ai_status.setVisible(False)
        self.ai_body.setText(
            f"{name}: {rationale}" if name else rationale or "No rationale returned."
        )
        self.ai_body.setVisible(True)
        if source:
            self.ai_source.setText(f"Source: {source}")
            self.ai_source.setVisible(True)

    def _next(self) -> None:
        self.index += 1
        if self.index >= len(self.questions):
            tooltip(
                f"Practice complete: {self.correct_count}/{len(self.questions)} correct. "
                "These now feed your performance signal and calibration."
            )
            _refresh(self.mw)
            self.accept()
            return
        self._load()


# --- exam target ------------------------------------------------------------


def _set_exam_target(mw: aqt.AnkiQt) -> None:
    if mw.col is None:
        return
    dialog = QDialog(mw)
    dialog.setWindowTitle("Speedrun exam target")
    disable_help_button(dialog)

    date_edit = QDateEdit()
    date_edit.setCalendarPopup(True)
    date_edit.setDisplayFormat("yyyy-MM-dd")
    date_edit.setDate(QDate.currentDate().addDays(90))

    score_spin = QSpinBox()
    score_spin.setRange(472, 528)
    score_spin.setValue(508)

    try:
        profile = mw.col._backend.get_exam_profile()
        if profile.exam_date_ms > 0:
            dt = datetime.fromtimestamp(profile.exam_date_ms / 1000, tz=timezone.utc)
            date_edit.setDate(QDate(dt.year, dt.month, dt.day))
        if profile.target_score > 0:
            score_spin.setValue(profile.target_score)
    except Exception:
        pass

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    qconnect(buttons.accepted, dialog.accept)
    qconnect(buttons.rejected, dialog.reject)
    ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
    if ok_btn is not None:
        _mark(ok_btn, primary=True)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(*_DIALOG_MARGINS)
    layout.setSpacing(10)
    layout.addWidget(_mark(QLabel("Exam target"), role="display"))
    layout.addWidget(
        _mark(QLabel("Anchor your plan to a date and a target score."), role="muted")
    )
    layout.addWidget(_mark(QLabel("Exam date"), role="eyebrow"))
    layout.addWidget(date_edit)
    layout.addWidget(_mark(QLabel("Target score (472–528)"), role="eyebrow"))
    layout.addWidget(score_spin)
    layout.addWidget(buttons)
    _style_dialog(dialog)

    if not dialog.exec():
        return
    qd = date_edit.date()
    dt = datetime(qd.year(), qd.month(), qd.day(), tzinfo=timezone.utc)
    exam_ms = int(dt.timestamp() * 1000)
    try:
        mw.col._backend.set_exam_profile(
            speedrun_pb2.ExamProfile(
                exam_date_ms=exam_ms, target_score=score_spin.value()
            )
        )
        _refresh(mw, "Exam target saved.")
    except Exception as exc:
        tooltip(f"Could not save exam target: {exc}")
