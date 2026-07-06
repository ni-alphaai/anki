# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for the desktop sync module's stable port, pairing-code expiry,
and idle auto-expiry helpers.

These exercise only Qt-free / binary-free helpers, so (unlike the lifecycle
integration tests) they run without the anki-sync-server binary built.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
import time
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _load_module() -> types.ModuleType:
    """Load speedrun_sync.py fresh (with ``aqt`` stubbed) so each test gets its
    own module globals (``_proc``, ``_last_activity`` etc.)."""
    if "aqt" not in sys.modules:
        aqt_stub = types.ModuleType("aqt")
        aqt_stub.AnkiQt = object  # type: ignore[attr-defined]
        sys.modules["aqt"] = aqt_stub
    if "aqt.utils" not in sys.modules:
        utils_stub = types.ModuleType("aqt.utils")
        utils_stub.tooltip = lambda *a, **k: None  # type: ignore[attr-defined]
        sys.modules["aqt.utils"] = utils_stub
    path = _REPO / "qt" / "aqt" / "speedrun_sync.py"
    spec = importlib.util.spec_from_file_location(
        "speedrun_sync_expiry_under_test", path
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeCol:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = dict(cfg or {})

    def get_config(self, key, default=None):
        return self.cfg.get(key, default)

    def set_config(self, key, value):
        self.cfg[key] = value


def _a_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


# --- stable port ------------------------------------------------------------


def test_stable_port_reuses_saved_free_port() -> None:
    ss = _load_module()
    free = _a_free_port()
    col = _FakeCol({ss._CFG_PORT: free})
    assert ss._stable_port(col) == free
    # Reuse must not re-persist a different port.
    assert col.cfg[ss._CFG_PORT] == free


def test_stable_port_replaces_occupied_saved_port() -> None:
    ss = _load_module()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("", 0))
        busy.listen()
        taken = int(busy.getsockname()[1])
        col = _FakeCol({ss._CFG_PORT: taken})
        chosen = ss._stable_port(col)
        assert chosen != taken
        assert col.cfg[ss._CFG_PORT] == chosen


def test_stable_port_persists_when_unset() -> None:
    ss = _load_module()
    col = _FakeCol()
    chosen = ss._stable_port(col)
    assert 1024 <= chosen <= 65535
    assert col.cfg[ss._CFG_PORT] == chosen


# --- pairing-code expiry ----------------------------------------------------


def test_pairing_payload_stamps_future_expiry() -> None:
    ss = _load_module()
    mw = types.SimpleNamespace(col=_FakeCol())
    before = int(time.time() * 1000)
    payload = ss.pairing_payload(mw)
    after = int(time.time() * 1000)
    exp = payload["exp"]
    assert before + ss.PAIRING_CODE_TTL_MS <= exp <= after + ss.PAIRING_CODE_TTL_MS
    assert payload["user"] and payload["token"]


# --- idle auto-expiry -------------------------------------------------------


def test_idle_helpers_are_noop_when_not_hosting() -> None:
    ss = _load_module()
    assert ss.idle_seconds() is None
    assert ss.stop_if_idle() is False


def test_stop_if_idle_stops_when_past_limit(monkeypatch) -> None:
    ss = _load_module()
    monkeypatch.setattr(ss, "is_running", lambda: True)
    ss._last_activity = time.monotonic() - (ss.IDLE_LIMIT_SECS + 1)
    stopped = {"v": False}
    monkeypatch.setattr(ss, "stop_server", lambda: stopped.__setitem__("v", True))
    assert ss.stop_if_idle() is True
    assert stopped["v"] is True


def test_stop_if_idle_keeps_recent_server(monkeypatch) -> None:
    ss = _load_module()
    monkeypatch.setattr(ss, "is_running", lambda: True)
    ss.touch_activity()

    def _boom() -> None:
        raise AssertionError("must not stop a freshly-active server")

    monkeypatch.setattr(ss, "stop_server", _boom)
    assert ss.stop_if_idle() is False
