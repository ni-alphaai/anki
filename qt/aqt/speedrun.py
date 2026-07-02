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

import aqt
from anki import speedrun_pb2
from anki.cards import Card
from aqt import (
    gui_hooks,
    speedrun_library as library,
    speedrun_theme as theme,
    speedrun_voice as voice,
)
from aqt.qt import (
    QAction,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDate,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QObject,
    QPlainTextEdit,
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
_REASONING_ROUND_SIZE = 5

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
# on reveal (self-explanation is pre-reveal). Styled to match the Speedrun look.
_EXPLAIN_BUTTON_JS = (
    r"""
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
    "padding:10px 18px;border-radius:980px;border:none;" +
    "background:#5E5CE6;color:#fff;box-shadow:0 4px 14px rgba(94,92,230,.4);" +
    "font:600 14px -apple-system,system-ui,sans-serif;cursor:pointer;";
  btn.addEventListener("click", function () { pycmd("__CMD__"); });
  document.body.appendChild(btn);
})();
""".replace("__CMD__", _EXPLAIN_CMD)
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
    gui_hooks.reviewer_did_show_question.append(_on_show_question)
    gui_hooks.reviewer_did_show_answer.append(_on_show_answer)
    gui_hooks.reviewer_did_answer_card.append(_on_answer_card)
    gui_hooks.reviewer_will_end.append(_on_reviewer_will_end)
    gui_hooks.webview_did_receive_js_message.append(_on_js_message)
    # Tear the dashboard's webview down before Qt shutdown to avoid a
    # QtWebEngine teardown segfault from a webview still alive at exit.
    gui_hooks.profile_will_close.append(_cleanup_dashboard)

    # In-place native integration.
    gui_hooks.webview_will_set_content.append(_on_will_set_content)
    gui_hooks.deck_browser_will_render_content.append(_on_deck_browser_content)
    gui_hooks.overview_will_render_content.append(_on_overview_content)
    gui_hooks.top_toolbar_did_init_links.append(_on_toolbar_links)
    gui_hooks.reviewer_will_init_answer_buttons.append(_on_answer_buttons)
    # The toolbar's first draw (finish_ui_setup) fires top_toolbar_did_init_links
    # BEFORE this setup() registers our handler, so the Dashboard link is missed.
    # Redraw once after the window is fully initialized to include it.
    gui_hooks.main_window_did_init.append(_redraw_toolbar)

    gui_hooks.collection_did_load.append(_on_collection_loaded)

    menu = QMenu("&Speedrun", mw)
    mw.form.menuTools.addMenu(menu)

    home = QAction("Speedrun home", mw)
    qconnect(home.triggered, lambda: _open_home())
    menu.addAction(home)

    practice = QAction("Practice questions", mw)
    qconnect(practice.triggered, lambda: _start_practice(mw))
    menu.addAction(practice)

    lib = QAction("Content library…", mw)
    qconnect(lib.triggered, lambda: library.open_library(mw))
    menu.addAction(lib)

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

    def first_run() -> None:
        library.maybe_load_example_deck(mw)
        library.maybe_show_onboarding(mw)

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

    Overview._show_finished_screen = patched


def _modern_on(col) -> bool:
    try:
        return bool(col.get_config(_CFG_MODERN, True))
    except Exception:
        return True


def _style_dialog(dialog: QDialog) -> None:
    """Apply the Speedrun token palette to a native Qt dialog (light/dark aware)."""
    try:
        from aqt.theme import theme_manager

        night = bool(theme_manager.night_mode)
    except Exception:
        night = False
    dialog.setStyleSheet(theme.dialog_qss(night))


def _mark(widget: QWidget, *, role: str | None = None, primary: bool = False) -> QWidget:
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
    """Inject the opt-in Apple/3.0 reskin into Anki's own chrome (not the card)."""
    mw = aqt.mw
    if mw is None or mw.col is None or not _modern_on(mw.col):
        return
    from aqt.deckbrowser import DeckBrowser
    from aqt.overview import Overview
    from aqt.reviewer import ReviewerBottomBar
    from aqt.toolbar import TopToolbar

    if isinstance(context, (DeckBrowser, Overview)):
        web_content.head += theme.reskin_style("screen")
    elif isinstance(context, TopToolbar):
        web_content.head += theme.reskin_style("toolbar")
    elif isinstance(context, ReviewerBottomBar):
        # Color-code + restyle the answer buttons (chrome, not the card).
        web_content.head += theme.answer_buttons_css()


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


def _on_deck_browser_content(deck_browser, content) -> None:
    """Embed the compact readiness banner on the deck-list home."""
    mw = aqt.mw
    if mw is None or mw.col is None:
        return
    try:
        content.stats += theme.banner_html(_collect(mw.col, fresh=False))
    except Exception as exc:  # pragma: no cover - never break the home screen
        print(f"speedrun: deck-browser render failed: {exc}")


def _on_overview_content(overview, content) -> None:
    """Embed the full readiness panel on the per-deck overview."""
    mw = aqt.mw
    if mw is None or mw.col is None:
        return
    try:
        content.table += theme.panel_html(_collect(mw.col, fresh=True))
    except Exception as exc:  # pragma: no cover - never break the overview
        print(f"speedrun: overview render failed: {exc}")


def _on_toolbar_links(links, toolbar) -> None:
    """Add a top-toolbar Dashboard entry (a primary destination, alongside Decks/
    Add/Browse/Stats/Sync) so the readiness view is reachable without opening a
    specific deck. Readiness is surfaced in the tooltip to keep the label clean."""
    mw = aqt.mw
    if mw is None or mw.col is None:
        return
    tip = "Speedrun readiness dashboard"
    try:
        snap = mw.col._backend.get_readiness_snapshot()
        if snap.sufficient:
            tip = f"Speedrun dashboard \u2014 projected MCAT {snap.readiness_scaled}"
    except Exception:
        pass
    links.append(
        toolbar.create_link(
            "speedrun",
            "Dashboard",
            lambda: _open_dashboard(mw),
            tip=tip,
            id="speedrun",
        )
    )


# --- data collection --------------------------------------------------------


def _fmt_ms(ms: int) -> str:
    if not ms or ms <= 0:
        return "just now"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000))


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
    if exam.get("has") and exam.get("readiness_sufficient") and not exam.get("on_track"):
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
            web.eval(_EXPLAIN_BUTTON_JS)
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
    """Quiet, native post-miss cue: name the failure mode + the next action."""
    if correct or diagnosis.kind not in _DIAGNOSIS_LABEL:
        return
    label = _DIAGNOSIS_LABEL[diagnosis.kind]
    action = _ACTION_LABEL.get(diagnosis.routed_action, "")
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
        _start_practice(mw)
    elif message == "speedrun:library":
        library.open_library(mw)
    elif message == "speedrun:settings":
        _open_settings(mw)
    elif message == "speedrun:dashboard":
        _open_dashboard(mw)
    elif message == "speedrun:decks":
        _open_home()
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
        _refresh_dashboard()
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
    tooltip("On." if not current else "Off.")


