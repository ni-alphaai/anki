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
import re
import time
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

import aqt
from anki import speedrun_pb2
from anki.cards import Card, CardId
from anki.collection import OpChangesWithId
from anki.utils import is_mac
from aqt import gui_hooks, speedrun_notesync
from aqt import speedrun_ai as srai
from aqt import speedrun_grouping as grouping
from aqt import speedrun_library as library
from aqt import speedrun_mcat as mcat
from aqt import speedrun_sync as srsync
from aqt import speedrun_theme as theme
from aqt import speedrun_voice as voice
from aqt.qt import (
    QAction,
    QApplication,
    QButtonGroup,
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
# Persistent "on conflict" preference for phone sync, mirroring the phone's
# SyncConflictPolicy: "ask" (default) prompts for a direction on a genuine
# two-sided conflict; "phone" / "desktop" auto-resolve toward that side and
# always report which copy was overwritten (never a silent overwrite).
_CFG_SYNC_CONFLICT = "speedrunSyncConflictPolicy"
_SYNC_CONFLICT_DEFAULT = "ask"
_SYNC_CONFLICT_CHOICES = ("ask", "phone", "desktop")
# A student at/above this overall exam-style accuracy counts as proficient for
# the withhold experiment (mirrors the engine's PROFICIENT_THRESHOLD).
_PROFICIENT_THRESHOLD = 0.8
# Client-only flag: the first-run onboarding placement quiz has been offered.
_CFG_DIAGNOSTIC = "speedrunDiagnosticDone"
_REASONING_ROUND_SIZE = 5


def _should_withhold_feedback(performance: float, enabled: bool) -> bool:
    """D7: withhold immediate correctness only when the experiment is enabled AND
    the student is already proficient. Mirrors the engine's
    ``should_withhold_correctness``; pure so it can be unit-tested."""
    return enabled and performance >= _PROFICIENT_THRESHOLD


_QUESTION_TYPE_SRS = 0
# Passage MCQ (CARS): misses classify as reasoning/passage, never "memory".
_QUESTION_TYPE_PASSAGE = 1
# Discrete content-linked exam-style question.
_QUESTION_TYPE_DISCRETE = 2

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
    "position:fixed;bottom:16px;right:16px;z-index:2147483647;" +
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
        # The most recently answered card (the one a diagnosis cue refers to).
        self.last_answered_card_id: int | None = None
        # Cards the user flagged via the cue's "Practice later" to practice at
        # the end of the session (instead of abandoning the review mid-card).
        self.flagged_card_ids: list[int] = []
        # The ephemeral filtered deck a "Review memory cards" session is running
        # in, and the Speedrun screen to return to when it ends. Set while a
        # topic review is live so the native "special deck" overview never shows.
        self.review_filtered_deck_id: int | None = None
        self.review_return_topic: str | None = None


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


def _guard_macos_cursor_crash() -> None:
    """Stop an intermittent whole-app crash on macOS 26 (Tahoe) + Anki's bundled
    Qt: ``QApplication.setOverrideCursor`` can route a cursor through
    ``QImage::toCGImage`` with an invalid CoreGraphics colorspace and SIGTRAP the
    process (seen from the busy spinner and the hover pointing-hand cursor). It's
    a native crash Python can't catch, and the override cursor is purely
    cosmetic, so neutralize it on macOS. No-op elsewhere."""
    if not is_mac:
        return
    QApplication.setOverrideCursor = staticmethod(  # type: ignore[assignment]
        lambda *a, **k: None
    )
    QApplication.restoreOverrideCursor = staticmethod(  # type: ignore[assignment]
        lambda *a, **k: None
    )


def setup(mw: aqt.AnkiQt) -> None:
    """Register the reviewer hooks, native-screen hooks, and a slim menu."""
    _guard_macos_cursor_crash()
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
    if os.environ.get("SR_THEME_DEBUG"):
        print(
            "SR_DEBUG setup complete: webview_will_set_content registered", flush=True
        )
    # Tear the embedded sync server down when the profile closes (atexit is a
    # backstop for hard exits).
    gui_hooks.profile_will_close.append(srsync.stop_server)
    # Note-encode Speedrun's sr_attempts around EVERY native sync (incl. AnkiWeb),
    # so exam-practice evidence rides the standard note sync a stock peer keeps
    # rather than the custom chunk it drops. The local phone-sync path calls the
    # same encode/decode inline (it bypasses these lifecycle hooks); both
    # directions are idempotent, so nothing is double-encoded or double-applied.
    gui_hooks.sync_will_start.append(_on_sync_will_start)
    gui_hooks.sync_did_finish.append(_on_sync_did_finish)
    # Focus auto-sync was intentionally removed: it fired Anki's native
    # "Syncing..." modal and reset to the native deck browser on every window
    # refocus, interrupting the user. Sync now runs only on the explicit Sync
    # action (toolbar button or the Sync-with-phone screen).

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

    diagnostic = QAction("Run placement diagnostic…", mw)
    qconnect(diagnostic.triggered, lambda: _start_diagnostic(mw))
    menu.addAction(diagnostic)

    setup_action = QAction("Set up Speedrun…", mw)
    qconnect(setup_action.triggered, lambda: library.open_onboarding(mw))
    menu.addAction(setup_action)

    ankiweb = QAction("Sync to AnkiWeb (cloud)…", mw)
    qconnect(ankiweb.triggered, lambda: _sync_to_ankiweb(mw))
    menu.addAction(ankiweb)


def _on_collection_loaded(_col) -> None:
    """First-run setup, deferred so it never blocks collection load: seed the
    bundled example deck on an empty collection, then offer onboarding."""
    from aqt.qt import QTimer

    mw = aqt.mw
    if mw is None:
        return

    # Install the deck-browser override now that mw.deckBrowser exists. It is
    # created in setupDeckBrowser, which runs AFTER setup()/setupHooks, so
    # installing it in setup() was a no-op (mw.deckBrowser was still None).
    # collection_did_load fires just before the first moveToState("deckBrowser"),
    # so wrapping show/refresh here means the themed dashboard owns the
    # deck-browser surface from the first paint, and every later reset()/refresh()
    # re-renders it instead of letting Anki paint the native deck list.
    _install_deck_browser_override(mw)

    _scrub_stale_sync_urls(mw.pm)

    # Anki's first setupStyle() ran before our style_did_init hook registered, so
    # rebuild the app stylesheet now that the collection (and toggle) are loaded.
    _reapply_app_style()

    def first_run() -> None:
        library.maybe_load_example_deck(mw)
        library.maybe_show_onboarding(mw)
        # Reveal the app-shell sidebar and land on Home (the readiness
        # dashboard), so the app opens on its value surface rather than a bare
        # deck list. Deferred with the rest of first-run so Anki's own startup
        # state has settled first. If the placement diagnostic is offered, it
        # owns the webview instead of the dashboard.
        _apply_shell_visibility(mw)
        if mw.col is not None and _modern_on(mw.col):
            if not maybe_start_diagnostic(mw):
                _show_workspace(mw, "dashboard")
        # No auto-sync on boot: syncing to a server that is not hosting yet threw
        # a "Processing..." modal and a "couldn't reach the sync server" error
        # over the startup screen. Sync runs only on the explicit Sync action.

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


def _install_deck_browser_override(mw: aqt.AnkiQt) -> None:
    """With the modern shell on, the deck-browser *state* is owned by the
    Speedrun home surface (the themed dashboard, rendered as an overlay on
    ``mw.web``). Anki repaints the native deck list on ``moveToState(
    "deckBrowser")`` -> ``DeckBrowser.show`` and on ``mw.reset()`` ->
    ``DeckBrowser.refresh`` (e.g. the ``mw.reset()`` in ``aqt/sync.py`` right
    after a sync, or when a review finishes). Those paint the native list over
    the themed screen, dropping the user onto Anki's deck list with the gauge
    hidden behind it (so readiness looks "stuck"). Wrap both so they re-render
    the current Speedrun workspace instead, which also re-collects readiness
    (``fresh=True``) so a post-sync refresh shows updated numbers. Falls through
    to the native browser when the modern UI is off."""
    db = getattr(mw, "deckBrowser", None)
    if db is None or getattr(db, "_speedrun_wrapped", False):
        return

    def _themed(orig):
        def wrapped(*args, **kwargs):
            col = mw.col
            if col is not None and _modern_on(col):
                try:
                    if _ws_active == "sync":
                        # The QR pairing screen isn't a _show_workspace tab; a
                        # sync fires mw.reset() mid-pairing, so keep the user on
                        # it rather than bouncing to the dashboard.
                        _show_sync_pair(mw, start=False)
                    else:
                        _show_workspace(mw, _ws_active or "dashboard")
                    return None
                except Exception as exc:  # pragma: no cover - never block paint
                    print(f"speedrun: themed deck-browser render failed: {exc}")
            return orig(*args, **kwargs)

        return wrapped

    db.show = _themed(db.show)  # type: ignore[method-assign]
    db.refresh = _themed(db.refresh)  # type: ignore[method-assign]
    db._speedrun_wrapped = True


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
    if os.environ.get("SR_THEME_DEBUG"):
        print(
            f"SR_DEBUG wsc ctx={type(context).__name__} "
            f"col={None if mw is None else (mw.col is not None)} "
            f"state={None if mw is None else mw.state}",
            flush=True,
        )
    if mw is None:
        return
    from aqt.deckbrowser import DeckBrowser
    from aqt.overview import Overview
    from aqt.reviewer import Reviewer, ReviewerBottomBar
    from aqt.toolbar import TopToolbar

    # The collection may not be loaded yet on the very first deck-browser paint;
    # the reskin defaults on, so assume on until the toggle is readable rather
    # than letting that first render fall back to the unstyled native chrome.
    modern = _modern_on(mw.col) if mw.col is not None else True
    if isinstance(context, (DeckBrowser, Overview)):
        # The banner / panel / finished embeds always render, so their tokens +
        # component styles must always be present -- injected once per page.
        web_content.head += theme.page_style()
        if modern:
            web_content.head += theme.screen_reskin()
        if os.environ.get("SR_THEME_DEBUG"):
            print(
                f"SR_DEBUG injected deck/overview ctx={type(context).__name__} "
                f"modern={modern}",
                flush=True,
            )
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
    # A single consistent scale from a lapse to an easy recall: "Forgot" for the
    # Again lapse, then Anki's Hard / Good / Easy difficulty words (the old mix of
    # "Forgot"/"Got it" with "Hard"/"Easy" read as two different vocabularies).
    remap = {
        tr.studying_again(): "Forgot",
        tr.studying_hard(): "Hard",
        tr.studying_good(): "Good",
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


def _show_feedback_report(mw: aqt.AnkiQt) -> None:
    """Render the end-of-session feedback report as a Speedrun screen (tokened
    cards) in the main webview, so it matches the rest of the app shell."""
    global _ws_active
    if mw.col is None:
        return
    fb = _feedback_report(mw.col)
    if fb is None:
        tooltip("Feedback report unavailable.")
        return
    _ws_active = "report"
    _render_ws(mw, theme.feedback_report_body(fb))
    _render_sidebar(mw)


def _collect(col, fresh: bool) -> dict:
    """Gather every Speedrun signal into a plain dict for the theme builders."""
    backend = col._backend
    snap = backend.compute_readiness() if fresh else backend.get_readiness_snapshot()
    cov = backend.get_coverage_report()

    # Exam-style attempts answered so far, so the UI can show progress toward the
    # performance threshold ("N/20") instead of a dead-end "no questions yet".
    exam_attempts = 0
    try:
        exam_attempts = int(backend.get_performance_report().exam_attempts)
    except Exception:
        pass

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
        "exam_attempts": exam_attempts,
        "exam_needed": 20,  # readiness.rs MIN_EXAM_ATTEMPTS
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
    data["due_today"] = _due_today_count(col)
    data["next_action"] = _next_action(data)
    return data


def _due_today_count(col) -> int:
    """Cards ready to study today (new + learning + due review) across the whole
    collection, from the scheduler's due tree. Top-level nodes already aggregate
    their subdecks, so summing them avoids double-counting. Drives the dashboard's
    'review today' recommendation."""
    try:
        tree = col.sched.deck_due_tree()
        return sum(
            int(c.new_count) + int(c.learn_count) + int(c.review_count)
            for c in getattr(tree, "children", [])
        )
    except Exception:
        return 0


# Per-topic evidence gates: much lower than the global readiness gates, since a
# single content category accumulates far less evidence than the whole deck. Used
# only to phrase a topic's status honestly, never to fabricate a score.
_TOPIC_MIN_ATTEMPTS = 3


def _topic_status(t: dict) -> tuple[str, str]:
    """A short status label + colour kind for one topic, from its raw counts."""
    if t["cards"] == 0:
        return ("Not in your decks", "muted")
    if t["review"] == 0 and t["attempts"] == 0:
        return ("Not started", "muted")
    perf = t["performance"]
    if t["attempts"] >= _TOPIC_MIN_ATTEMPTS and perf is not None and perf < 0.55:
        return ("Needs work", "danger")
    mem = t["memory"]
    if mem is not None and mem >= 0.7 and (perf is None or perf >= 0.65):
        return ("Strong", "perf")
    return ("Building", "memory")


def _collect_topic_dashboard(mw) -> dict:
    """Per-topic coverage/memory/performance grouped by MCAT section, for the
    topic-centric Home dashboard and the per-topic drill-in. Backed by the
    engine's GetTopicSignals (raw counts); fractions + status derived here."""
    col = mw.col
    try:
        signals = list(col._backend.get_topic_signals())
    except Exception:
        signals = []
    # Unlinked (cid=0) practice attempts - the MMLU bank, CARS passages - grouped
    # by their stored subject topic, folded into each MCAT section's performance
    # so a section backed only by unlinked practice no longer reads "Perf -".
    section_extra: dict[str, list[int]] = {}
    try:
        for st in col._backend.get_topic_attempt_stats():
            key = mcat.section_key_for_subject(str(st.topic)) or "other"
            acc = section_extra.setdefault(key, [0, 0])
            acc[0] += int(st.attempts)
            acc[1] += int(st.correct)
    except Exception:
        pass
    meta = library.topic_meta()

    topics: dict[str, dict] = {}
    for s in signals:
        review, mature = int(s.review_cards), int(s.mature_cards)
        attempts, correct = int(s.exam_attempts), int(s.exam_correct)
        m = meta.get(s.topic, {})
        sec = _section_for_topic(s.topic)
        t = {
            "id": s.topic,
            "name": m.get("name") or s.label or s.topic,
            "section_key": sec["key"] if sec else "",
            "section": sec["short"] if sec else "",
            "subject": m.get("subject", ""),
            "weight": float(m.get("weight", s.weight)),
            "cards": int(s.cards),
            "covered": bool(s.covered),
            "review": review,
            "mature": mature,
            "attempts": attempts,
            "correct": correct,
            "memory": (mature / review) if review else None,
            "performance": (correct / attempts) if attempts else None,
        }
        t["status"], t["kind"] = _topic_status(t)
        topics[s.topic] = t

    sections: list[dict] = []
    for sec in mcat.SECTIONS:
        rows = sorted(
            (t for t in topics.values() if t["section_key"] == sec["key"]),
            key=lambda t: (-t["weight"], t["name"]),
        )
        sections.append(
            _topic_section_summary(
                sec["key"],
                sec["short"],
                sec["full"],
                bool(sec.get("reasoning")),
                rows,
                section_extra.get(sec["key"]),
            )
        )

    other = sorted(
        (t for t in topics.values() if not t["section_key"]),
        key=lambda t: (-t["weight"], t["name"]),
    )
    if other or section_extra.get("other"):
        sections.append(
            _topic_section_summary(
                "other",
                "Other",
                "Uncategorized topics",
                False,
                other,
                section_extra.get("other"),
            )
        )

    general_practice = sum(a for a, _ in section_extra.values())
    return {
        "sections": sections,
        "has_topics": bool(topics) or bool(section_extra),
        "general_practice": general_practice,
    }


def _section_for_topic(topic_id: str) -> dict | None:
    """The MCAT section a topic id belongs to, from its Foundational Concept
    number (leading digits of ``1A``..``10E`` or ``fc1``..``fc10``): FC1-3 ->
    Bio/Biochem, FC4-5 -> Chem/Phys, FC6-10 -> Psych/Soc. Mirrors the mobile
    ``Mcat.sectionForTopic`` so both platforms group topics identically."""
    m = re.match(r"(?:fc)?(\d+)", str(topic_id).lower())
    if not m:
        return None
    fc = int(m.group(1))
    key = (
        "bio_biochem"
        if 1 <= fc <= 3
        else "chem_phys"
        if 4 <= fc <= 5
        else "psych_soc"
        if 6 <= fc <= 10
        else ""
    )
    return next((s for s in mcat.SECTIONS if s["key"] == key), None) if key else None


def _topic_section_summary(
    key: str,
    short: str,
    full: str,
    reasoning: bool,
    rows: list[dict],
    extra: list[int] | None = None,
) -> dict:
    total = len(rows)
    covered = sum(1 for t in rows if t["covered"])
    sum_review = sum(t["review"] for t in rows)
    sum_mature = sum(t["mature"] for t in rows)
    sum_att = sum(t["attempts"] for t in rows)
    sum_cor = sum(t["correct"] for t in rows)
    # Fold in the section's unlinked practice attempts (e.g. the MMLU bank) so
    # per-section performance reflects them without inflating any one topic row.
    if extra:
        sum_att += extra[0]
        sum_cor += extra[1]
    return {
        "key": key,
        "short": short,
        "full": full,
        # CARS: reading/reasoning practice with no content cards -> memory and
        # coverage are N/A (None), never a misleading 0%.
        "reasoning": reasoning,
        "total": total,
        "covered": covered,
        "coverage": None if reasoning else ((covered / total) if total else 0.0),
        "memory": None
        if reasoning
        else ((sum_mature / sum_review) if sum_review else None),
        "performance": (sum_cor / sum_att) if sum_att else None,
        "attempts": sum_att,
        "topics": rows,
    }


def _collect_deck_list(mw) -> dict:
    """Flatten the scheduler's deck-due tree (name / New / Learn / Due) for the
    Speedrun-styled all-decks list."""
    rows: list[dict] = []

    def walk(node: object, depth: int) -> None:
        for child in getattr(node, "children", []):
            rows.append(
                {
                    "id": child.deck_id,
                    "name": child.name,
                    "depth": depth,
                    "new": child.new_count,
                    "learn": child.learn_count,
                    "review": child.review_count,
                }
            )
            if not getattr(child, "collapsed", False):
                walk(child, depth + 1)

    try:
        walk(mw.col.sched.deck_due_tree(), 0)
    except Exception:
        pass
    return {
        "decks": rows,
        "studied": "Your decks, by New / Learn / Due — tap to study.",
    }


def _open_deck(mw: aqt.AnkiQt, deck_id: int) -> None:
    """Select a deck and open its (reskinned) overview to study it."""
    global _ws_active
    try:
        from anki.decks import DeckId

        mw.col.decks.select(DeckId(deck_id))
    except Exception:
        pass
    _ws_active = None
    mw.moveToState("overview")
    _render_sidebar(mw)


def _deck_action(mw: aqt.AnkiQt, arg: str) -> None:
    """Route ``speedrun:deck:*``: the deck-list action buttons + a deck id to open."""
    if arg == "create":
        try:
            from aqt.operations.deck import add_deck_dialog

            if op := add_deck_dialog(parent=mw):
                op.run_in_background()
        except Exception as exc:
            tooltip(f"Could not create deck: {exc}")
    elif arg == "import":
        try:
            mw.onImport()
        except Exception as exc:
            tooltip(f"Could not open import: {exc}")
    elif arg == "shared":
        try:
            from aqt.utils import openLink

            openLink(f"{aqt.appShared}decks/")
        except Exception:
            pass
    else:
        try:
            _open_deck(mw, int(arg))
        except ValueError:
            pass


def _collect_progress(col) -> dict:
    """Gather the Progress screen's chart data: the three signals, calibration
    bins, misses-by-cause, and per-topic coverage."""
    backend = col._backend
    snap = backend.compute_readiness()
    cov = backend.get_coverage_report()
    data: dict = {
        "memory": snap.memory,
        "performance": snap.performance,
        "coverage": snap.coverage,
        "memory_ok": snap.memory_sufficient,
        "perf_ok": snap.performance_sufficient,
        "gap": snap.recall_perf_gap,
        "sufficient": snap.sufficient,
        "cov_total": cov.topics_total,
        "cov_covered": cov.topics_covered,
        "weighted": cov.weighted_coverage,
        "topics": [
            {"label": t.label or t.topic, "covered": t.covered, "weight": t.weight}
            for t in cov.topics
        ],
    }
    try:
        c = backend.get_calibration_report()
        data["calibration"] = {
            "sufficient": c.sufficient,
            "n": c.n,
            "brier": c.brier,
            "logloss": c.log_loss,
            "bins": [
                {
                    "mean_predicted": b.mean_predicted,
                    "mean_outcome": b.mean_outcome,
                    "count": b.count,
                }
                for b in c.bins
            ],
        }
    except Exception:
        data["calibration"] = None
    data["feedback"] = _feedback_report(col)
    return data


def _next_action(data: dict) -> dict:
    """The single recommended step, with the command that performs it so the
    panel's card can be a real button (not inert text)."""
    due = int(data.get("due_today", 0) or 0)
    if not data["sufficient"]:
        seed_cmd = "speedrun:seed" if data.get("cov_total", 0) == 0 else None
        blocking = data.get("blocking", "")
        # Memory + graded-attempt evidence is built by REVIEWING cards, so when
        # anything is ready today the next step is a real review session, not an
        # inert "study more" note the user can't act on.
        if blocking in ("memory", "attempts") and due > 0:
            return {
                "title": "Review today's cards",
                "detail": (
                    f"You have {due} card{'s' if due != 1 else ''} ready to review "
                    "today. Working through them builds the recall evidence your "
                    "readiness score needs."
                ),
                "cmd": "speedrun:review",
                "cta": "Start review",
            }
        # Nothing due today, or a different dimension is blocking: give an
        # honest next step that still routes somewhere real.
        by = {
            "memory": (
                "Add cards to review",
                "Readiness needs graded reviews to estimate your memory signal. "
                "Import a deck or add cards, then review them daily.",
                "speedrun:decks",
                "Browse decks",
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
                "Keep reviewing daily",
                "A handful more graded reviews will unlock your readiness estimate. "
                "Come back as cards fall due, or add more cards.",
                "speedrun:decks",
                "Browse decks",
            ),
        }
        title, detail, cmd, cta = by.get(
            blocking,
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


# One-tap reveal+rate: the rating buttons render on the question front, so a
# single tap reveals the answer, records that rating, and advances (grading from
# memory). Reuses the data-ease styling from _ANSWER_BUTTONS. "Again" is shown as
# "Forgot" to match the answer-state relabel (_on_answer_buttons).
_REVIEW_RATINGS = ((1, "Forgot"), (2, "Hard"), (3, "Good"), (4, "Easy"))


def _reviewer_front_buttons_html() -> str:
    cells = "".join(
        f'<td align=center><button data-ease="{n}" '
        f"onclick='pycmd(\"speedrun:reviewrate:{n}\")'>{label}</button></td>"
        for n, label in _REVIEW_RATINGS
    )
    return f"<table cellpadding=0><tr>{cells}</tr></table>"


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
    # Show the rating buttons on the front so one tap reveals + rates + advances.
    if mw.col is not None and _modern_on(mw.col):
        try:
            mw.reviewer.bottom.web.eval(
                f"showAnswer({json.dumps(_reviewer_front_buttons_html())}, false);"
            )
        except Exception as exc:  # pragma: no cover - never break the review loop
            print(f"speedrun: failed to inject front rating buttons: {exc}")


def _reviewer_reveal_rate(mw: aqt.AnkiQt, ease_str: str) -> None:
    """A one-tap front rating: reveal the answer (flipping to the answer state and
    firing the answer-side hooks) then rate + advance. No-op unless a review is
    active and the ease is 1-4."""
    reviewer = mw.reviewer
    if reviewer is None or mw.state != "review":
        return
    try:
        ease = int(ease_str)
    except ValueError:
        return
    if not 1 <= ease <= 4:
        return
    if reviewer.state != "answer":
        reviewer._showAnswer()
    reviewer._answerCard(ease)  # type: ignore[arg-type]


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
    _state.last_answered_card_id = card.id

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


def _flag_practice(mw: aqt.AnkiQt) -> None:
    """The diagnosis cue's "Practice later": queue the just-missed card for the
    end-of-session reasoning round, instead of navigating away and abandoning the
    review mid-card (the old "Practice this" jumped straight to the Practice tab)."""
    cid = _state.last_answered_card_id
    if cid is not None and cid not in _state.flagged_card_ids:
        _state.flagged_card_ids.append(cid)
    tooltip("Queued — you'll get exam-style questions on this after your session.")


# --- pycmd bridge -----------------------------------------------------------


def _cmd_seed(mw: aqt.AnkiQt) -> None:
    try:
        mw.col._backend.seed_mcat_topic_outline()
        _refresh(mw, "Seeded MCAT topics. Tag cards with a topic to cover it.")
    except Exception as exc:
        tooltip(f"Could not seed outline: {exc}")


def _cmd_refresh(mw: aqt.AnkiQt) -> None:
    try:
        mw.col._backend.compute_readiness()
    except Exception:
        pass
    _refresh(mw)


def _cmd_pq_next(mw: aqt.AnkiQt) -> None:
    if _ws_practice is not None:
        _ws_practice.advance()


def _cmd_pq_quit(mw: aqt.AnkiQt) -> None:
    """Leave an in-progress practice session early, back to the practice landing.

    Each answered question is banked atomically at submit time, so dropping the
    in-memory runner here abandons only the unanswered remainder - there is no
    half-recorded attempt to clean up. Clearing the runner also stops
    ``_show_workspace(mw, "practice")`` from resuming it, so the section landing
    shows instead."""
    global _ws_practice
    _ws_practice = None
    _show_workspace(mw, "practice")


def _cmd_customstudy(mw: aqt.AnkiQt) -> None:
    try:
        from aqt.customstudy import CustomStudy

        CustomStudy.fetch_data_and_show(mw)
    except Exception:
        _open_home()


def _cmd_download_deck(mw: aqt.AnkiQt, arg: str) -> None:
    try:
        library.download_popular_deck(mw, int(arg))
    except ValueError:
        pass


# Exact-match pycmds -> handler(mw). Lambda values defer name resolution to call
# time, so this table can sit next to the router even though many handlers are
# defined further down the module.
_EXACT_CMDS: dict[str, Callable[[aqt.AnkiQt], object]] = {
    _EXPLAIN_CMD: lambda mw: _start_self_explanation(mw),
    "speedrun:seed": _cmd_seed,
    "speedrun:exam": lambda mw: _set_exam_target(mw),
    "speedrun:practice": lambda mw: _show_workspace(mw, "practice"),
    "speedrun:review": lambda mw: _review_today(mw),
    "speedrun:practicelater": lambda mw: _flag_practice(mw),
    "speedrun:report": lambda mw: _show_feedback_report(mw),
    "speedrun:library": lambda mw: _show_workspace(mw, "library"),
    "speedrun:lib:content": lambda mw: library.import_content_library(mw),
    "speedrun:lib:sample": lambda mw: library.seed_sample_history(mw),
    "speedrun:lib:e2e": lambda mw: library.import_e2e_pack(mw),
    "speedrun:lib:mmlu": lambda mw: library.import_mmlu_pack(mw),
    "speedrun:lib:cars": lambda mw: library.import_cars_pack(mw),
    "speedrun:diag:start": lambda mw: _start_diagnostic(mw),
    "speedrun:diag:skip": lambda mw: _skip_diagnostic(mw),
    "speedrun:ai:toggle": lambda mw: _toggle_ai(mw),
    "speedrun:lib:pick": lambda mw: library.pick_file(mw),
    "speedrun:lib:paste": lambda mw: library.paste_link(mw),
    "speedrun:settings": lambda mw: _show_workspace(mw, "settings"),
    "speedrun:dashboard": lambda mw: _show_workspace(mw, "dashboard"),
    "speedrun:decks": lambda mw: _show_workspace(mw, "decks"),
    "speedrun:decks:all": lambda mw: _show_workspace(mw, "alldecks"),
    "speedrun:decks:native": lambda mw: _open_native_decks(mw),
    "speedrun:group": lambda mw: _group_cards(mw),
    "speedrun:back": lambda mw: _leave_workspace(mw),
    "speedrun:syncnow": lambda mw: _sync_now_from_screen(mw),
    "speedrun:syncankiweb": lambda mw: _sync_to_ankiweb(mw),
    "speedrun:ankiwebsignin": lambda mw: _ankiweb_sign_in(mw),
    "speedrun:ankiwebsignout": lambda mw: _ankiweb_sign_out(mw),
    "speedrun:syncrefresh": lambda mw: _refresh_pairing_code(mw),
    "speedrun:syncpull": lambda mw: _sync_pull_from_phone(mw),
    "speedrun:syncpush": lambda mw: _sync_push_to_phone(mw),
    "speedrun:syncusb": lambda mw: _refresh_usb_tunnel(mw),
    "speedrun:syncclear": lambda mw: _clear_for_sync_test(mw),
    "speedrun:pq:next": _cmd_pq_next,
    "speedrun:pq:quit": _cmd_pq_quit,
    "speedrun:pr:home": lambda mw: _show_workspace(mw, "practice"),
    "speedrun:customstudy": _cmd_customstudy,
    "speedrun:refresh": _cmd_refresh,
}

# Prefix pycmds -> handler(mw, remainder). No prefix is a prefix of another, so
# first match wins.
_PREFIX_CMDS: list[tuple[str, Callable[[aqt.AnkiQt, str], object]]] = [
    ("speedrun:lib:deck:", _cmd_download_deck),
    ("speedrun:ws:", lambda mw, a: _show_workspace(mw, a)),
    ("speedrun:nav:", lambda mw, a: _navigate(mw, a)),
    ("speedrun:set:", lambda mw, a: _ws_set(mw, a)),
    ("speedrun:syncpolicy:", lambda mw, a: _set_sync_conflict_policy(mw, a)),
    ("speedrun:sync:", lambda mw, a: _ws_sync(mw, a)),
    ("speedrun:pq:submit:", lambda mw, a: _ws_practice_submit(a)),
    ("speedrun:pq:voice:", lambda mw, a: _ws_practice_voice(mw, a)),
    ("speedrun:theme:", lambda mw, a: _set_theme_mode(mw, a)),
    ("speedrun:deck:", lambda mw, a: _deck_action(mw, a)),
    ("speedrun:section:", lambda mw, a: _show_section(mw, a)),
    ("speedrun:topic:", lambda mw, a: _open_topic(mw, a)),
    ("speedrun:pr:sec:", lambda mw, a: _show_practice_section(mw, a)),
    (
        "speedrun:pr:go:",
        lambda mw, a: _start_practice(mw, [t for t in a.split(",") if t]),
    ),
    ("speedrun:toggle:", lambda mw, a: _toggle(mw, a)),
    ("speedrun:reviewrate:", _reviewer_reveal_rate),
]


def _on_js_message(handled: tuple[bool, object], message: str, context: object):
    """Route Speedrun panel/button actions; leave all other messages alone.

    Dispatches through the exact/prefix tables above so this stays a flat, cheap
    lookup rather than a long branching chain."""
    if not message.startswith("speedrun:"):
        return handled
    mw = aqt.mw
    if mw is None or mw.col is None:
        return (True, None)
    exact = _EXACT_CMDS.get(message)
    if exact is not None:
        exact(mw)
        return (True, None)
    for prefix, handler in _PREFIX_CMDS:
        if message.startswith(prefix):
            handler(mw, message[len(prefix) :])
            break
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


def _toggle_ai(mw: aqt.AnkiQt) -> None:
    """Flip the source-grounded AI coach on/off from the rail chip. AI is always
    optional - everything still scores with it off - so this is a one-click switch
    from anywhere, mirrored by the Settings row."""
    col = mw.col
    if col is None:
        return
    if not srai.available():
        tooltip("AI coach isn't installed in this build.")
        return
    current = bool(col.get_config(srai._CFG_AI_DIAGNOSIS, False))
    col.set_config(srai._CFG_AI_DIAGNOSIS, not current)
    _render_sidebar(mw)
    if _ws_active is not None:
        _show_workspace(mw, _ws_active)
    tooltip(f"AI coach {'on' if not current else 'off'}.")


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
# The topic / section id currently drilled into, so a background refresh can
# repaint the exact same drill-in screen rather than bouncing to the dashboard.
_ws_topic_id: str | None = None
_ws_section_key: str | None = None
# Set once the Speedrun dashboard has taken over from Anki's startup deck browser,
# so the initial "land on Home" only fires on the first launch state change.
_landed_home = False

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
# Speedrun's only navigation surface is a persistent left rail (Home / Decks /
# Add / Browse / Stats / Practice / Library / Settings) rendered into its own
# webview, so it is never clobbered by an Anki state change the way content
# painted into the main webview is. Home / Practice / Library / Settings render as
# Speedrun screens in mw.web; Decks hands off to Anki's native deck -> overview ->
# reviewer flow; Add / Browse / Stats open Anki's native dialogs on top.

_shell_web: Any = None
_shell_holder: Any = None


class _ShellContext:
    """Render/bridge context for the sidebar webview, so the will_set_content
    hook doesn't treat it as the deck browser."""


_shell_ctx = _ShellContext()

# Map the active Speedrun screen (_ws_active) to the sidebar item to highlight.
# None (no Speedrun screen showing) means a native Anki state is active = Decks.
_SECTION_FOR_WS = {
    "dashboard": "home",
    "topic": "home",
    "section": "decks",
    "decks": "decks",
    "alldecks": "decks",
    "practice": "practice",
    "progress": "progress",
    "library": "library",
    "settings": "settings",
    # These screens have no matching nav item, so no sidebar item highlights.
    "sync": "sync",
    "report": "report",
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
    # No Speedrun screen showing -> a native Anki deck/review state is active.
    return _SECTION_FOR_WS.get(_ws_active or "", "decks")


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
        ai_state = {"available": srai.available(), "enabled": srai.enabled(mw.col)}
        html = theme.sidebar_html(
            _shell_active_section(),
            _sync_chip_state(mw),
            ai_state,
            _current_theme_mode(mw),
        )
        web.stdHtml(html, head=theme.page_style(), context=_shell_ctx)
    except Exception as exc:  # pragma: no cover - never break navigation
        print(f"speedrun: sidebar render failed: {exc}")


def _current_theme_mode(mw: aqt.AnkiQt) -> str:
    """The persisted appearance choice as a rail-toggle key."""
    try:
        from aqt.theme import Theme

        return {
            Theme.FOLLOW_SYSTEM: "system",
            Theme.LIGHT: "light",
            Theme.DARK: "dark",
        }.get(mw.pm.theme(), "system")
    except Exception:
        return "system"


def _set_theme_mode(mw: aqt.AnkiQt, mode: str) -> None:
    """Switch the app between System / Light / Dark (persisted by Anki), then
    re-render the shell + current Speedrun screen so the new palette takes hold."""
    try:
        from aqt.theme import Theme

        theme_for = {
            "system": Theme.FOLLOW_SYSTEM,
            "light": Theme.LIGHT,
            "dark": Theme.DARK,
        }
        mw.set_theme(theme_for.get(mode, Theme.FOLLOW_SYSTEM))
    except Exception as exc:
        print(f"speedrun: set theme failed: {exc}")
        return
    # Re-render our own webviews so they pick up the new night-mode palette
    # (Anki refreshes its native screens itself).
    _render_sidebar(mw)
    if _ws_active in (
        "dashboard",
        "decks",
        "alldecks",
        "practice",
        "library",
        "settings",
        "progress",
    ):
        _show_workspace(mw, _ws_active)


_toolbar_height_patched = False


def _retire_native_toolbar(mw: aqt.AnkiQt) -> None:
    """Full nav consolidation: the sidebar owns Decks / Add / Browse / Stats /
    Sync, so Anki's native top toolbar is redundant chrome. Anki re-derives the
    toolbar height from its content on every redraw (TopWebView._onHeight ->
    setFixedHeight), so a one-shot hide won't stick; instead we wrap that callback
    to clamp the height to 0 while the modern shell is on, and let it size
    normally when the modern UI is toggled off (vanilla Anki)."""
    global _toolbar_height_patched
    tb = getattr(mw, "toolbarWeb", None)
    if tb is None:
        return
    if not _toolbar_height_patched:
        original = tb._onHeight

        def _clamped(qvar: object) -> None:
            if mw.col is not None and _modern_on(mw.col):
                tb.setFixedHeight(0)
                tb.setVisible(False)
                return
            original(qvar)

        tb._onHeight = _clamped  # type: ignore[method-assign]
        _toolbar_height_patched = True
    if mw.col is not None and _modern_on(mw.col):
        # Height 0 keeps Anki's height-recompute from re-expanding it; hiding the
        # widget removes it outright (the sidebar owns Decks/Add/Browse/Stats/Sync).
        tb.setFixedHeight(0)
        tb.setVisible(False)
    else:
        tb.setVisible(True)
        tb.adjustHeightToFit()


def _apply_shell_visibility(mw: aqt.AnkiQt) -> None:
    """Show the sidebar when the modern UI is on, hide it otherwise (reverting to
    vanilla Anki chrome). Re-renders it when shown and retires/restores the native
    top toolbar to match."""
    holder = _shell_holder
    if holder is None:
        return
    on = _modern_on(mw.col) if mw.col is not None else False
    holder.setVisible(on)
    _retire_native_toolbar(mw)
    if on:
        _render_sidebar(mw)


def _navigate(mw: aqt.AnkiQt, section: str) -> None:
    """Handle a sidebar nav click. Decks hands off to Anki's native deck flow,
    Add/Browse/Stats open Anki's native dialogs on top, and the Speedrun screens
    (Home / Decks / Practice / Library / Settings) render in the main webview."""
    if mw.col is None:
        return
    if section in ("decks", "study"):  # "study" kept as a legacy alias
        _show_workspace(mw, "decks")
    elif section == "add":
        mw.onAddCard()
    elif section == "browse":
        mw.onBrowse()
    elif section in ("stats", "progress"):
        _show_workspace(mw, "progress")
    elif section == "library":
        _show_workspace(mw, "library")
    elif section == "sync":
        # AnkiWeb is primary now, so don't auto-start the local phone server on
        # open; it starts on demand when the user expands the phone section.
        _show_sync_pair(mw, start=False)
    elif section in ("practice", "settings"):
        _show_workspace(mw, section)
    else:  # home / dashboard / anything unrecognized
        _show_workspace(mw, "dashboard")


# --- one-button phone sync (Sync with phone) --------------------------------
#
# The desktop hosts the sync server (speedrun_sync manages the child process +
# credentials); this section owns the client-side orchestration: point Anki's
# own sync at the local server, run the sync (auto-resolving full syncs toward
# upload), and drive the pairing UI.


def _qr_svg(text: str) -> str:
    """Render a payload string as an inline SVG QR (dark modules on white, so it
    scans in both light and dark themes).

    segno emits ``<svg width="N" height="N">`` with no ``viewBox``; the pairing
    screen's CSS then forces the SVG to 220px. Without a viewBox that CROPS the
    QR (the intrinsic size exceeds 220), cutting off the quiet zone / finder
    modules so a phone can't decode it. We swap the fixed size for a viewBox so
    the whole code scales to fit and stays scannable.
    """
    try:
        import segno

        svg = segno.make(text, error="m").svg_inline(
            scale=5, border=2, dark="#16181D", light="#FFFFFF"
        )
        m = re.match(r'<svg width="(\d+(?:\.\d+)?)" height="(\d+(?:\.\d+)?)"', svg)
        if m:
            svg = svg.replace(
                m.group(0), f'<svg viewBox="0 0 {m.group(1)} {m.group(2)}"', 1
            )
        return svg
    except Exception as exc:  # pragma: no cover - QR is best-effort
        print(f"speedrun: qr generation failed: {exc}")
        return ""


def _sync_pair_data(mw: aqt.AnkiQt) -> dict:
    running = srsync.is_running()
    policy = _sync_conflict_policy(mw.col) if mw.col is not None else "ask"
    data: dict = {
        "running": running,
        "conflict_policy": policy,
        "ankiweb_signed_in": _ankiweb_signed_in(mw),
    }
    if running:
        payload = srsync.pairing_payload(mw)
        usb_ok, usb_status = srsync.usb_status_cached()
        data.update(
            {
                "url": payload.get("url", ""),
                "usb_url": payload.get("usb_url", ""),
                "user": payload.get("user", ""),
                "token": payload.get("token", ""),
                "exp": payload.get("exp", 0),
                "qr_svg": _qr_svg(json.dumps(payload)),
                "usb_ready": usb_ok,
                "usb_status": usb_status,
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

    def on_usb_ready() -> None:
        # The USB tunnel is established off-thread; refresh the badge once it is
        # known, without re-starting the server or blocking the initial render.
        if _ws_active == "sync":
            _show_sync_pair(mw, start=False, status_msg=status_msg)

    started = srsync.start_server(mw, on_ready=on_usb_ready) if start else False
    _ws_active = "sync"
    data = _sync_pair_data(mw)
    if status_msg:
        data["status"] = status_msg
    _render_ws(mw, theme.sync_pair_body(data))
    _render_sidebar(mw)
    # On the first pairing, seed the server with this device's collection so the
    # phone receives current data the moment it scans.
    if started and not srsync.is_paired(mw.col):
        _sync_to_local(
            mw,
            on_done=lambda: _show_sync_pair(
                mw, start=False, status_msg="Ready to scan. Server seeded."
            ),
            land_home=False,
        )


def _on_sync_will_start() -> None:
    """Before ANY native sync (AnkiWeb or a stock server), encode new sr_attempts
    as hidden notes so the standard note sync carries evidence a stock peer would
    otherwise drop with the custom chunk. Idempotent: attempts already noted are
    skipped, so this coexists with the local phone-sync path's inline encode."""
    mw = aqt.mw
    if mw is None or mw.col is None:
        return
    try:
        speedrun_notesync.encode_attempts(mw.col)
    except Exception as exc:  # pragma: no cover - never block a sync
        print(f"speedrun: attempt encode before sync failed: {exc}")


def _on_sync_did_finish() -> None:
    """After ANY native sync, decode attempts that arrived as notes back into
    sr_attempts and recompute readiness so synced practice history shows up.
    Idempotent (insert-or-ignore by attempt id); harmless when the custom chunk
    already delivered them."""
    mw = aqt.mw
    if mw is None or mw.col is None:
        return
    # An AnkiWeb sync is done; re-engage the local-server pin for later syncs.
    mw._speedrun_ankiweb_sync = False  # type: ignore[attr-defined]
    try:
        speedrun_notesync.decode_attempts(mw.col)
        mw.col._load_scheduler()
        library.refresh_readiness_after_sync(mw.col)
    except Exception as exc:  # pragma: no cover - never block a sync
        print(f"speedrun: attempt decode after sync failed: {exc}")


def _after_local_sync(
    mw: aqt.AnkiQt, on_done: object = None, *, land_home: bool = True
) -> None:
    """Refresh the UI after a sync so phone reviews show up on the desktop."""
    from aqt.qt import QTimer

    def finish_refresh() -> None:
        try:
            if mw.col is not None:
                # Decode attempts that arrived as notes (stock/AnkiWeb peers drop
                # the custom sr_attempts chunk), then reload + recompute readiness
                # so synced practice history shows up. Idempotent + harmless when
                # the chunk already delivered them.
                speedrun_notesync.decode_attempts(mw.col)
                mw.col._load_scheduler()
                library.refresh_readiness_after_sync(mw.col)
        except Exception:
            pass
        if land_home:
            try:
                _show_workspace(mw, "dashboard")
                mw.toolbar.redraw()
            except Exception:
                pass
        _render_sidebar(mw)
        if callable(on_done):
            on_done()

    try:
        mw.reset()
    except Exception:
        finish_refresh()
        return
    # Reset paints the native deck list first; replace it on the next tick.
    QTimer.singleShot(0, finish_refresh)


def _valid_sync_url(url: str | None) -> bool:
    """True when ``url`` is a usable http(s) sync base (host + port required)."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse

        p = urlparse(url.strip())
        return p.scheme in ("http", "https") and bool(p.hostname) and p.port is not None
    except Exception:
        return False


def _scrub_stale_sync_urls(pm) -> None:
    """Drop malformed saved endpoints (e.g. ``http://127.0.0.1:`` with no port)."""
    try:
        if not _valid_sync_url(pm.custom_sync_url()):
            pm.set_custom_sync_url(None)
        if not _valid_sync_url(pm._current_sync_url()):
            pm.set_current_sync_url(None)
    except Exception:
        pass


def _patch_profile_sync_auth(mw: aqt.AnkiQt) -> None:
    """Pin every sync call to the live embedded server when this collection is paired.

    Anki's own helpers (media sync, full upload/download, status checks) read
    ``pm.sync_auth()``, which otherwise reuses a stale ``currentSyncUrl`` like
    ``http://127.0.0.1:`` and fails with ``error sending request for url ()``.
    """
    pm = mw.pm
    original = pm.sync_auth

    def patched():
        auth = original()
        # During an explicit "Sync to AnkiWeb", don't pin to the local server -
        # let the real AnkiWeb endpoint (from stock login) through unchanged.
        if getattr(mw, "_speedrun_ankiweb_sync", False):
            return auth
        if auth is None or mw.col is None or not srsync.is_paired(mw.col):
            return auth
        url = _pin_local_sync_url(mw)
        if not url:
            return None
        from anki.sync import SyncAuth

        return SyncAuth(
            hkey=auth.hkey,
            endpoint=url,
            io_timeout_secs=auth.io_timeout_secs,
        )

    pm.sync_auth = patched  # type: ignore[method-assign]


def _local_sync_auth(mw: aqt.AnkiQt, url: str):
    """Return sync auth pinned to the live local server URL."""
    from anki.sync import SyncAuth

    auth = mw.pm.sync_auth()
    if auth is None:
        return None
    return SyncAuth(
        hkey=auth.hkey,
        endpoint=url,
        io_timeout_secs=auth.io_timeout_secs,
    )


def _pin_local_sync_url(mw: aqt.AnkiQt) -> str | None:
    """Start the embedded server if needed and point the profile at its URL."""
    if not srsync.start_server(mw):
        return None
    url = srsync.local_url()
    if not _valid_sync_url(url):
        return None
    # Overwrite both slots: a poisoned currentSyncUrl (missing port) otherwise
    # wins over customSyncUrl and breaks media sync + full sync helpers.
    mw.pm.set_custom_sync_url(url)
    mw.pm.set_current_sync_url(url)
    return url


def _local_full_download(
    mw: aqt.AnkiQt, server_usn: int | None, on_done: object
) -> None:
    """Full download pinned to the live embedded server (not stale profile URL)."""
    from aqt import gui_hooks
    from aqt import sync as anki_sync
    from aqt.qt import QTimer
    from aqt.utils import qconnect, tr

    url = _pin_local_sync_url(mw)
    if not url or mw.col is None:
        tooltip(
            "Sync server is not running. Open Sync with phone and tap "
            "Start & show code."
        )
        if callable(on_done):
            on_done()
        return
    auth = _local_sync_auth(mw, url)
    if auth is None:
        tooltip("Not signed in to the local sync server.")
        if callable(on_done):
            on_done()
        return

    label = tr.sync_downloading_from_ankiweb()

    def on_timer() -> None:
        anki_sync.on_full_sync_timer(mw, label)

    timer = QTimer(mw)
    qconnect(timer.timeout, on_timer)
    timer.start(150)
    gui_hooks.collection_will_temporarily_close(mw.col)

    def download() -> None:
        mw.create_backup_now()
        mw.col.close_for_full_sync()
        mw.col.full_upload_or_download(auth=auth, server_usn=server_usn, upload=False)

    def on_future_done(fut) -> None:
        timer.stop()
        mw.reopen(after_full_sync=True)
        mw.reset()
        try:
            fut.result()
        except Exception as err:
            anki_sync.handle_sync_error(mw, err)
        mw.media_syncer.start_monitoring()
        if callable(on_done):
            on_done()

    mw.taskman.with_progress(download, on_future_done)


def _local_full_upload(mw: aqt.AnkiQt, server_usn: int | None, on_done: object) -> None:
    """Full upload pinned to the live embedded server (not stale profile URL)."""
    from aqt import gui_hooks
    from aqt import sync as anki_sync
    from aqt.qt import QTimer
    from aqt.utils import qconnect, tr

    url = _pin_local_sync_url(mw)
    if not url or mw.col is None:
        tooltip(
            "Sync server is not running. Open Sync with phone and tap "
            "Start & show code."
        )
        if callable(on_done):
            on_done()
        return
    auth = _local_sync_auth(mw, url)
    if auth is None:
        tooltip("Not signed in to the local sync server.")
        if callable(on_done):
            on_done()
        return

    gui_hooks.collection_will_temporarily_close(mw.col)
    mw.col.close_for_full_sync()
    label = tr.sync_uploading_to_ankiweb()

    def on_timer() -> None:
        anki_sync.on_full_sync_timer(mw, label)

    timer = QTimer(mw)
    qconnect(timer.timeout, on_timer)
    timer.start(150)

    def on_future_done(fut) -> None:
        timer.stop()
        mw.reopen(after_full_sync=True)
        mw.reset()
        try:
            fut.result()
        except Exception as err:
            anki_sync.handle_sync_error(mw, err)
            if callable(on_done):
                on_done()
            return
        mw.media_syncer.start_monitoring()
        if callable(on_done):
            on_done()

    mw.taskman.with_progress(
        lambda: mw.col.full_upload_or_download(
            auth=auth, server_usn=server_usn, upload=True
        ),
        on_future_done,
    )


def _sync_conflict_policy(col) -> str:
    """The persisted on-conflict preference ("ask" / "phone" / "desktop")."""
    try:
        value = str(col.get_config(_CFG_SYNC_CONFLICT, _SYNC_CONFLICT_DEFAULT) or "")
    except Exception:
        return _SYNC_CONFLICT_DEFAULT
    return value if value in _SYNC_CONFLICT_CHOICES else _SYNC_CONFLICT_DEFAULT


# User-facing sync failure copy, kept parallel to the phone's messages so a
# timeout is never mislabeled as a bad URL and an auth rejection is actionable.
_SYNC_CONNECTIVITY_MSG = (
    "Couldn't reach the sync server. Open Sync with phone to host it, and make "
    "sure USB or Wi-Fi is connected."
)
_SYNC_AUTH_MSG = (
    "The pairing key was rejected. Open Sync with phone to show a fresh code, "
    "then re-pair."
)
# Substrings that mark a connectivity/timeout failure rather than a bad URL,
# used only when the backend error arrives without a typed kind. Note that
# "error sending request for url ()" is a network failure (reqwest), not an
# invalid-URL error, so it must map to connectivity.
_SYNC_CONNECTIVITY_HINTS = (
    "error sending request",
    "url ()",
    "connection refused",
    "connection reset",
    "connection timed out",
    "timed out",
    "timeout",
    "failed to connect",
    "network is unreachable",
    "no route to host",
    "could not resolve",
    "dns error",
)


def _sync_error_message(err: Exception) -> str:
    """Map a raw sync exception to an accurate, actionable message.

    Distinguishes connectivity/timeout vs an auth rejection vs anything else,
    mirroring the phone so an unreachable server is never reported as an invalid
    URL (a genuinely malformed URL is caught earlier by ``_valid_sync_url``).
    """
    try:
        from anki.errors import NetworkError, SyncError, SyncErrorKind

        if isinstance(err, NetworkError):
            return _SYNC_CONNECTIVITY_MSG
        if isinstance(err, SyncError) and err.kind is SyncErrorKind.AUTH:
            return _SYNC_AUTH_MSG
    except Exception:
        pass
    raw = str(err)
    lower = raw.lower()
    if any(hint in lower for hint in _SYNC_CONNECTIVITY_HINTS):
        return _SYNC_CONNECTIVITY_MSG
    return f"Sync unavailable: {raw}"


def _autoresolved_conflict_message(*, phone_wins: bool) -> str:
    """Transparent success text after the saved preference resolved a conflict."""
    if phone_wins:
        return (
            "Conflict resolved: kept the phone's data (your preference); the "
            "desktop copy was overwritten."
        )
    return (
        "Conflict resolved: kept the desktop's data (your preference); the "
        "phone's copy was overwritten."
    )


def _resolve_full_sync_conflict(mw: aqt.AnkiQt, server_usn: int | None, done) -> None:
    """Resolve a genuine two-sided conflict per the saved on-conflict policy.

    "phone" mirrors the server's copy down (phone wins); "desktop" pushes this
    copy up (desktop wins); both report which side was overwritten. "ask" prompts
    for the direction instead of silently picking one.
    """
    policy = _sync_conflict_policy(mw.col) if mw.col is not None else "ask"
    if policy == "phone":

        def after_phone() -> None:
            tooltip(_autoresolved_conflict_message(phone_wins=True))
            done()

        _local_full_download(mw, server_usn, after_phone)
    elif policy == "desktop":

        def after_desktop() -> None:
            tooltip(_autoresolved_conflict_message(phone_wins=False))
            done()

        _local_full_upload(mw, server_usn, after_desktop)
    else:
        _prompt_conflict_direction(mw, server_usn, done)


def _prompt_conflict_direction(mw: aqt.AnkiQt, server_usn: int | None, done) -> None:
    """Ask which copy wins on a genuine conflict (the "ask" policy). The choice
    surfaces only here, on a real conflict, not as always-visible buttons."""
    from aqt.utils import ask_user_dialog

    def callback(choice: int) -> None:
        if choice == 0:
            _local_full_download(mw, server_usn, done)
        elif choice == 1:
            _local_full_upload(mw, server_usn, done)
        else:
            done()

    ask_user_dialog(
        "This desktop and the phone have different data. Choose which copy to "
        "keep - the other side will be replaced.",
        callback=callback,
        buttons=["Use phone data", "Use desktop data", "Cancel"],
        default_button=2,
        parent=mw,
        title="Sync conflict",
    )


def _run_local_sync(
    mw: aqt.AnkiQt, on_done: object = None, *, land_home: bool = True
) -> None:
    """Sync the collection with the embedded local server.

    Incremental merges run inside ``sync_collection`` and finish as NO_CHANGES.
    Full syncs where the server dictates the direction (FULL_UPLOAD /
    FULL_DOWNLOAD) resolve automatically; a genuine two-sided conflict
    (FULL_SYNC) is routed through the saved on-conflict policy instead of being
    silently overwritten.
    """
    url = _pin_local_sync_url(mw)
    if not url or mw.col is None:
        tooltip(
            "Sync server is not running. Open Sync with phone and tap "
            "Start & show code."
        )
        return
    auth = _local_sync_auth(mw, url)
    if auth is None:
        _sync_to_local(mw, on_done, land_home=land_home)
        return

    def done() -> None:
        _after_local_sync(mw, on_done, land_home=land_home)

    def on_future_done(fut: Any) -> None:
        try:
            mw.col._load_scheduler()
        except Exception:
            pass
        try:
            out = fut.result()
        except Exception as err:
            tooltip(_sync_error_message(err))
            return done()
        try:
            mw.pm.set_host_number(out.host_number)
            if out.new_endpoint and _valid_sync_url(out.new_endpoint):
                mw.pm.set_current_sync_url(out.new_endpoint)
        except Exception:
            pass
        if out.required in (out.NO_CHANGES, out.NORMAL_SYNC):
            try:
                mw.media_syncer.start_monitoring()
            except Exception:
                pass
            return done()
        server_usn = out.server_media_usn if mw.pm.media_syncing_enabled() else None
        if out.required == out.FULL_DOWNLOAD:
            _local_full_download(mw, server_usn, done)
        elif out.required == out.FULL_UPLOAD:
            _local_full_upload(mw, server_usn, done)
        else:
            # FULL_SYNC: both sides diverged. Honor the on-conflict policy
            # (ask / prefer phone / prefer desktop) instead of silently picking.
            _resolve_full_sync_conflict(mw, server_usn, done)

    # Encode new sr_attempts as notes BEFORE the sync so they ride the standard
    # note sync (AnkiWeb / any peer), not only the custom chunk a stock peer drops.
    try:
        speedrun_notesync.encode_attempts(mw.col)
    except Exception as exc:  # pragma: no cover - never block a sync
        print(f"speedrun: attempt encode before sync failed: {exc}")

    mw.taskman.with_progress(
        lambda: mw.col.sync_collection(auth, mw.pm.media_syncing_enabled()),
        on_future_done,
        label="Syncing…",
        immediate=True,
    )


def _sync_directional_local(mw: aqt.AnkiQt, *, upload: bool, on_done=None) -> None:
    """Force a one-way full sync with the embedded server."""
    if mw.col is None or not srsync.start_server(mw):
        tooltip("Sync server is not running.")
        return
    url = _pin_local_sync_url(mw)
    if not url:
        return
    auth = _local_sync_auth(mw, url)
    if auth is None:
        _login_to_local(
            mw,
            on_done=lambda: _sync_directional_local(mw, upload=upload, on_done=on_done),
        )
        return

    def done() -> None:
        _after_local_sync(mw, on_done)

    server_usn = None
    if upload:
        _local_full_upload(mw, server_usn, done)
    else:
        _local_full_download(mw, server_usn, done)


def _sync_pull_from_phone(mw: aqt.AnkiQt) -> None:
    """Download the server's collection (phone uploads land here)."""
    if not srsync.start_server(mw):
        _show_sync_pair(mw, start=False)
        return
    _show_sync_pair(mw, start=False, status_msg="Pulling phone changes…")
    _sync_directional_local(
        mw,
        upload=False,
        on_done=lambda: tooltip("Desktop updated from phone."),
    )


def _sync_push_to_phone(mw: aqt.AnkiQt) -> None:
    """Upload this desktop collection to the server for the phone to mirror."""
    if not srsync.start_server(mw):
        _show_sync_pair(mw, start=False)
        return
    _show_sync_pair(mw, start=False, status_msg="Pushing desktop copy…")
    _sync_directional_local(
        mw,
        upload=True,
        on_done=lambda: tooltip("Server updated with desktop copy."),
    )


def _refresh_usb_tunnel(mw: aqt.AnkiQt) -> None:
    """Re-run adb reverse off the Qt thread and refresh the Sync screen USB
    status, so the click never blocks on the adb subprocess timeouts."""
    if not srsync.start_server(mw):
        _show_sync_pair(mw, start=False, status_msg="Could not start sync server.")
        return
    _show_sync_pair(mw, start=False, status_msg="Refreshing USB tunnel\u2026")

    def work() -> tuple[bool, str]:
        return srsync.setup_usb_tunnel()

    def done(fut: Any) -> None:
        try:
            ok, msg = fut.result()
        except Exception as exc:
            ok, msg = False, f"USB tunnel failed: {exc}"
        srsync.set_usb_status(ok, msg)
        if _ws_active == "sync":
            _show_sync_pair(mw, start=False, status_msg=msg if ok else f"USB: {msg}")

    mw.taskman.run_in_background(work, done, uses_collection=False)


def _refresh_pairing_code(mw: aqt.AnkiQt) -> None:
    """Regenerate the QR with a fresh expiry (starting the server if needed), so
    a code that expired can be replaced without leaving the screen."""
    _show_sync_pair(mw, start=True, status_msg="Fresh pairing code ready.")


def _set_sync_conflict_policy(mw: aqt.AnkiQt, value: str) -> None:
    """Persist the on-conflict preference (ask / phone / desktop) and re-render
    the sync screen so the selected option + its plain-language effect update."""
    col = mw.col
    if col is None or value not in _SYNC_CONFLICT_CHOICES:
        return
    col.set_config(_CFG_SYNC_CONFLICT, value)
    _show_sync_pair(mw, start=False, status_msg="On-conflict preference saved.")


_idle_watchdog: Any = None


def _ensure_idle_watchdog(mw: aqt.AnkiQt) -> None:
    """Install a once-a-minute check that stops the hosted sync server after it
    has been idle too long, then refreshes the UI to reflect that it is no longer
    hosting (so a stale QR is never left on screen)."""
    global _idle_watchdog
    if _idle_watchdog is not None:
        return
    from aqt.qt import QTimer

    timer = QTimer(mw)
    timer.setInterval(60_000)
    timer.timeout.connect(lambda: _on_idle_check(mw))
    timer.start()
    _idle_watchdog = timer


def _on_idle_check(mw: aqt.AnkiQt) -> None:
    if mw.col is None or not srsync.stop_if_idle():
        return
    _render_sidebar(mw)
    if _ws_active == "sync":
        _show_sync_pair(
            mw,
            start=False,
            status_msg="Sync server stopped after being idle. Tap Start & show "
            "code to host again.",
        )


def _clear_for_sync_test(mw: aqt.AnkiQt) -> None:
    """Wipe this desktop's Speedrun study evidence so sync from the phone can be
    re-tested from a clean, honestly-abstaining state.

    Clears the diagnostic evidence layer only - the recorded practice/exam
    attempts (``sr_attempts``), the cached readiness snapshots (``sr_readiness``)
    and the sample-history flag - then recomputes readiness so the cached
    snapshot the UI reads is the honest "no score yet" abstain. Imported decks,
    cards and the topic map are left untouched. The active Speedrun screen is
    re-rendered in place afterwards so the reset is visible and the user is never
    dropped onto Anki's native deck list."""
    from aqt.utils import askUser, showWarning

    col = mw.col
    if col is None:
        return
    if not askUser(
        "Clear Speedrun study data on this desktop?\n\n"
        "Removes practice attempts and resets your readiness back to "
        "\u201cno score yet\u201d. Imported decks and cards stay. Then tap "
        "Sync now to pull the phone's data.",
        title="Speedrun",
    ):
        return

    def done(fut) -> None:
        try:
            attempts = fut.result()
        except Exception as exc:  # pragma: no cover - surfaced to the user
            showWarning(f"Speedrun: {exc}")
            return
        # Re-render the themed surface in place. Anki's native deck browser
        # repaints asynchronously, so calling its refresh here would clobber the
        # Speedrun screen a tick later; drive the in-place re-render instead.
        _render_sidebar(mw)
        if _ws_active == "sync":
            _show_sync_pair(
                mw,
                start=False,
                status_msg="Cleared. Tap Sync now to pull the phone's data.",
            )
        elif _ws_active is not None:
            _show_workspace(mw, _ws_active)
        mw.toolbar.redraw()
        tooltip(f"Cleared {attempts} attempts. Tap Sync now to pull the phone's data.")

    mw.taskman.with_progress(
        lambda: _clear_study_evidence(col),
        done,
        parent=mw,
        label="Clearing study data\u2026",
        immediate=True,
    )


def _clear_study_evidence(col) -> int:
    """Delete the Speedrun diagnostic evidence layer and recompute readiness.

    Removes every recorded attempt (``sr_attempts``) and cached readiness
    snapshot (``sr_readiness``) and clears the sample-history flag, then
    recomputes readiness so the cached snapshot abstains again (with no attempts
    the give-up rule always refuses to commit to a number). Cards, decks and the
    topic map are left untouched. Returns the number of attempts removed."""
    attempts = int(col.db.scalar("select count(*) from sr_attempts") or 0)
    col.db.execute("delete from sr_attempts")
    col.db.execute("delete from sr_readiness")
    col.set_config(library._CFG_SAMPLE, False)
    col._backend.compute_readiness()
    return attempts


def _login_to_local(mw: aqt.AnkiQt, on_done=None) -> None:
    """Sign the profile into the embedded server without running a sync."""
    if mw.col is None:
        return
    url = _pin_local_sync_url(mw)
    if not url:
        tooltip(
            "Sync server is not running. Open Sync with phone and tap "
            "Start & show code."
        )
        return
    user, token = srsync.creds(mw.col)

    def do_login():
        return mw.col.sync_login(username=user, password=token, endpoint=url)

    def after_login(fut) -> None:
        try:
            auth = fut.result()
        except Exception as exc:
            tooltip(_sync_error_message(exc))
            return
        mw.pm.set_sync_key(auth.hkey)
        try:
            mw.pm.set_sync_username(user)
        except Exception:
            pass
        srsync.mark_paired(mw.col)
        if callable(on_done):
            on_done()

    mw.taskman.with_progress(do_login, after_login, parent=mw)


def _sync_to_local(mw: aqt.AnkiQt, on_done=None, *, land_home: bool = True) -> None:
    """Start the local server, sign Anki's own sync in against it, and sync. This
    is the shared path for the toolbar Sync button, the chip, and auto-sync."""
    # Any local sync clears a stuck AnkiWeb-sync flag (e.g. a cancelled login),
    # so the local-server pin re-engages.
    mw._speedrun_ankiweb_sync = False  # type: ignore[attr-defined]

    def after_login() -> None:
        _run_local_sync(mw, on_done, land_home=land_home)

    _login_to_local(mw, after_login)


def _is_ankiweb_endpoint(url: str | None) -> bool:
    """True when ``url`` points at AnkiWeb (vs the embedded/self-hosted server)."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return host.endswith("ankiweb.net")


def _ankiweb_signed_in(mw: aqt.AnkiQt) -> bool:
    """True when Anki holds a persisted AnkiWeb session (so the user stays signed
    in across syncs). Reads the RAW stored auth past the local-server pin; an
    empty endpoint means the default (AnkiWeb) endpoint, so it counts as signed
    in, while a local (127.0.0.1/LAN) endpoint does not."""
    prev = getattr(mw, "_speedrun_ankiweb_sync", False)
    try:
        mw._speedrun_ankiweb_sync = True  # type: ignore[attr-defined]
        auth = mw.pm.sync_auth()
        if not auth:
            return False
        endpoint = getattr(auth, "endpoint", "") or ""
        return (not endpoint) or _is_ankiweb_endpoint(endpoint)
    except Exception:
        return False
    finally:
        mw._speedrun_ankiweb_sync = prev  # type: ignore[attr-defined]


def _ankiweb_sign_in(mw: aqt.AnkiQt) -> None:
    """Sign in to AnkiWeb ONCE (no sync). Anki persists the session key, so the
    user stays signed in - later syncs reuse it without re-entering a password."""
    if mw.col is None:
        return
    # Target AnkiWeb (not the pinned local server): drop any local url so the
    # stock login dialog uses AnkiWeb's default endpoint.
    try:
        mw.pm.set_current_sync_url(None)
        mw.pm.set_custom_sync_url(None)
    except Exception:
        pass
    from aqt.sync import sync_login

    sync_login(
        mw,
        on_success=lambda: _show_sync_pair(
            mw, start=False, status_msg="Signed in to AnkiWeb."
        ),
    )


def _ankiweb_sign_out(mw: aqt.AnkiQt) -> None:
    """Forget the stored AnkiWeb session."""
    try:
        mw.pm.clear_sync_auth()
    except Exception:
        pass
    _show_sync_pair(mw, start=False, status_msg="Signed out of AnkiWeb.")


def _sync_to_ankiweb(mw: aqt.AnkiQt) -> None:
    """Sync to AnkiWeb (the cloud), reusing the persisted sign-in when present so
    no password is needed. sr_attempts ride the standard note sync via the
    ``sync_will_start``/``sync_did_finish`` hooks. If not signed in, Anki's stock
    login dialog collects credentials first (then the session is remembered)."""
    native = getattr(mw, "_speedrun_native_sync", None)
    if native is None or mw.col is None:
        tooltip("AnkiWeb sync is unavailable.")
        return
    mw._speedrun_ankiweb_sync = True  # type: ignore[attr-defined]
    # Reuse an existing AnkiWeb session; only when the stored auth is the local
    # server (or missing) do we drop it so the stock dialog logs in to AnkiWeb.
    if not _ankiweb_signed_in(mw):
        try:
            mw.pm.clear_sync_auth()
            mw.pm.set_current_sync_url(None)
            mw.pm.set_custom_sync_url(None)
        except Exception:
            pass
    native()


def _sync_now_from_screen(mw: aqt.AnkiQt) -> None:
    """The Sync screen's primary button: ensure the server is up, then sync."""
    if not srsync.start_server(mw):
        _show_sync_pair(mw, start=False)
        return
    _show_sync_pair(mw, start=False, status_msg="Syncing…")
    _sync_to_local(mw, on_done=lambda: tooltip("Synced."))


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
            "title": "Auto practice after review",
            "on": on(_CFG_AUTO_ROUND, False),
            "desc": "After you finish a deck's reviews, jump straight into "
            "practice on what you just studied (otherwise it's offered).",
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
    paired it syncs to the local server directly (auto-resolving via
    ``_run_local_sync``); otherwise it opens the Sync-with-phone screen (start
    server + show QR)."""

    # Preserve Anki's stock AnkiWeb sync (login dialog + collection/media sync)
    # so the Speedrun "Sync to AnkiWeb" action can reach the cloud. The toolbar
    # button below still routes to the local phone-sync flow.
    if not hasattr(mw, "_speedrun_native_sync"):
        mw._speedrun_native_sync = mw.on_sync_button_clicked  # type: ignore[attr-defined]

    def patched() -> None:
        # AnkiWeb is the primary sync (cloud, no LAN needed). The stock login
        # dialog collects credentials on first use; phone/LAN pairing lives on
        # the Sync screen as a secondary option.
        if mw.col is not None:
            _sync_to_ankiweb(mw)

    mw.on_sync_button_clicked = patched  # type: ignore[method-assign]

    _scrub_stale_sync_urls(mw.pm)
    _patch_profile_sync_auth(mw)
    _ensure_idle_watchdog(mw)

    # Anki auto-syncs on startup/shutdown by default, using the stored custom
    # sync url. Our embedded server binds a fresh port each launch, so that url
    # is stale on open -> "error sending request for url ()". We own sync (via the
    # toolbar button + a guarded focus auto-sync), so switch Anki's native
    # unattended auto-sync off -- persisted, and blocked this session too.
    try:
        mw.pm.profile["autoSync"] = False
        mw.can_auto_sync = lambda: False  # type: ignore[method-assign]
    except Exception:
        pass

    # Belt-and-suspenders: route EVERY sync failure (incl. Anki's full-sync
    # helpers, which bypass our own handler) through a quiet tooltip instead of
    # the blocking "A network error occurred" modal.
    try:
        import aqt.sync as _anki_sync
        from anki.errors import Interrupted, SyncError, SyncErrorKind

        def _quiet_sync_error(mw_: object, err: Exception) -> None:
            if isinstance(err, SyncError) and err.kind is SyncErrorKind.AUTH:
                try:
                    mw_.pm.clear_sync_auth()  # type: ignore[attr-defined]
                except Exception:
                    pass
            if isinstance(err, Interrupted):
                return
            tooltip(_sync_error_message(err))

        _anki_sync.handle_sync_error = _quiet_sync_error  # type: ignore[assignment]
    except Exception:
        pass


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
            tooltip(_sync_error_message(exc))
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


_PRACTICE_SET_SIZE = 20


def _fetch_practice_questions(
    mw: aqt.AnkiQt, topics: list[str] | None = None
) -> list[dict]:
    """Load a practice set. With no topics, a mixed diagnostic across the whole
    bank; with topics, an even split filtered to those subjects (merged, so a
    whole section pulls from each of its subjects)."""
    try:
        if not topics:
            raw = list(
                mw.col._backend.get_practice_questions(
                    limit=_PRACTICE_SET_SIZE, topic=""
                )
            )
            return _parse_question_items(raw)
        per = max(_PRACTICE_SET_SIZE // len(topics), 4)
        merged: list[dict] = []
        for topic in topics:
            raw = list(mw.col._backend.get_practice_questions(limit=per, topic=topic))
            merged = _merge_questions(
                merged, _parse_question_items(raw), len(merged) + per
            )
        return merged[:_PRACTICE_SET_SIZE]
    except Exception as exc:
        tooltip(f"Could not load practice questions: {exc}")
        return []


def _bank_counts(mw: aqt.AnkiQt) -> dict[str, int]:
    """Per-subject question counts from the backend bank summary (empty on any
    error, so the Practice landing still renders)."""
    try:
        summary = mw.col._backend.get_practice_bank_summary()
        return {row.topic: int(row.count) for row in summary.topics}
    except Exception:
        return {}


def _practice_landing(mw: aqt.AnkiQt) -> dict:
    counts = _bank_counts(mw)
    sections = []
    for sec in mcat.SECTIONS:
        sec_count = sum(counts.get(subject, 0) for subject in sec["subjects"])
        sections.append(
            {
                "key": sec["key"],
                "short": sec["short"],
                "full": sec["full"],
                "subjects": sec["subjects"],
                "reasoning": bool(sec.get("reasoning")),
                "count": sec_count,
            }
        )
    return {
        "mode": "landing",
        "total": sum(counts.values()),
        "sections": sections,
    }


def _practice_section(mw: aqt.AnkiQt, key: str) -> dict | None:
    sec = mcat.section_by_key(key)
    if sec is None:
        return None
    counts = _bank_counts(mw)
    subjects = [
        {
            "subject": subject,
            "label": mcat.subject_label(subject),
            "count": counts.get(subject, 0),
        }
        for subject in sec["subjects"]
    ]
    return {
        "mode": "section",
        "section": {"key": sec["key"], "short": sec["short"], "full": sec["full"]},
        "count": sum(s["count"] for s in subjects),
        "subjects": subjects,
    }


def _show_practice_section(mw: aqt.AnkiQt, key: str) -> None:
    """Render a section drill-down; fall back to the landing on an unknown key."""
    global _ws_active
    data = _practice_section(mw, key)
    if data is None:
        _show_workspace(mw, "practice")
        return
    _ws_active = "practice"
    _render_ws(mw, theme.practice_body(data))
    _render_sidebar(mw)


def _start_practice(mw: aqt.AnkiQt, topics: list[str]) -> None:
    """Start a topic-filtered runner (empty ``topics`` = mixed diagnostic)."""
    global _ws_active, _ws_practice
    questions = _fetch_practice_questions(mw, topics or None)
    if not questions:
        tooltip("No practice questions available for that selection yet.")
        return
    _ws_active = "practice"
    _ws_practice = _WsPractice(mw, questions)
    _ws_practice.render()
    _render_sidebar(mw)


# --- first-run placement diagnostic -----------------------------------------
#
# Right after onboarding (exam date + target), offer a short mixed placement
# quiz. It reuses the practice runner but records to its own session so the
# answers seed the performance + coverage + calibration signals, then shows a
# per-section read with an honest (often still-abstaining) readiness.

_DIAGNOSTIC_SID = "onboarding-diagnostic"
_DIAGNOSTIC_PER_SECTION = 5


def _diagnostic_questions(mw: aqt.AnkiQt) -> list[dict]:
    """A balanced placement set: up to _DIAGNOSTIC_PER_SECTION exam-style
    questions from each scored MCAT section that has a question bank (CARS has
    none), drawn from the practice bank via the existing per-subject fetch."""
    out: list[dict] = []
    for sec in mcat.SECTIONS:
        subjects = sec.get("subjects") or []
        # CARS is passage-based and needs its reading panel, so it is not part of
        # the quick discrete-question placement quiz.
        if not subjects or sec.get("reasoning"):
            continue
        out.extend(_fetch_practice_questions(mw, subjects)[:_DIAGNOSTIC_PER_SECTION])
    return out


def _start_diagnostic(mw: aqt.AnkiQt) -> None:
    """Run the placement quiz through the practice runner, in its own session, and
    show the per-section report on completion."""
    global _ws_active, _ws_practice
    if mw.col is None:
        return
    mw.col.set_config(_CFG_DIAGNOSTIC, True)
    questions = _diagnostic_questions(mw)
    if not questions:
        tooltip("No practice questions available for a diagnostic yet.")
        _show_workspace(mw, "dashboard")
        return
    _ws_active = "practice"
    _ws_practice = _WsPractice(
        mw, questions, session_id=_DIAGNOSTIC_SID, on_complete=_show_diagnostic_report
    )
    _ws_practice.render()
    _render_sidebar(mw)


def _skip_diagnostic(mw: aqt.AnkiQt) -> None:
    if mw.col is not None:
        mw.col.set_config(_CFG_DIAGNOSTIC, True)
    _show_workspace(mw, "dashboard")


def _show_diagnostic_report(mw: aqt.AnkiQt, runner: "_WsPractice") -> None:
    """Render the placement result: per-section accuracy + an honest readiness
    read (abstains until the give-up gate is met)."""
    global _ws_active
    if mw.col is not None:
        mw.col.set_config(_CFG_DIAGNOSTIC, True)
    sections = []
    total_c = total_n = 0
    for sec in mcat.SECTIONS:
        stat = runner.section_stats.get(sec["key"])
        if not stat:
            continue
        correct, n = stat[0], stat[1]
        total_c += correct
        total_n += n
        sections.append(
            {
                "short": sec["short"],
                "full": sec["full"],
                "correct": correct,
                "total": n,
                "pct": round(100 * correct / n) if n else 0,
            }
        )
    overall = {
        "correct": total_c,
        "total": total_n,
        "pct": round(100 * total_c / total_n) if total_n else 0,
    }
    readiness: dict = {"sufficient": False, "reason": ""}
    try:
        snap = mw.col._backend.compute_readiness()
        readiness = {
            "sufficient": bool(snap.sufficient),
            "scaled": int(snap.readiness_scaled),
            "low": int(snap.low_scaled),
            "high": int(snap.high_scaled),
            "reason": str(snap.reason),
        }
    except Exception:
        pass
    _ws_active = "practice"
    _render_ws(
        mw,
        theme.diagnostic_report_body(
            {"sections": sections, "overall": overall, "readiness": readiness}
        ),
    )
    _render_sidebar(mw)


def maybe_start_diagnostic(mw: aqt.AnkiQt) -> bool:
    """Offer the first-run placement quiz once, after onboarding, when the modern
    UI is on and the practice bank has questions. Returns True if the intro was
    shown (so first-run doesn't paint the dashboard over it)."""
    global _ws_active
    col = mw.col
    if col is None or not _modern_on(col):
        return False
    if col.get_config(_CFG_DIAGNOSTIC, False):
        return False
    try:
        if sum(_bank_counts(mw).values()) <= 0:
            return False
    except Exception:
        return False
    count = len(_diagnostic_questions(mw))
    if count <= 0:
        return False
    _ws_active = "practice"
    _render_ws(mw, theme.diagnostic_intro_body(count))
    _render_sidebar(mw)
    return True


def _show_workspace(mw: aqt.AnkiQt, tab: str) -> None:
    """Render a Speedrun screen (home/dashboard, practice, library, or settings)
    into the main webview and sync the sidebar highlight."""
    global _ws_active
    if mw.col is None:
        return
    if tab == "practice":
        _ws_active = "practice"
        # Resume an in-progress runner; otherwise show the MCAT-section landing.
        if _ws_practice is not None and not _ws_practice.done():
            _ws_practice.render()
        else:
            _render_ws(mw, theme.practice_body(_practice_landing(mw)))
    elif tab == "library":
        _ws_active = "library"
        _render_ws(
            mw, theme.library_body(library.content_status(mw), library.popular_decks())
        )
    elif tab == "settings":
        _ws_active = "settings"
        _render_ws(mw, theme.settings_body(_settings_items(mw.col), _sync_info(mw)))
    elif tab == "progress":
        _ws_active = "progress"
        _render_ws(mw, theme.progress_body(_collect_progress(mw.col)))
    elif tab == "decks":
        _ws_active = "decks"
        dash = _collect_topic_dashboard(mw)
        ungrouped = grouping.ungrouped_note_count(mw.col)
        _render_ws(mw, theme.decks_topic_body(dash, ungrouped))
    elif tab == "alldecks":
        _ws_active = "alldecks"
        _render_ws(mw, theme.deck_list_body(_collect_deck_list(mw)))
    else:
        _ws_active = "dashboard"
        body = theme._stack(_collect(mw.col, fresh=True))
        body += theme.topic_dashboard_html(_collect_topic_dashboard(mw))
        _render_ws(mw, body)
    _render_sidebar(mw)


def _show_topic_detail(mw: aqt.AnkiQt, topic_id: str) -> None:
    """Render one topic's focused view (its three signals + actions only)."""
    global _ws_active
    if mw.col is None:
        return
    dash = _collect_topic_dashboard(mw)
    topic = next(
        (
            t
            for sec in dash.get("sections", [])
            for t in sec.get("topics", [])
            if t["id"] == topic_id
        ),
        None,
    )
    if topic is None:
        _show_workspace(mw, "dashboard")
        return
    global _ws_topic_id
    _ws_active = "topic"
    _ws_topic_id = topic_id
    _render_ws(mw, theme.topic_detail_body(topic))
    _render_sidebar(mw)


def _open_topic(mw: aqt.AnkiQt, arg: str) -> None:
    """Route ``speedrun:topic:*``: a bare id opens the topic view; ``study:<id>``
    opens the browser filtered to that topic's cards; ``practice:<id>`` starts a
    practice runner for the topic's subject."""
    if arg.startswith("study:"):
        _study_topic(mw, arg[len("study:") :])
    elif arg.startswith("review:"):
        _review_topic(mw, arg[len("review:") :])
    elif arg.startswith("practice:"):
        tid = arg[len("practice:") :]
        subject = library.topic_meta().get(tid, {}).get("subject")
        if subject:
            topics = [subject]
        else:
            # No per-category subject (e.g. the coarse 10-FC map): fall back to
            # the topic's whole section rather than a mixed all-sections set.
            sec = _section_for_topic(tid)
            topics = list(sec["subjects"]) if sec else []
        _start_practice(mw, topics)
    else:
        _show_topic_detail(mw, arg)


def _show_section(mw: aqt.AnkiQt, key: str) -> None:
    """Render one MCAT section's page (its subtopics), reached by tapping a
    section card on Home / Decks."""
    global _ws_active
    if mw.col is None:
        return
    dash = _collect_topic_dashboard(mw)
    sec = next((s for s in dash.get("sections", []) if s.get("key") == key), None)
    if sec is None:
        _show_workspace(mw, "decks")
        return
    global _ws_section_key
    _ws_active = "section"
    _ws_section_key = key
    _render_ws(mw, theme.section_detail_body(sec))
    _render_sidebar(mw)


def rerender_active_workspace(mw: aqt.AnkiQt) -> bool:
    """Repaint whichever in-place Speedrun screen currently owns the webview, so
    a background refresh (e.g. after an import) never lets Anki's native
    overview / deck list paint over it. Returns True when a screen was
    repainted."""
    if _ws_active is None or mw.col is None:
        return False
    try:
        if _ws_active == "topic" and _ws_topic_id is not None:
            _show_topic_detail(mw, _ws_topic_id)
        elif _ws_active == "section" and _ws_section_key is not None:
            _show_section(mw, _ws_section_key)
        elif _ws_active in (
            "dashboard",
            "decks",
            "alldecks",
            "practice",
            "library",
            "settings",
            "progress",
        ):
            _show_workspace(mw, _ws_active)
        else:
            return False
    except Exception as exc:  # pragma: no cover - refresh must never crash
        print(f"speedrun: workspace refresh failed: {exc}")
        return False
    return True


def _start_review(mw: aqt.AnkiQt) -> None:
    """Enter a real review session on the current deck, mirroring what the deck
    overview's 'Study Now' does (timebox + move to the reviewer). If nothing is
    studyable the reviewer bounces back to the overview on its own."""
    global _ws_active
    try:
        mw.col.startTimebox()
    except Exception:
        pass
    _ws_active = None
    mw.moveToState("review")
    _render_sidebar(mw)


def _review_today(mw: aqt.AnkiQt) -> None:
    """The dashboard's 'Start today's review': pick the deck with the most cards
    ready today (top-level nodes aggregate their subdecks, so this spans topics)
    and begin reviewing it right away. Falls back to the Decks hub when nothing is
    due, so the recommendation is never a dead end."""
    if mw.col is None:
        return
    try:
        from anki.decks import DeckId

        best_id, best_n = 0, 0
        for child in getattr(mw.col.sched.deck_due_tree(), "children", []):
            n = int(child.new_count) + int(child.learn_count) + int(child.review_count)
            if n > best_n:
                best_id, best_n = int(child.deck_id), n
    except Exception:
        best_id, best_n = 0, 0
    if not best_id or best_n == 0:
        _show_workspace(mw, "decks")
        return
    try:
        mw.col.decks.set_current(DeckId(best_id))  # type: ignore[attr-defined]
    except Exception:
        pass
    _start_review(mw)


def _review_topic(mw: aqt.AnkiQt, topic_id: str) -> None:
    """Build (or reuse) a filtered deck of this topic's cards and start reviewing
    it immediately, so 'Review memory cards' drops straight into a real review
    session rather than Anki's 'special deck / outside the normal schedule'
    overview (which read as an 'extra', secondary activity). The deck is torn
    down when the session ends so the native overview never surfaces."""
    try:
        from anki.decks import DeckId, FilteredDeckConfig
        from aqt.operations.scheduling import add_or_update_filtered_deck
    except Exception:
        _study_topic(mw, topic_id)
        return
    name = f"Speedrun review · {topic_id}"
    try:
        existing = mw.col.decks.by_name(name)
        existing_id = int(existing["id"]) if existing and existing.get("dyn") else 0
        deck = mw.col.sched.get_or_create_filtered_deck(deck_id=DeckId(existing_id))
        deck.name = name
        cfg = deck.config
        cfg.reschedule = True
        del cfg.search_terms[:]
        cfg.search_terms.append(
            FilteredDeckConfig.SearchTerm(
                search=f"tag:{topic_id}",
                limit=200,
                order=FilteredDeckConfig.SearchTerm.Order.OLDEST_REVIEWED_FIRST,
            )
        )
    except Exception:
        _study_topic(mw, topic_id)
        return

    def success(out: OpChangesWithId) -> None:
        try:
            mw.col.decks.set_current(DeckId(out.id))
            _state.review_filtered_deck_id = int(out.id)
            _state.review_return_topic = topic_id
        except Exception:
            _state.review_filtered_deck_id = None
            _state.review_return_topic = None
        _start_review(mw)

    add_or_update_filtered_deck(parent=mw, deck=deck).success(
        success
    ).run_in_background()


def _teardown_review_filtered_deck() -> str | None:
    """Delete the ephemeral 'Review memory cards' filtered deck, returning its
    cards to their home decks (Anki empties a filtered deck on removal). Returns
    the topic to route back to, or None when no Speedrun review was active."""
    fdeck = _state.review_filtered_deck_id
    if fdeck is None:
        return None
    return_topic = _state.review_return_topic
    _state.review_filtered_deck_id = None
    _state.review_return_topic = None
    mw = aqt.mw
    if mw is not None and mw.col is not None:
        try:
            from anki.decks import DeckId

            deck = mw.col.decks.get(DeckId(fdeck), default=False)
            if deck and deck.get("dyn"):
                mw.col.decks.remove([DeckId(fdeck)])
        except Exception as exc:  # pragma: no cover - cleanup must never crash
            print(f"speedrun: review deck teardown failed: {exc}")
    return return_topic


def _study_topic(mw: aqt.AnkiQt, topic_id: str) -> None:
    """Open the card browser filtered to a topic's tag, so the student can study
    or review exactly that content category."""
    try:
        browser = aqt.dialogs.open("Browser", mw)
        browser.search_for(f"tag:{topic_id}")
    except Exception:
        try:
            aqt.dialogs.open("Browser", mw, search=(f"tag:{topic_id}",))
        except Exception:
            tooltip(f"Search your cards for tag:{topic_id}")


def _open_native_decks(mw: aqt.AnkiQt) -> None:
    """Escape hatch from the topic view to Anki's native deck browser."""
    global _ws_active
    _ws_active = None
    mw.moveToState("deckBrowser")
    _render_sidebar(mw)


def _group_cards(mw: aqt.AnkiQt) -> None:
    """Auto-classify untagged cards into MCAT topics, then refresh the view so
    the newly-grouped cards appear under their content categories."""
    grouping.group_and_report(mw)
    _show_workspace(mw, "decks" if _ws_active != "dashboard" else "dashboard")


def _leave_workspace(mw: aqt.AnkiQt) -> None:
    global _ws_active
    _ws_active = None
    _open_home()
    _render_sidebar(mw)


def _on_state_changed(new_state: str, old_state: str) -> None:
    """A native Anki state move (Decks/overview/review) leaves the Speedrun
    screen; reflect that as the Decks section in the sidebar.

    On the first launch transition into the deck browser, take over the main
    webview with the Speedrun dashboard *synchronously* (inside the same
    moveToState turn, before the window repaints), so Anki's native deck list
    never flashes as the startup surface. The deferred first-run pass then adds
    the example deck / onboarding / sync on top of an already-Speedrun surface."""
    global _ws_active, _landed_home
    mw = aqt.mw
    # A Speedrun "Review memory cards" session just ended (any move out of the
    # reviewer while its ephemeral filtered deck is live): tear the deck down and
    # return to the Speedrun screen, so the native filtered-deck overview
    # (Options / Rebuild / Empty) never surfaces.
    if (
        _state.review_filtered_deck_id is not None
        and new_state != "review"
        and mw is not None
        and mw.col is not None
    ):
        return_topic = _teardown_review_filtered_deck()
        _ws_active = None
        if _modern_on(mw.col):
            try:
                if return_topic:
                    _show_topic_detail(mw, return_topic)
                else:
                    _show_workspace(mw, "dashboard")
            except Exception as exc:  # pragma: no cover - never block navigation
                print(f"speedrun: review return failed: {exc}")
            return
        _render_sidebar(mw)
        return
    if new_state == "deckBrowser":
        # The deck-browser state is the Speedrun home surface: DeckBrowser.show
        # (wrapped in _install_deck_browser_override) has already painted the
        # themed workspace. Leave _ws_active as it set it - resetting it here
        # would drop the sidebar highlight and make the next native refresh
        # forget which screen was showing. Just run the one-time first-run pass.
        if not _landed_home:
            _landed_home = True
            if mw is not None and mw.col is not None and _modern_on(mw.col):
                try:
                    _apply_shell_visibility(mw)
                    if not maybe_start_diagnostic(mw):
                        _show_workspace(mw, "dashboard")
                except Exception as exc:  # pragma: no cover - never block startup
                    print(f"speedrun: initial dashboard takeover failed: {exc}")
        _render_sidebar(aqt.mw)
        return
    # A genuinely native state (overview / review) now owns the screen.
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
    conf = int(data.get("conf", 0))
    explain = str(data.get("explain", ""))
    # Mandatory self-explain + forced confidence: the JS gates this before posting,
    # but guard here too so an attempt is never recorded without a selected answer,
    # a captured explanation, and a chosen confidence level (1-3).
    if sel < 0 or conf < 1 or not explain.strip():
        return
    _ws_practice.submit(sel, conf, explain)


def _ws_practice_voice(mw: aqt.AnkiQt, raw: str) -> None:
    """Voice self-explanation for the in-webview practice card.

    Opens the same on-device voice capture the reviewer/native dialogs use
    (pre-filled with whatever the student already typed), then writes the
    transcript back into the ``#sr-explain`` textarea so ``srSubmit`` picks it
    up. No-op once the question is answered."""
    from urllib.parse import unquote

    if _ws_practice is None or _ws_practice.answered:
        return
    dialog = _ExplainDialog(mw, initial_text=unquote(raw))
    if not dialog.exec():
        return
    payload = json.dumps(dialog.text())
    mw.web.eval(
        "(function(){var t=document.getElementById('sr-explain');"
        f"if(t){{t.value={payload};}}"
        "var b=document.getElementById('sr-pq-voice');"
        f"if(b&&{payload}.trim()){{b.classList.add('captured');}}"
        "if(window.srUpdate){srUpdate();}})();"
    )


class _WsPractice:
    """In-place counterpart of _PracticeDialog: records each answer as an
    exam-style attempt (question_type=2) and renders question/verdict/AI into
    the main webview instead of a popup dialog."""

    _CONFIDENCE = {1: 0.35, 2: 0.6, 3: 0.85}

    def __init__(
        self,
        mw: aqt.AnkiQt,
        questions: list[dict],
        *,
        session_id: str | None = None,
        on_complete: "Callable[[aqt.AnkiQt, _WsPractice], None] | None" = None,
    ) -> None:
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
        # Optional overrides so the same runner backs the onboarding diagnostic:
        # a dedicated session id and a completion hook, plus per-section tallies.
        self.session_id = session_id
        self.on_complete = on_complete
        self.section_stats: dict[str, list[int]] = {}
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
            # Gate the "Speak your reasoning" mic on on-device transcription being
            # importable, so the card degrades to text-only when it isn't.
            "voice": voice.is_available(),
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
        # Tally per MCAT section (subject -> section) for the diagnostic report.
        section = mcat.section_key_for_subject(str(q.get("topic", ""))) or "other"
        stat = self.section_stats.setdefault(section, [0, 0])
        stat[1] += 1
        if correct:
            stat[0] += 1
        self.selected = sel
        took_ms = max(int((time.monotonic() - self.shown_at) * 1000), 0)
        predicted = self._CONFIDENCE.get(conf_index)
        qtype = _question_type_for(q)
        req = speedrun_pb2.RecordAttemptRequest(
            card_id=q["card_id"],
            note_id=_note_id_for_card(self.mw, q["card_id"]),
            session_id=self.session_id or _state.session_id,
            answered_at_ms=int(time.time() * 1000),
            took_ms=took_ms,
            question_type=qtype,
            selected=sel,
            correct=correct,
            topic=str(q.get("topic", "")),
            signals=speedrun_pb2.ClassifyAttemptRequest(
                correct=correct,
                took_ms=took_ms,
                recall_failed=False,
                passage_evidence_missed=False,
                question_type=qtype,
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
        self._maybe_ai(q, sel, took_ms, predicted, correct, explanation)

    def _maybe_ai(self, q, selected, took_ms, predicted, correct, explanation) -> None:
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
            "question_type": _question_type_for(q),
            "self_explanation": explanation,
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
            try:
                self.mw.col._backend.compute_readiness()
            except Exception:
                pass
            if self.on_complete is not None:
                self.on_complete(self.mw, self)
                return
            tooltip(
                f"Practice complete: {self.correct_count}/{len(self.questions)} correct. "
                "These now feed your performance signal and calibration."
            )
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

    def __init__(self, mw: aqt.AnkiQt, initial_text: str | None = None) -> None:
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
        # Callers that already hold the field's text (e.g. the in-webview practice
        # card) pass it in; the reviewer path defaults to the session buffer.
        self.edit.setPlainText(
            _state.pending_explanation if initial_text is None else initial_text
        )

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
    """Turn backend QuestionItems into the dicts the practice dialog expects.
    Passage fields (CARS) ride along so a passage panel can stay pinned across
    the questions that share it."""
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
                "passage": payload.get("passage", ""),
                "passage_id": payload.get("passage_id", ""),
                "passage_title": payload.get("passage_title", ""),
            }
        )
    return questions


def _question_type_for(q: dict) -> int:
    """CARS/passage items record as passage MCQ so a miss is diagnosed as a
    reasoning/passage gap, never as forgotten memory."""
    if q.get("passage") or q.get("passage_id") or str(q.get("topic", "")) == "cars":
        return _QUESTION_TYPE_PASSAGE
    return _QUESTION_TYPE_DISCRETE


def _note_id_for_card(mw: aqt.AnkiQt, card_id: int) -> int:
    """The note backing a card, or 0 when the card isn't in the collection
    (held-out practice questions usually carry no real card)."""
    if not card_id or mw.col is None:
        return 0
    try:
        return int(mw.col.get_card(CardId(card_id)).nid)
    except Exception:
        return 0


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
    flagged = list(_state.flagged_card_ids)
    _state.reviewed_card_ids = []
    _state.flagged_card_ids = []
    if not card_ids and not flagged:
        return
    try:
        finished = sum(mw.col.sched.counts()) == 0
    except Exception:
        finished = False
    # Offer the round when the deck's due cards ran out, or whenever the user
    # explicitly flagged a miss for practice via the diagnosis cue (honor it even
    # on a manual mid-session exit).
    if not finished and not flagged:
        return
    # Practice the flagged concepts first, then the rest of the session.
    ordered = flagged + [c for c in card_ids if c not in flagged]
    from aqt.qt import QTimer

    QTimer.singleShot(500, lambda: _launch_reasoning_round(mw, ordered))


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
    """A themed Start/Skip card bridging the memory phase to application practice.

    Framed as "Practice" (not a separate "reasoning check") so it reads as the
    same feature as the Practice tab: both answer exam-style questions and both
    feed the performance score. Here it is pre-filled with the concepts just
    studied."""
    dialog = QDialog(mw)
    dialog.setWindowTitle("Speedrun practice")
    disable_help_button(dialog)

    title = _mark(QLabel("Practice: what you just studied"), role="display")
    body = _mark(
        QLabel(
            f"You finished your review. Answer {len(questions)} exam-style "
            "question(s) on today's concepts. This is the same Practice you can "
            "open any time - your answers count toward your performance score."
        ),
        role="muted",
    )
    body.setWordWrap(True)

    skip = QPushButton("Skip")
    qconnect(skip.clicked, dialog.reject)
    start = _mark(QPushButton("Start practice"), primary=True)
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

        # Confidence doubles as submit: tapping Low/Medium/High records the answer
        # at that confidence (1-3 map through _CONFIDENCE), so there is no separate
        # Submit button. _submit still guards on a selected answer + explanation.
        conf_row = QHBoxLayout()
        conf_row.addWidget(_mark(QLabel("How confident? Tap to submit:"), role="muted"))
        self.conf_btns: list[QPushButton] = []
        for ci, label in ((1, "Low"), (2, "Medium"), (3, "High")):
            btn = QPushButton(label)
            qconnect(btn.clicked, lambda _=False, c=ci: self._submit(c))
            self.conf_btns.append(btn)
            conf_row.addWidget(btn)
        conf_row.addStretch(1)

        self.explain_btn = QPushButton("Self-explain (required)")
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

        # Advance button (Next / Finish), shown only AFTER an answer is recorded;
        # before that the confidence buttons above are the submit.
        self.action_btn = _mark(QPushButton("Next question"), primary=True)
        self.action_btn.setVisible(False)
        qconnect(self.action_btn.clicked, self._next)

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
        for b in self.conf_btns:
            b.setEnabled(True)
        self.explain_btn.setEnabled(True)
        self.explain_btn.setText("Self-explain (required)")
        self.action_btn.setVisible(False)
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

    def _submit(self, conf_index: int) -> None:
        # conf_index (1-3) comes from the tapped confidence button, which is also
        # the submit action. Self-explain stays mandatory.
        selected = self.group.checkedId()
        if selected < 0:
            tooltip("Pick an answer first.")
            return
        if not self.pending_explanation.strip():
            tooltip("Self-explain your reasoning first.")
            return
        q = self.questions[self.index]
        correct = selected == q["correct_index"]
        if correct:
            self.correct_count += 1
        took_ms = max(int((time.monotonic() - self.shown_at) * 1000), 0)
        predicted = self._CONFIDENCE.get(conf_index)

        qtype = _question_type_for(q)
        req = speedrun_pb2.RecordAttemptRequest(
            card_id=q["card_id"],
            note_id=_note_id_for_card(self.mw, q["card_id"]),
            session_id=_state.session_id,
            answered_at_ms=int(time.time() * 1000),
            took_ms=took_ms,
            question_type=qtype,
            selected=selected,
            correct=correct,
            topic=str(q.get("topic", "")),
            signals=speedrun_pb2.ClassifyAttemptRequest(
                correct=correct,
                took_ms=took_ms,
                recall_failed=False,
                passage_evidence_missed=False,
                question_type=qtype,
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
        for b in self.conf_btns:
            b.setEnabled(False)
        self.explain_btn.setEnabled(False)
        # The confidence buttons submitted; now reveal the advance button.
        self.action_btn.setText(
            "Finish" if self.index + 1 >= len(self.questions) else "Next question"
        )
        self.action_btn.setVisible(True)

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
            "question_type": _question_type_for(q),
            "self_explanation": self.pending_explanation,
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
    """Edit the exam profile via the shared editor (the same dialog onboarding
    uses), so setting and editing a target look identical."""
    library.open_exam_target(mw)
