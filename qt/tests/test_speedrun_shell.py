# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Regression tests for the app-shell "native deck list clobbers the themed
Home" bug.

The Speedrun dashboard is rendered as an overlay on Anki's ``deckBrowser``
state. Anki repaints the native deck list on ``moveToState("deckBrowser")`` ->
``DeckBrowser.show`` and on ``mw.reset()`` -> ``DeckBrowser.refresh`` (e.g. the
``mw.reset()`` in ``aqt/sync.py`` right after a sync). Left alone those paint the
native list over the themed screen, so after a sync or a finished review the
user lands on Anki's deck list instead of the dashboard (and readiness looks
"stuck" because the gauge is hidden behind it).

``_install_deck_browser_override`` wraps both methods so, when the modern shell
is on, they re-render the current Speedrun workspace instead of the native list.
"""

from __future__ import annotations

import inspect
import types

from aqt import speedrun


def test_deck_browser_override_installed_after_collection_load():
    """The override wraps mw.deckBrowser.show/refresh, but mw.deckBrowser is
    created in setupDeckBrowser - AFTER setup()/setupHooks - so installing it in
    setup() is a no-op. It must be installed from _on_collection_loaded (which
    runs after the screens exist, just before the first moveToState). Guard the
    install site so it can't regress back to setup()-only."""
    assert "_install_deck_browser_override(mw)" in inspect.getsource(
        speedrun._on_collection_loaded
    )


def _fake_mw(modern: bool):
    native = {"show": 0, "refresh": 0}
    db = types.SimpleNamespace()
    db.show = lambda *a, **k: native.__setitem__("show", native["show"] + 1)
    db.refresh = lambda *a, **k: native.__setitem__("refresh", native["refresh"] + 1)

    class _Col:
        def get_config(self, key, default=None):
            return modern if key == speedrun._CFG_MODERN else default

    return types.SimpleNamespace(col=_Col(), deckBrowser=db), native


def test_deck_browser_paints_themed_workspace_when_modern(monkeypatch):
    mw, native = _fake_mw(modern=True)
    rendered: list[str] = []
    monkeypatch.setattr(
        speedrun, "_show_workspace", lambda m, tab: rendered.append(tab)
    )
    monkeypatch.setattr(speedrun, "_ws_active", "dashboard")

    speedrun._install_deck_browser_override(mw)
    mw.deckBrowser.refresh()  # e.g. the post-sync mw.reset()
    mw.deckBrowser.show()  # e.g. moveToState back from a finished review

    # Both native repaints are redirected to the themed dashboard, and the native
    # deck list is never painted.
    assert rendered == ["dashboard", "dashboard"]
    assert native == {"show": 0, "refresh": 0}


def test_deck_browser_keeps_current_workspace(monkeypatch):
    """A repaint re-renders whatever Speedrun screen owns the surface (e.g. the
    themed all-decks list), not always the dashboard."""
    mw, _ = _fake_mw(modern=True)
    rendered: list[str] = []
    monkeypatch.setattr(
        speedrun, "_show_workspace", lambda m, tab: rendered.append(tab)
    )
    monkeypatch.setattr(speedrun, "_ws_active", "alldecks")

    speedrun._install_deck_browser_override(mw)
    mw.deckBrowser.refresh()
    assert rendered == ["alldecks"]


def test_deck_browser_keeps_pairing_screen(monkeypatch):
    """A sync fires mw.reset() mid-pairing; the QR screen must survive rather
    than bounce to the dashboard (it isn't a _show_workspace tab)."""
    mw, _ = _fake_mw(modern=True)
    ws: list[str] = []
    sync: list[bool] = []
    monkeypatch.setattr(speedrun, "_show_workspace", lambda m, tab: ws.append(tab))
    monkeypatch.setattr(speedrun, "_show_sync_pair", lambda m, **k: sync.append(True))
    monkeypatch.setattr(speedrun, "_ws_active", "sync")

    speedrun._install_deck_browser_override(mw)
    mw.deckBrowser.refresh()
    assert sync == [True]
    assert ws == []


def test_reviewer_front_buttons_html():
    """One-tap reveal+rate renders 4 data-ease buttons that post reviewrate."""
    html = speedrun._reviewer_front_buttons_html()
    for n in (1, 2, 3, 4):
        assert f'data-ease="{n}"' in html
        assert f"speedrun:reviewrate:{n}" in html
    assert "Forgot" in html  # Again is relabeled on the front too


