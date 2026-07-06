# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Regression tests for the desktop one-button sync server lifecycle.

Root cause these guard against: ``stop_server`` only reaps the sync-server
child within the same live process. If Anki crashes or is force-quit, the
child is orphaned (reparented to init) and keeps the server-side collection's
media db locked; the next ``start_server`` then fails with ``Locked`` -- the
"Sync server exited on startup" error. ``_reap_stale_server`` clears that
orphan via a pidfile before spawning a fresh server.

These are integration tests: they spawn the real ``anki-sync-server`` binary,
so they skip cleanly when it has not been built.
"""

from __future__ import annotations

import http.server
import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_BIN = _REPO / "out" / "bin" / "anki-sync-server"

pytestmark = pytest.mark.skipif(
    not _BIN.is_file(), reason="anki-sync-server binary not built (out/bin)"
)


def _load_module() -> types.ModuleType:
    """Load speedrun_sync.py with ``aqt`` stubbed (only Qt-free helpers used)."""
    if "aqt" not in sys.modules:
        aqt_stub = types.ModuleType("aqt")
        aqt_stub.AnkiQt = object  # type: ignore[attr-defined]
        sys.modules["aqt"] = aqt_stub
    if "aqt.utils" not in sys.modules:
        utils_stub = types.ModuleType("aqt.utils")
        utils_stub.tooltip = lambda *a, **k: None  # type: ignore[attr-defined]
        sys.modules["aqt.utils"] = utils_stub
    path = _REPO / "qt" / "aqt" / "speedrun_sync.py"
    spec = importlib.util.spec_from_file_location("speedrun_sync_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _spawn_server(base: Path, port: int, logname: str):
    env = dict(os.environ)
    env.update(
        {
            "SYNC_HOST": "127.0.0.1",
            "SYNC_PORT": str(port),
            "SYNC_BASE": str(base),
            "SYNC_USER1": "speedrun:testtoken",
        }
    )
    log = open(base / logname, "w")  # noqa: SIM115
    proc = subprocess.Popen(  # noqa: S603
        [str(_BIN)], env=env, stdout=log, stderr=subprocess.STDOUT
    )
    return proc, log


def _health(port: int, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _wait_health(port: int, secs: float = 8.0) -> bool:
    deadline = int(secs / 0.2)
    for _ in range(deadline):
        if _health(port):
            return True
        time.sleep(0.2)
    return False


def _kill(proc, log) -> None:
    try:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    except Exception:
        pass
    try:
        log.close()
    except Exception:
        pass


def test_reap_stale_server_frees_the_collection_lock(tmp_path: Path) -> None:
    ss = _load_module()
    base = tmp_path / "speedrun_syncserver"
    base.mkdir()

    # An orphaned server from a previous run holds the server-side lock.
    port_a = _free_port()
    proc_a, log_a = _spawn_server(base, port_a, "a.log")
    try:
        assert _wait_health(port_a), "stale server A never came up"
        ss._write_pidfile(str(base), proc_a.pid)

        # Bug reproduction: a fresh server on the same base cannot open the
        # locked media db and dies on startup.
        port_b = _free_port()
        proc_b, log_b = _spawn_server(base, port_b, "b.log")
        try:
            for _ in range(20):
                if proc_b.poll() is not None:
                    break
                time.sleep(0.1)
            assert proc_b.poll() is not None, "server B should have died on the lock"
            assert "Locked" in (base / "b.log").read_text()
        finally:
            _kill(proc_b, log_b)

        # Fix: reaping the orphan releases the lock so a new server starts.
        ss._reap_stale_server(str(base))
        assert proc_a.poll() is not None, "server A should have been reaped"
        assert not os.path.exists(ss._pidfile_path(str(base))), "pidfile not cleared"

        port_c = _free_port()
        proc_c, log_c = _spawn_server(base, port_c, "c.log")
        try:
            assert _wait_health(port_c), (
                "server did not start after reaping:\n" + (base / "c.log").read_text()
            )
        finally:
            _kill(proc_c, log_c)
    finally:
        _kill(proc_a, log_a)


def test_reap_is_noop_without_pidfile(tmp_path: Path) -> None:
    ss = _load_module()
    base = tmp_path / "speedrun_syncserver"
    base.mkdir()
    # No pidfile -> nothing to reap, must not raise.
    ss._reap_stale_server(str(base))


def test_reap_clears_pidfile_for_dead_pid(tmp_path: Path) -> None:
    ss = _load_module()
    base = tmp_path / "speedrun_syncserver"
    base.mkdir()
    # A dead / recycled pid must be treated as absent and the pidfile cleaned.
    dead = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    dead.wait()
    ss._write_pidfile(str(base), dead.pid)
    ss._reap_stale_server(str(base))
    assert not os.path.exists(ss._pidfile_path(str(base)))


def test_reap_ignores_unrelated_pid(tmp_path: Path) -> None:
    ss = _load_module()
    base = tmp_path / "speedrun_syncserver"
    base.mkdir()
    # A live process that is NOT our sync server must never be killed.
    other = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    try:
        ss._write_pidfile(str(base), other.pid)
        ss._reap_stale_server(str(base))
        assert other.poll() is None, "reap killed an unrelated process"
        # ...and it dropped the stale pidfile since that pid is not our server.
        assert not os.path.exists(ss._pidfile_path(str(base)))
    finally:
        other.kill()
        other.wait(timeout=5)


class _NoHealthHandler(http.server.BaseHTTPRequestHandler):
    """A listener that answers only ``/`` and ``/sync/*`` (404), never ``/health``
    -- proving readiness detection does not depend on a health route."""

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a: object) -> None:  # silence test noise
        pass


def test_accepting_detects_a_listener_without_a_health_route(tmp_path: Path) -> None:
    """Readiness is a plain TCP connect, so it works even when the server serves
    no ``/health`` (the original respawn-loop hypothesis), and returns False
    fast when nothing is listening."""
    ss = _load_module()

    dead = _free_port()
    assert ss._accepting(dead) is False

    srv = http.server.HTTPServer(("127.0.0.1", 0), _NoHealthHandler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        assert ss._accepting(port) is True
    finally:
        srv.shutdown()
        thread.join(timeout=5)


def test_start_server_is_idempotent(tmp_path: Path) -> None:
    """A second start while one is already hosting must reuse the live child, not
    reap-and-respawn it (the bug: repeated spawns leaking the previous process)."""
    ss = _load_module()
    base = tmp_path / "speedrun_syncserver"
    base.mkdir()
    port = _free_port()
    proc, log = _spawn_server(base, port, "s.log")
    try:
        assert _wait_health(port), "server never came up"
        # State as it is right after a first successful start_server.
        ss._proc = proc
        ss._port = port
        ss._log_file = log
        ss._base = str(base)
        ss._write_pidfile(str(base), proc.pid)

        class _Col:
            path = str(base / "collection.anki2")

            def get_config(self, key: str, default: object = None) -> object:
                return {"speedrunServerPort": port}.get(key, default)

            def set_config(self, key: str, value: object) -> None:
                pass

        mw = types.SimpleNamespace(col=_Col())

        result = ss.start_server(mw)
        assert result is True
        assert ss._proc is proc, "idempotent start replaced the running child"
        assert ss._proc.poll() is None, "idempotent start killed the running child"
    finally:
        ss._proc = None  # detach so the module's atexit stop_server is a no-op
        ss._log_file = None
        _kill(proc, log)


def test_side_effects_run_off_the_ui_thread(monkeypatch) -> None:
    """The Sync click must not block on adb: the mDNS advert + USB tunnel (the
    slow ``adb devices``/``adb reverse`` subprocess calls) are dispatched to a
    background task rather than run inline on the Qt thread. Regression guard for
    the seconds-long freeze on Sync."""
    ss = _load_module()

    # A tunnel setup standing in for the adb subprocess timeouts; if it were run
    # on the calling thread the click would stall, so it must only run when the
    # captured background task is invoked.
    ran = {"tunnel": False}

    def fake_tunnel(port: object = None) -> tuple[bool, str]:
        ran["tunnel"] = True
        return True, "USB ready"

    monkeypatch.setattr(ss, "setup_usb_tunnel", fake_tunnel)
    monkeypatch.setattr(ss, "_advertise_mdns", lambda ip, port: None)
    monkeypatch.setattr(ss, "lan_ip", lambda: "127.0.0.1")

    captured: dict = {}

    class _Taskman:
        def run_in_background(self, task, on_done=None, uses_collection=True):
            captured["task"] = task
            captured["on_done"] = on_done
            captured["uses_collection"] = uses_collection

    fired = {"ready": False}
    mw = types.SimpleNamespace(taskman=_Taskman())
    ss._run_side_effects(mw, 27701, on_ready=lambda: fired.__setitem__("ready", True))

    # Dispatch returned immediately, without touching adb on the calling thread,
    # and off the collection executor so it never contends with a collection op.
    assert ran["tunnel"] is False
    assert captured["uses_collection"] is False
    assert ss.usb_status_cached() == ss._USB_STATUS_CHECKING

    # Running the background task performs the adb work and caches the result the
    # UI reads.
    captured["task"]()
    assert ran["tunnel"] is True
    assert ss.usb_status_cached() == (True, "USB ready")

    # The done callback runs on the main thread and lets the caller refresh a
    # status badge once the tunnel result is known.
    captured["on_done"](None)
    assert fired["ready"] is True


def test_reap_spares_our_own_live_child(tmp_path: Path) -> None:
    """The pidfile points at our live child; reaping must never terminate it."""
    ss = _load_module()
    base = tmp_path / "speedrun_syncserver"
    base.mkdir()
    port = _free_port()
    proc, log = _spawn_server(base, port, "s.log")
    try:
        assert _wait_health(port), "server never came up"
        ss._proc = proc
        ss._port = port
        ss._write_pidfile(str(base), proc.pid)
        ss._reap_stale_server(str(base))
        assert proc.poll() is None, "reap killed our own live child"
    finally:
        ss._proc = None
        _kill(proc, log)
