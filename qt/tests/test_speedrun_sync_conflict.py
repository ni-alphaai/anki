# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the desktop sync conflict-policy orchestration and error messages.

These bring the desktop to parity with the phone: a genuine two-sided conflict
is resolved by the persisted "on conflict" preference (ask / prefer phone /
prefer desktop) with a transparent overwrite message, and a connectivity or auth
failure is reported accurately rather than as an invalid-URL error.

They import ``aqt.speedrun`` (which pulls in Qt) but never construct a
QApplication, so they run headlessly like ``test_speedrun_desktop_invariants``.
"""

from __future__ import annotations

from types import SimpleNamespace

from anki.errors import NetworkError, SyncError, SyncErrorKind
from aqt import speedrun


class _FakeCol:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = dict(cfg or {})

    def get_config(self, key, default=None):
        return self.cfg.get(key, default)

    def set_config(self, key, value):
        self.cfg[key] = value


def _mw(policy: str | None = None) -> SimpleNamespace:
    cfg = {} if policy is None else {speedrun._CFG_SYNC_CONFLICT: policy}
    return SimpleNamespace(col=_FakeCol(cfg))


# --- persisted preference ---------------------------------------------------


class TestConflictPolicyConfig:
    def test_default_is_ask(self) -> None:
        assert speedrun._sync_conflict_policy(_FakeCol()) == "ask"

    def test_reads_prefer_sides(self) -> None:
        assert (
            speedrun._sync_conflict_policy(
                _FakeCol({speedrun._CFG_SYNC_CONFLICT: "phone"})
            )
            == "phone"
        )
        assert (
            speedrun._sync_conflict_policy(
                _FakeCol({speedrun._CFG_SYNC_CONFLICT: "desktop"})
            )
            == "desktop"
        )

    def test_unknown_value_falls_back_to_ask(self) -> None:
        assert (
            speedrun._sync_conflict_policy(
                _FakeCol({speedrun._CFG_SYNC_CONFLICT: "garbage"})
            )
            == "ask"
        )


# --- accurate error messages ------------------------------------------------


class TestSyncErrorMessage:
    def test_network_error_is_connectivity_not_invalid_url(self) -> None:
        msg = speedrun._sync_error_message(NetworkError("boom", None, None, None))
        assert msg == speedrun._SYNC_CONNECTIVITY_MSG
        assert "invalid" not in msg.lower()
        assert "url" not in msg.lower()

    def test_auth_error_is_reported_as_rejected_key(self) -> None:
        err = SyncError("nope", None, None, None, SyncErrorKind.AUTH)
        assert speedrun._sync_error_message(err) == speedrun._SYNC_AUTH_MSG

    def test_timeout_string_maps_to_connectivity(self) -> None:
        # A reqwest network failure arrives as a plain message with an empty URL;
        # it must never be mislabeled as an invalid URL.
        assert (
            speedrun._sync_error_message(Exception("error sending request for url ()"))
            == speedrun._SYNC_CONNECTIVITY_MSG
        )
        assert (
            speedrun._sync_error_message(Exception("operation timed out"))
            == speedrun._SYNC_CONNECTIVITY_MSG
        )

    def test_other_error_is_passed_through(self) -> None:
        msg = speedrun._sync_error_message(Exception("schema up to date"))
        assert msg == "Sync unavailable: schema up to date"

    def test_non_auth_sync_error_is_passed_through(self) -> None:
        err = SyncError("server busy", None, None, None, SyncErrorKind.OTHER)
        assert speedrun._sync_error_message(err) == "Sync unavailable: server busy"


# --- conflict resolution routing --------------------------------------------


class TestResolveFullSyncConflict:
    def _patch_full_sync(self, monkeypatch):
        calls: dict = {"download": None, "upload": None, "tooltips": [], "prompt": None}
        monkeypatch.setattr(
            speedrun,
            "_local_full_download",
            lambda mw, usn, cb: calls.__setitem__("download", (usn, cb)),
        )
        monkeypatch.setattr(
            speedrun,
            "_local_full_upload",
            lambda mw, usn, cb: calls.__setitem__("upload", (usn, cb)),
        )
        monkeypatch.setattr(
            speedrun,
            "_prompt_conflict_direction",
            lambda mw, usn, done: calls.__setitem__("prompt", (usn, done)),
        )
        monkeypatch.setattr(
            speedrun, "tooltip", lambda msg, *a, **k: calls["tooltips"].append(msg)
        )
        return calls

    def test_prefer_phone_downloads_and_reports_overwrite(self, monkeypatch) -> None:
        calls = self._patch_full_sync(monkeypatch)
        done = {"ran": False}
        speedrun._resolve_full_sync_conflict(
            _mw("phone"), 7, lambda: done.__setitem__("ran", True)
        )
        assert calls["download"] is not None and calls["upload"] is None
        assert calls["prompt"] is None
        usn, after = calls["download"]
        assert usn == 7
        # The overwrite is transparent: message fires when the download finishes.
        after()
        assert done["ran"] is True
        assert any("kept the phone's data" in m for m in calls["tooltips"])
        assert any("desktop copy was overwritten" in m for m in calls["tooltips"])

    def test_prefer_desktop_uploads_and_reports_overwrite(self, monkeypatch) -> None:
        calls = self._patch_full_sync(monkeypatch)
        done = {"ran": False}
        speedrun._resolve_full_sync_conflict(
            _mw("desktop"), None, lambda: done.__setitem__("ran", True)
        )
        assert calls["upload"] is not None and calls["download"] is None
        _usn, after = calls["upload"]
        after()
        assert done["ran"] is True
        assert any("kept the desktop's data" in m for m in calls["tooltips"])
        assert any("phone's copy was overwritten" in m for m in calls["tooltips"])

    def test_ask_prompts_for_direction(self, monkeypatch) -> None:
        calls = self._patch_full_sync(monkeypatch)
        done = object()
        speedrun._resolve_full_sync_conflict(_mw("ask"), 3, done)
        assert calls["download"] is None and calls["upload"] is None
        assert calls["prompt"] == (3, done)


class TestPromptConflictDirection:
    def _run(self, monkeypatch, choice: int):
        import aqt.utils

        captured: dict = {"buttons": None, "download": False, "upload": False}
        monkeypatch.setattr(
            speedrun,
            "_local_full_download",
            lambda mw, usn, cb: captured.__setitem__("download", True) or cb(),
        )
        monkeypatch.setattr(
            speedrun,
            "_local_full_upload",
            lambda mw, usn, cb: captured.__setitem__("upload", True) or cb(),
        )

        def fake_dialog(text, callback, buttons=None, **kwargs):
            captured["buttons"] = buttons
            captured["text"] = text
            callback(choice)

        monkeypatch.setattr(aqt.utils, "ask_user_dialog", fake_dialog)
        done = {"ran": False}
        speedrun._prompt_conflict_direction(
            _mw(), 1, lambda: done.__setitem__("ran", True)
        )
        return captured, done

    def test_offers_both_directions_and_a_cancel(self, monkeypatch) -> None:
        captured, _done = self._run(monkeypatch, choice=2)
        assert captured["buttons"] == ["Use phone data", "Use desktop data", "Cancel"]
        # The conflict copy must not imply an invalid URL.
        assert "different data" in captured["text"]

    def test_choosing_phone_downloads(self, monkeypatch) -> None:
        captured, done = self._run(monkeypatch, choice=0)
        assert captured["download"] is True and captured["upload"] is False
        assert done["ran"] is True

    def test_choosing_desktop_uploads(self, monkeypatch) -> None:
        captured, done = self._run(monkeypatch, choice=1)
        assert captured["upload"] is True and captured["download"] is False
        assert done["ran"] is True

    def test_cancel_overwrites_nothing(self, monkeypatch) -> None:
        captured, done = self._run(monkeypatch, choice=2)
        assert captured["download"] is False and captured["upload"] is False
        # Cancel still completes the flow (no hang), just without any overwrite.
        assert done["ran"] is True
