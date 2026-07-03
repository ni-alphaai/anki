# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Speedrun one-button phone sync (desktop side).

The desktop hosts the sync server: it runs the bundled ``anki-sync-server``
binary as a child process bound to the LAN, mints a stable per-collection
credential, and exposes a "Sync with phone" flow -- start the server, push this
device's collection to it, and show a QR code the phone scans to pair in one
step (no URL/username/password typing on either device).

The same shared Rust engine powers the server (this binary) and both clients
(desktop + Android), so the phone connects to ``http://<lan-ip>:<port>`` while
the desktop syncs to ``http://127.0.0.1:<port>``; both talk to one server-side
collection, which is what makes the sync genuinely bidirectional.
"""

from __future__ import annotations

import atexit
import os
import secrets
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import aqt
from aqt.utils import tooltip

# Collection-config keys. The user + token identify the single server account
# that both the desktop client and the phone authenticate with.
_CFG_USER = "speedrunServerUser"
_CFG_TOKEN = "speedrunServerToken"
_CFG_PAIRED = "speedrunPaired"

_DEFAULT_USER = "speedrun"

# Live server process state (one embedded server per running app).
_proc: Any = None
_port: int | None = None
_log_file: Any = None


# --- credentials ------------------------------------------------------------


def creds(col) -> tuple[str, str]:
    """A stable (user, token) for this collection's server, created once."""
    user = str(col.get_config(_CFG_USER, "") or "") or _DEFAULT_USER
    token = str(col.get_config(_CFG_TOKEN, "") or "")
    if not token:
        token = secrets.token_hex(16)
        col.set_config(_CFG_USER, user)
        col.set_config(_CFG_TOKEN, token)
    return user, token


def is_paired(col) -> bool:
    try:
        return bool(col.get_config(_CFG_PAIRED, False))
    except Exception:
        return False


def mark_paired(col) -> None:
    try:
        col.set_config(_CFG_PAIRED, True)
    except Exception:
        pass


# --- server process ---------------------------------------------------------


def _binary_path() -> str | None:
    """Locate the anki-sync-server executable (dev build or packaged)."""
    override = os.environ.get("SPEEDRUN_SYNC_SERVER_BIN")
    candidates = []
    if override:
        candidates.append(Path(override))
    here = Path(__file__).resolve()
    # dev layout: <repo>/qt/aqt/speedrun_sync.py -> <repo>/out/bin/anki-sync-server
    candidates.append(here.parents[2] / "out" / "bin" / "anki-sync-server")
    # packaged layouts: alongside the interpreter / app resources
    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir / "anki-sync-server")
    candidates.append(exe_dir.parent / "Resources" / "anki-sync-server")
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _server_base(mw: aqt.AnkiQt) -> str:
    """A per-collection folder holding the server-side collection + media."""
    col_path = mw.col.path
    base = os.path.join(os.path.dirname(col_path), "speedrun_syncserver")
    os.makedirs(base, exist_ok=True)
    return base


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def _health_ok(port: int, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def is_running() -> bool:
    return _proc is not None and _proc.poll() is None and _port is not None


def start_server(mw: aqt.AnkiQt) -> bool:
    """Start the embedded sync server (idempotent). Returns True if it is up."""
    global _proc, _port, _log_file
    if is_running() and _port is not None and _health_ok(_port):
        return True
    if mw.col is None:
        return False
    binary = _binary_path()
    if binary is None:
        tooltip(
            "Sync server binary not found. Build it, or set SPEEDRUN_SYNC_SERVER_BIN."
        )
        return False

    user, token = creds(mw.col)
    base = _server_base(mw)
    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "SYNC_HOST": "0.0.0.0",
            "SYNC_PORT": str(port),
            "SYNC_BASE": base,
            "SYNC_USER1": f"{user}:{token}",
        }
    )
    try:
        _log_file = open(os.path.join(base, "server.log"), "w")  # noqa: SIM115
        _proc = subprocess.Popen(  # noqa: S603
            [binary], env=env, stdout=_log_file, stderr=subprocess.STDOUT
        )
    except Exception as exc:
        tooltip(f"Could not start sync server: {exc}")
        return False
    _port = port

    # Wait briefly for the listener to accept connections.
    from aqt.qt import QEventLoop, QTimer

    for _ in range(40):  # up to ~8s
        if _proc.poll() is not None:  # died on startup
            tooltip(
                "Sync server exited on startup; see speedrun_syncserver/server.log."
            )
            _proc = None
            _port = None
            return False
        if _health_ok(port):
            _advertise_mdns(lan_ip(), port)
            return True
        loop = QEventLoop()
        QTimer.singleShot(200, loop.quit)
        loop.exec()
    tooltip("Sync server did not become ready in time.")
    return _health_ok(port)


def stop_server() -> None:
    global _proc, _port, _log_file
    _withdraw_mdns()
    proc, _proc, _port = _proc, None, None
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    if _log_file is not None:
        try:
            _log_file.close()
        except Exception:
            pass
        _log_file = None


atexit.register(stop_server)


# --- mDNS advertising -------------------------------------------------------
#
# Advertise the running server as `_speedrun-sync._tcp` on the LAN so the phone
# can re-find the desktop after its IP changes, without re-scanning the QR. Only
# the address is broadcast -- the credential still comes from the QR pairing.

_MDNS_TYPE = "_speedrun-sync._tcp.local."
_zc: Any = None
_svc_info: Any = None


def _advertise_mdns(ip: str, port: int) -> None:
    global _zc, _svc_info
    _withdraw_mdns()
    try:
        from zeroconf import ServiceInfo, Zeroconf

        host = socket.gethostname()
        info = ServiceInfo(
            _MDNS_TYPE,
            f"Speedrun ({host}).{_MDNS_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties={b"v": b"1"},
            server=f"{host}.local.",
        )
        zc = Zeroconf()
        zc.register_service(info)
        _zc, _svc_info = zc, info
    except Exception as exc:  # pragma: no cover - discovery is best-effort
        print(f"speedrun: mDNS advertise failed: {exc}")


def _withdraw_mdns() -> None:
    global _zc, _svc_info
    zc, info = _zc, _svc_info
    _zc, _svc_info = None, None
    if zc is None:
        return
    try:
        if info is not None:
            zc.unregister_service(info)
        zc.close()
    except Exception:
        pass


# --- addressing + pairing payload -------------------------------------------


def lan_ip() -> str:
    """The machine's primary LAN IP (best effort), for the phone to reach us."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
    except Exception:
        return "127.0.0.1"


def phone_url() -> str:
    return f"http://{lan_ip()}:{_port}" if _port else ""


def local_url() -> str:
    return f"http://127.0.0.1:{_port}" if _port else ""


def pairing_payload(mw: aqt.AnkiQt) -> dict:
    """The data the phone needs to pair, encoded into the QR (and shown as text
    for manual fallback)."""
    user, token = creds(mw.col)
    return {"v": 1, "url": phone_url(), "user": user, "token": token}


# The client-sync orchestration (login to the local server, run the native
# Anki sync, mark paired) lives in speedrun.py, which owns the saved original
# toolbar-sync handler; this module stays focused on the server + credentials.


def status(mw: aqt.AnkiQt) -> dict:
    """State for the sidebar sync chip."""
    if is_running():
        return {"state": "ok", "label": "Sharing on LAN", "detail": phone_url()}
    if mw.col is not None and is_paired(mw.col):
        return {"state": "idle", "label": "Sync with phone", "detail": "Tap to start"}
    return {"state": "idle", "label": "Sync with phone", "detail": "Not connected"}