def _refresh(mw: aqt.AnkiQt, msg: str | None = None) -> None:
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
    _refresh_dashboard()
    if msg:
        tooltip(msg)


# --- settings (config toggles, moved out of the panel) ----------------------


def _open_settings(mw: aqt.AnkiQt) -> None:
    """The study levers + appearance, moved off the panel to keep it uncluttered."""
    col = mw.col
    if col is None:
        return
    dialog = QDialog(mw)
    dialog.setWindowTitle("Speedrun settings")
    disable_help_button(dialog)
    dialog.resize(480, 420)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 16)
    layout.setSpacing(8)
    layout.addWidget(_mark(QLabel("Settings"), role="display"))
    layout.addWidget(
        _mark(QLabel("Study levers and appearance. Changes apply immediately."), role="muted")
    )

    def add_toggle(cfg: str, default: bool, title: str, desc: str) -> None:
        check = QCheckBox(title)
        check.setChecked(bool(col.get_config(cfg, default)))
        qconnect(check.stateChanged, lambda _s, c=cfg, cb=check: col.set_config(c, cb.isChecked()))
        sub = _mark(QLabel(desc), role="muted")
        sub.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(check)
        layout.addWidget(sub)

    add_toggle(
        _CFG_POINTS, False, "Points-at-stake queue",
        "Order due cards by weakness \u00d7 topic weight, so the highest-value cards come first.",
    )
    add_toggle(
        _CFG_INTERLEAVE, False, "Spaced + interleaved practice",
        "Interleave confusable sibling topics (same parent tag) across reviews and new cards.",
    )
    add_toggle(
        _CFG_AUTO_ROUND, False, "Auto reasoning round",
        "After you finish a deck's reviews, jump straight into a reasoning check (otherwise it's offered).",
    )
    add_toggle(
        _CFG_MODERN, True, "Modern UI",
        "Apple-style reskin of Anki's deck list, overview, and toolbar.",
    )

    layout.addStretch(1)
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    qconnect(box.rejected, dialog.reject)
    layout.addWidget(box)
    _style_dialog(dialog)
    dialog.exec()
    # Apply once on close: config affects the queue and the reskin.
    try:
        mw.reset()
    except Exception:
        pass
    _refresh(mw)


# --- dashboard (top-toolbar window, always available) -----------------------

_dashboard: _DashboardDialog | None = None