class _FakeReviewer:
    def __init__(self):
        self.state = "question"
        self.calls: list = []

    def _showAnswer(self):
        self.calls.append("show")
        self.state = "answer"

    def _answerCard(self, ease):
        self.calls.append(("rate", ease))


def test_reviewer_reveal_rate_reveals_then_rates():
    rv = _FakeReviewer()
    mw = types.SimpleNamespace(reviewer=rv, state="review")
    speedrun._reviewer_reveal_rate(mw, "3")
    assert rv.calls == ["show", ("rate", 3)]


def test_reviewer_reveal_rate_guards():
    # out-of-range ease and non-review state are no-ops
    rv = _FakeReviewer()
    speedrun._reviewer_reveal_rate(
        types.SimpleNamespace(reviewer=rv, state="review"), "9"
    )
    speedrun._reviewer_reveal_rate(
        types.SimpleNamespace(reviewer=rv, state="deckBrowser"), "2"
    )
    assert rv.calls == []


def test_setup_registers_notesync_on_sync_lifecycle():
    """Encode/decode must ride the native sync lifecycle hooks (not just the
    hand-wired local path) so note-encoded attempts also travel over AnkiWeb."""
    src = inspect.getsource(speedrun.setup)
    assert "gui_hooks.sync_will_start.append(_on_sync_will_start)" in src
    assert "gui_hooks.sync_did_finish.append(_on_sync_did_finish)" in src


def test_sync_hooks_encode_before_and_decode_after(monkeypatch):
    """sync_will_start encodes attempts to notes; sync_did_finish decodes them
    back and recomputes readiness."""
    calls: list[str] = []

    class _Col:
        def _load_scheduler(self):
            calls.append("load_sched")

    mw = types.SimpleNamespace(col=_Col())
    monkeypatch.setattr(speedrun.aqt, "mw", mw)
    monkeypatch.setattr(
        speedrun.speedrun_notesync,
        "encode_attempts",
        lambda col: calls.append("encode") or 0,
    )
    monkeypatch.setattr(
        speedrun.speedrun_notesync,
        "decode_attempts",
        lambda col: calls.append("decode") or 0,
    )
    monkeypatch.setattr(
        speedrun.library,
        "refresh_readiness_after_sync",
        lambda col: calls.append("readiness"),
    )

    speedrun._on_sync_will_start()
    speedrun._on_sync_did_finish()

    assert calls == ["encode", "decode", "load_sched", "readiness"]


def test_sync_hooks_are_noops_without_collection(monkeypatch):
    monkeypatch.setattr(speedrun.aqt, "mw", types.SimpleNamespace(col=None))
    # Must not raise when no collection is open.
    speedrun._on_sync_will_start()
    speedrun._on_sync_did_finish()


def test_guard_macos_cursor_crash_neutralizes_on_mac(monkeypatch):
    """On macOS the override cursor is neutralized (it SIGTRAPs via toCGImage on
    macOS 26 + bundled Qt); a native crash can't be caught, so the call itself
    must be removed."""

    class _App:
        @staticmethod
        def setOverrideCursor(*_a, **_k):
            raise AssertionError("real setOverrideCursor must not run on macOS")

        @staticmethod
        def restoreOverrideCursor(*_a, **_k):
            raise AssertionError("real restoreOverrideCursor must not run on macOS")

    monkeypatch.setattr(speedrun, "is_mac", True)
    monkeypatch.setattr(speedrun, "QApplication", _App)
    speedrun._guard_macos_cursor_crash()
    # Neutralized: calling it no longer reaches the crashing Qt path.
    _App.setOverrideCursor("x")
    _App.restoreOverrideCursor()


def test_guard_macos_cursor_crash_is_noop_off_mac(monkeypatch):
    calls: list = []

    class _App:
        @staticmethod
        def setOverrideCursor(*_a, **_k):
            calls.append("set")

    monkeypatch.setattr(speedrun, "is_mac", False)
    monkeypatch.setattr(speedrun, "QApplication", _App)
    speedrun._guard_macos_cursor_crash()
    _App.setOverrideCursor("x")
    assert calls == ["set"]  # left untouched on non-macOS


