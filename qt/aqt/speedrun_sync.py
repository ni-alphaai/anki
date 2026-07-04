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
import signal
import socket
import subprocess
import sys
import time
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
_base: str | None = None


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


# --- stale-server reaping ---------------------------------------------------
#
# ``stop_server`` only reaps the child within this live process (via atexit).
# If Anki crashes or is force-quit, the child sync-server is reparented to init
# and keeps the server-side collection's media db locked. The next
# ``start_server`` then spawns a fresh server against the same base, which fails
# to open the locked db ("Sync server exited on startup ... Locked"). We record
# the child's pid in a pidfile and, before spawning, reap any orphan it names.


def _pidfile_path(base: str) -> str:
    return os.path.join(base, "server.pid")


def _write_pidfile(base: str, pid: int) -> None:
    try:
        with open(_pidfile_path(base), "w") as fh:
            fh.write(str(pid))
    except Exception:
        pass


def _read_pidfile(base: str) -> int | None:
    try:
        with open(_pidfile_path(base)) as fh:
            return int(fh.read().strip())
    except Exception:
        return None


def _remove_pidfile(base: str) -> None:
    try:
        os.remove(_pidfile_path(base))
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _process_is_sync_server(pid: int) -> bool:
    """True only if ``pid`` is a live process whose command is our sync server.

    The command check guards against a recycled pid belonging to something else,
    so reaping never signals an unrelated process.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # existence probe; does not actually signal
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False
    try:
        out = subprocess.run(  # noqa: S603
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return False
    return "anki-sync-server" in out.stdout


def _reap_stale_server(base: str) -> None:
    """Terminate an orphaned sync server (from a crashed run) holding ``base``'s
    lock, so a fresh server can start. No-op when there is nothing to reap."""
    pid = _read_pidfile(base)
    if pid is None:
        return
    if not _process_is_sync_server(pid):
        _remove_pidfile(base)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        _remove_pidfile(base)
        return
    for _ in range(50):  # wait up to ~5s for it to release the lock
        if not _process_is_sync_server(pid):
            break
        time.sleep(0.1)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    _remove_pidfile(base)


def _server_log_tail(base: str) -> str:
    """The last non-empty line of the server log, for an actionable message."""
    try:
        with open(os.path.join(base, "server.log")) as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
        return lines[-1] if lines else ""
    except Exception:
        return ""


def start_server(mw: aqt.AnkiQt) -> bool:
    """Start the embedded sync server (idempotent). Returns True if it is up."""
    global _proc, _port, _log_file, _base
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
    _base = base
    # Clear any server orphaned by a previous run that still holds the lock;
    # otherwise the fresh server below dies on startup with "Locked".
    _reap_stale_server(base)
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
    _write_pidfile(base, _proc.pid)

    # Wait briefly for the listener to accept connections.
    from aqt.qt import QEventLoop, QTimer

    for _ in range(40):  # up to ~8s
        if _proc.poll() is not None:  # died on startup
            detail = _server_log_tail(base)
            tooltip(
                "Sync server exited on startup; see speedrun_syncserver/server.log."
                + (f"\n{detail}" if detail else "")
            )
            _proc = None
            _port = None
            _remove_pidfile(base)
            return False
        if _health_ok(port):
            _advertise_mdns(lan_ip(), port)
            setup_usb_tunnel(port)
            return True
        loop = QEventLoop()
        QTimer.singleShot(200, loop.quit)
        loop.exec()
    tooltip("Sync server did not become ready in time.")
    if _health_ok(port):
        _advertise_mdns(lan_ip(), port)
        setup_usb_tunnel(port)
        return True
    return False


def stop_server() -> None:
    global _proc, _port, _log_file, _base
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
    if _base is not None:
        _remove_pidfile(_base)
        _base = None


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
    return f"http://127.0.0.1:{_port}/" if _port else ""


def _adb_path() -> str | None:
    """Locate the adb binary (dev default: Android SDK platform-tools)."""
    candidates: list[Path | str] = []
    if override := os.environ.get("SPEEDRUN_ADB_BIN"):
        candidates.append(override)
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if android_home:
        candidates.append(Path(android_home) / "platform-tools" / "adb")
    candidates.append(Path.home() / "Library/Android/sdk/platform-tools/adb")
    for cand in candidates:
        path = Path(cand)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def adb_device_connected() -> bool:
    """True when ``adb devices`` reports at least one authorized device."""
    adb = _adb_path()
    if adb is None:
        return False
    try:
        out = subprocess.run(  # noqa: S603
            [adb, "devices"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    for line in out.stdout.splitlines()[1:]:
        if line.strip().endswith("\tdevice"):
            return True
    return False


def usb_tunnel_active(port: int | None = None) -> bool:
    """True when adb reverse forwards the sync port to the desktop."""
    port = port if port is not None else _port
    if port is None:
        return False
    adb = _adb_path()
    if adb is None:
        return False
    try:
        out = subprocess.run(  # noqa: S603
            [adb, "reverse", "--list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    needle = f"tcp:{port}"
    return any(needle in line for line in out.stdout.splitlines())


def setup_usb_tunnel(port: int | None = None) -> tuple[bool, str]:
    """Forward ``tcp:<port>`` on a USB-connected phone to this desktop.

    Returns ``(ok, human-readable status)``. Best-effort: LAN sync still works
    when no device is plugged in."""
    port = port if port is not None else _port
    if port is None:
        return False, "Sync server is not running."
    adb = _adb_path()
    if adb is None:
        return False, "adb not found (install Android platform-tools)."
    if not adb_device_connected():
        return False, "Plug in your phone and allow USB debugging."
    try:
        subprocess.run(  # noqa: S603
            [adb, "reverse", f"tcp:{port}", f"tcp:{port}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except Exception as exc:
        return False, f"USB tunnel failed: {exc}"
    return True, f"USB ready at {local_url()}"


def pairing_payload(mw: aqt.AnkiQt) -> dict:
    """The data the phone needs to pair, encoded into the QR (and shown as text
    for manual fallback). Includes ``usb_url`` for adb-reverse sync."""
    user, token = creds(mw.col)
    return {
        "v": 1,
        "url": phone_url(),
        "usb_url": local_url(),
        "user": user,
        "token": token,
    }


# The client-sync orchestration (login to the local server, run the native
# Anki sync, mark paired) lives in speedrun.py, which owns the saved original
# toolbar-sync handler; this module stays focused on the server + credentials.


def status(mw: aqt.AnkiQt) -> dict:
    """State for the sidebar sync chip."""
    if is_running():
        if usb_tunnel_active():
            return {
                "state": "ok",
                "label": "USB sync ready",
                "detail": local_url(),
            }
        return {"state": "ok", "label": "Sharing on LAN", "detail": phone_url()}
    if mw.col is not None and is_paired(mw.col):
        return {"state": "idle", "label": "Sync with phone", "detail": "Tap to start"}
    return {"state": "idle", "label": "Sync with phone", "detail": "Not connected"}