class _DashboardDialog(QDialog):
    """A standalone Speedrun dashboard, opened from the top toolbar like Stats.
    Renders the same signals as the panel but independent of any deck, so it is
    reachable even when every deck is finished (no congrats-page dead-end)."""

    def __init__(self, mw: aqt.AnkiQt) -> None:
        super().__init__(mw)
        from aqt.webview import AnkiWebView

        self.mw = mw
        self.setWindowTitle("Speedrun")
        disable_help_button(self)
        self.resize(860, 760)
        self.web = AnkiWebView(mw)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web)
        self.web.set_bridge_command(self._on_cmd, self)
        self.render()

    def render(self) -> None:
        if self.mw.col is None:
            return
        try:
            data = _collect(self.mw.col, fresh=True)
            self.web.stdHtml(theme.dashboard_html(data), context=self)
        except Exception as exc:  # pragma: no cover
            print(f"speedrun: dashboard render failed: {exc}")

    def _on_cmd(self, message: str) -> object:
        if isinstance(message, str) and message.startswith("speedrun:"):
            _on_js_message((False, None), message, self)
            # reflect any state change (an action dialog just closed)
            self.render()
        return None

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        global _dashboard
        _dashboard = None
        try:
            self.web.cleanup()
        except Exception:
            pass
        super().closeEvent(event)


def _redraw_toolbar() -> None:
    """Re-render the top toolbar so the Dashboard link (registered after the
    initial draw) appears. Safe no-op if the toolbar isn't ready."""
    mw = aqt.mw
    if mw is None:
        return
    try:
        mw.toolbar.draw()
    except Exception:  # pragma: no cover - never block startup
        pass


def _open_dashboard(mw: aqt.AnkiQt) -> None:
    global _dashboard
    if mw.col is None:
        return
    if _dashboard is None:
        _dashboard = _DashboardDialog(mw)
    else:
        _dashboard.render()
    _dashboard.show()
    _dashboard.raise_()
    _dashboard.activateWindow()


def _refresh_dashboard() -> None:
    if _dashboard is not None:
        try:
            _dashboard.render()
        except Exception:
            pass


def _cleanup_dashboard() -> None:
    """Close + clean the dashboard webview before app/profile shutdown."""
    global _dashboard
    dialog, _dashboard = _dashboard, None
    if dialog is not None:
        try:
            dialog.close()
        except Exception:
            pass


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
        self.edit.setPlaceholderText("Your spoken reasoning appears here — or just type.")
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
        layout.setContentsMargins(20, 18, 20, 16)
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
                    self.edit.setPlainText((current + " " + text).strip() if current else text)

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


def _start_practice(mw: aqt.AnkiQt) -> None:
    """Open the held-out question-practice dialog (performance signal loop)."""
    if mw.col is None:
        return
    try:
        raw = list(mw.col._backend.get_practice_questions(limit=20, topic=""))
    except Exception as exc:
        tooltip(f"Could not load practice questions: {exc}")
        return
    questions = _parse_question_items(raw)
    if not questions:
        tooltip("No practice questions yet — import a question pack to begin.")
        return
    _PracticeDialog(mw, questions).exec()


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
    layout.setContentsMargins(22, 20, 22, 16)
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

        self.feedback = _mark(QLabel(), role="muted")
        self.feedback.setWordWrap(True)

        self.action_btn = _mark(QPushButton("Submit answer"), primary=True)
        qconnect(self.action_btn.clicked, self._on_action)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        qconnect(close_box.rejected, self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(self.progress)
        layout.addWidget(self.stem)
        layout.addWidget(options_container)
        layout.addLayout(conf_row)
        layout.addWidget(self.explain_btn)
        layout.addWidget(self.feedback)
        layout.addWidget(self.action_btn)
        layout.addWidget(close_box)
        _style_dialog(self)

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

    def _load(self) -> None:
        q = self.questions[self.index]
        self.answered = False
        self.pending_explanation = ""
        self.shown_at = time.monotonic()
        self.progress.setText(
            f"Question {self.index + 1} of {len(self.questions)}"
            f"  ·  {q['topic'].replace('_', ' ')}"
        )
        self.stem.setText(q["stem"])
        self.feedback.setText("")
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

        ci = q["correct_index"]
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
    layout.setContentsMargins(20, 18, 20, 16)
    layout.setSpacing(10)
    layout.addWidget(_mark(QLabel("Exam target"), role="display"))
    layout.addWidget(_mark(QLabel("Anchor your plan to a date and a target score."), role="muted"))
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
            speedrun_pb2.ExamProfile(exam_date_ms=exam_ms, target_score=score_spin.value())
        )
        _refresh(mw, "Exam target saved.")
    except Exception as exc:
        tooltip(f"Could not save exam target: {exc}")