def test_sync_to_ankiweb_bypasses_pin_and_runs_stock_sync():
    """Sync to AnkiWeb sets the bypass flag, drops the local auth/url so the
    stock login targets AnkiWeb, then runs Anki's saved native sync."""
    calls: list = []

    class _PM:
        def sync_auth(self):
            return None  # not signed in to AnkiWeb yet -> force login

        def clear_sync_auth(self):
            calls.append("clear_auth")

        def set_current_sync_url(self, u):
            calls.append(("cur", u))

        def set_custom_sync_url(self, u):
            calls.append(("custom", u))

    mw = types.SimpleNamespace(
        col=object(),
        pm=_PM(),
        _speedrun_native_sync=lambda: calls.append("native"),
    )
    speedrun._sync_to_ankiweb(mw)

    assert mw._speedrun_ankiweb_sync is True
    # local auth + both urls cleared BEFORE the stock sync runs
    assert (
        "clear_auth" in calls and ("cur", None) in calls and ("custom", None) in calls
    )
    assert calls.index("clear_auth") < calls.index("native")
    assert calls[-1] == "native"


def test_sync_to_ankiweb_reuses_existing_ankiweb_login():
    """When already signed in to AnkiWeb, don't clear the auth - just sync."""
    calls: list = []

    class _Auth:
        endpoint = "https://sync.ankiweb.net/"

    class _PM:
        def sync_auth(self):
            return _Auth()

        def clear_sync_auth(self):
            calls.append("clear_auth")

    mw = types.SimpleNamespace(
        col=object(),
        pm=_PM(),
        _speedrun_native_sync=lambda: calls.append("native"),
    )
    speedrun._sync_to_ankiweb(mw)
    assert calls == ["native"]  # no clear_auth -> reused the AnkiWeb session


def test_is_ankiweb_endpoint():
    assert speedrun._is_ankiweb_endpoint("https://sync.ankiweb.net/")
    assert speedrun._is_ankiweb_endpoint("https://usa.ankiweb.net")
    assert not speedrun._is_ankiweb_endpoint("http://127.0.0.1:55413/")
    assert not speedrun._is_ankiweb_endpoint("")
    assert not speedrun._is_ankiweb_endpoint(None)


def test_ankiweb_signed_in_detection():
    def mk(auth):
        return types.SimpleNamespace(pm=types.SimpleNamespace(sync_auth=lambda: auth))

    def auth(ep):
        return types.SimpleNamespace(endpoint=ep)

    assert speedrun._ankiweb_signed_in(mk(auth("https://sync.ankiweb.net/"))) is True
    # An empty endpoint means the default (AnkiWeb) endpoint -> signed in.
    assert speedrun._ankiweb_signed_in(mk(auth(""))) is True
    # A local/self-hosted endpoint is not an AnkiWeb session.
    assert speedrun._ankiweb_signed_in(mk(auth("http://127.0.0.1:27701/"))) is False
    assert speedrun._ankiweb_signed_in(mk(None)) is False


def test_ankiweb_sign_out_clears_stored_session(monkeypatch):
    calls: list = []
    monkeypatch.setattr(speedrun, "_show_sync_pair", lambda *a, **k: None)
    mw = types.SimpleNamespace(
        pm=types.SimpleNamespace(clear_sync_auth=lambda: calls.append("clear")),
    )
    speedrun._ankiweb_sign_out(mw)
    assert calls == ["clear"]


def test_sync_to_ankiweb_noop_without_native_handler(monkeypatch):
    # No saved stock handler -> don't half-run (flag stays unset).
    monkeypatch.setattr(speedrun, "tooltip", lambda *a, **k: None)
    mw = types.SimpleNamespace(col=object(), pm=object())
    speedrun._sync_to_ankiweb(mw)
    assert getattr(mw, "_speedrun_ankiweb_sync", False) is False


def test_local_sync_clears_ankiweb_flag(monkeypatch):
    """A local/phone sync re-engages the local-server pin (clears the flag)."""
    monkeypatch.setattr(speedrun, "_login_to_local", lambda mw, cb: None)
    mw = types.SimpleNamespace(col=object())
    mw._speedrun_ankiweb_sync = True
    speedrun._sync_to_local(mw)
    assert mw._speedrun_ankiweb_sync is False


def test_deck_browser_falls_through_when_modern_off(monkeypatch):
    mw, native = _fake_mw(modern=False)
    rendered: list[str] = []
    monkeypatch.setattr(
        speedrun, "_show_workspace", lambda m, tab: rendered.append(tab)
    )

    speedrun._install_deck_browser_override(mw)
    mw.deckBrowser.refresh()

    # Legacy UI: leave Anki's native deck browser untouched.
    assert rendered == []
    assert native["refresh"] == 1
