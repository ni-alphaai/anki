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
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aqt
from aqt.utils import tooltip

# Collection-config keys. The user + token identify the single server account
# that both the desktop client and the phone authenticate with; the port is
# persisted so a paired phone's cached USB URL survives desktop restarts.
_CFG_USER = "speedrunServerUser"
_CFG_TOKEN = "speedrunServerToken"
_CFG_PAIRED = "speedrunPaired"
_CFG_PORT = "speedrunServerPort"

_DEFAULT_USER = "speedrun"

# A stable default port. Persisting the actually-chosen port per collection keeps
# the phone's saved http://127.0.0.1:<port>/ USB URL valid across desktop
# restarts (the adb-reverse tunnel is re-established on the same port).
_DEFAULT_PORT = 27701

# The QR pairing code is short-lived: a stale screenshot cannot be used to pair
# after this window. The phone enforces the same deadline at scan time.
PAIRING_CODE_TTL_MS = 5 * 60 * 1000

# The hosted server stops after this much time with no sync activity, so it is
# never left listening indefinitely; re-open "Sync with phone" to host again.
IDLE_LIMIT_SECS = 30 * 60

# Live server process state (one embedded server per running app).
_proc: Any = None
_port: int | None = None
_log_file: Any = None
_base: str | None = None
# Monotonic timestamp of the last sync activity, for idle auto-expiry.
_last_activity: float | None = None
# Last-known USB tunnel result (ok, human message). The adb subprocess calls
# that produce it run off the Qt thread, so this cache lets the sidebar chip and
# the Sync screen render the USB state without a blocking adb call. Reset when
# the server (re)starts or stops.
_USB_STATUS_CHECKING: tuple[bool, str] = (False, "Checking USB tunnel\u2026")
_usb_status: tuple[bool, str] = _USB_STATUS_CHECKING


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


def _port_free(port: int) -> bool:
    """True when nothing else is bound to ``port`` (a plain probe, no reuse)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False


def _stable_port(col) -> int:
    """The server port for this collection, reused across launches.

    Callers reap any orphaned server first, so a persisted port is free again on
    a normal restart and gets reused -- keeping the phone's cached USB URL valid.
    Only when the saved port is taken by an unrelated process (or none is saved
    yet) do we pick a new one and persist it.
    """
    try:
        saved = int(col.get_config(_CFG_PORT, 0) or 0)
    except Exception:
        saved = 0
    if 1024 <= saved <= 65535 and _port_free(saved):
        return saved
    port = _DEFAULT_PORT if _port_free(_DEFAULT_PORT) else _free_port()
    try:
        col.set_config(_CFG_PORT, port)
    except Exception:
        pass
    return port


# --- idle auto-expiry -------------------------------------------------------
#
# The hosted server should not linger forever. We track the time of the last
# sync activity (every start_server/sync touches it) and let a caller-driven
# watchdog stop the server once it has been idle past IDLE_LIMIT_SECS.


def touch_activity() -> None:
    """Mark the server as freshly used, resetting the idle-expiry countdown."""
    global _last_activity
    _last_activity = time.monotonic()


def idle_seconds() -> float | None:
    """Seconds since the last sync activity, or None when not hosting."""
    if not is_running() or _last_activity is None:
        return None
    return time.monotonic() - _last_activity


def stop_if_idle() -> bool:
    """Stop the hosted server when it has been idle past IDLE_LIMIT_SECS.

    Returns True only when it was actually stopped."""
    idle = idle_seconds()
    if idle is not None and idle >= IDLE_LIMIT_SECS:
        stop_server()
        return True
    return False


def _accepting(port: int, timeout: float = 0.2) -> bool:
    """True when the loopback port accepts a TCP connection.

    A plain connect is a better readiness signal than an HTTP probe here: the
    server serves no body on ``/`` (any response means "up"), and a connect is
    bounded so it never hangs the Qt thread. A connect dropped by a local
    firewall fails fast at ``timeout`` rather than blocking.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def is_running() -> bool:
    return _proc is not None and _proc.poll() is None and _port is not None


def _discard_proc() -> None:
    """Reap the child we currently hold and close its log handle.

    Terminating + waiting the previous process, and closing its ``server.log``
    file object, before dropping the references is what keeps a respawn from
    leaking a process ("subprocess still running") or the log file handle.
    """
    global _proc, _log_file
    proc, _proc = _proc, None
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
    # Only orphans from a previous run are reaped here, never the child this
    # process is actively running (that would be the respawn-loop bug).
    if _proc is not None and _proc.poll() is None and pid == _proc.pid:
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


# Readiness window for a freshly spawned server. Bounded so it never hangs the
# Qt thread; because start is idempotent this wait only runs on the first spawn.
# A healthy server accepts within ~1s, so this window mainly caps how long we
# wait before falling back to "process alive" when loopback probes are firewalled.
_READY_TRIES = 12
_READY_STEP_MS = 150


def _await_ready(base: str, port: int) -> bool:
    """Wait (bounded) for the freshly spawned server to become usable.

    A real startup failure (e.g. a locked collection) makes the process exit,
    which we detect and report. Otherwise we return once it accepts a loopback
    connection -- or, if such probes are dropped by a local firewall, once it
    has stayed alive through the window, since the subsequent sync surfaces any
    genuine connectivity error instead of us spinning here.
    """
    global _port
    from aqt.qt import QEventLoop, QTimer

    for _ in range(_READY_TRIES):
        if _proc is None or _proc.poll() is not None:  # died on startup
            detail = _server_log_tail(base)
            tooltip(
                "Sync server exited on startup; see speedrun_syncserver/server.log."
                + (f"\n{detail}" if detail else "")
            )
            _discard_proc()
            _port = None
            _remove_pidfile(base)
            return False
        if _accepting(port):
            return True
        loop = QEventLoop()
        QTimer.singleShot(_READY_STEP_MS, loop.quit)
        loop.exec()
    return _proc is not None and _proc.poll() is None


def start_server(mw: aqt.AnkiQt, *, on_ready: Callable[[], None] | None = None) -> bool:
    """Start the embedded sync server (idempotent). Returns True if it is up.

    Idempotency keys on our own live child process, not a network probe: a
    loopback probe can be dropped by a local firewall, and re-probing a server
    we already started would otherwise make us reap and respawn it in a loop.

    The mDNS advert and USB tunnel are best-effort side effects run off the Qt
    thread (see ``_run_side_effects``); the returned readiness only reflects the
    server listening, which is all the sync itself needs. ``on_ready`` fires on
    the main thread once those side effects finish, so callers can refresh a
    status badge without blocking the click.
    """
    global _proc, _port, _log_file, _base
    if is_running():
        touch_activity()
        return True
    if mw.col is None:
        return False
    binary = _binary_path()
    if binary is None:
        tooltip(
            "Sync server binary not found. Build it, or set SPEEDRUN_SYNC_SERVER_BIN."
        )
        return False

    # Drop any dead child (and its log handle) we still hold before respawning.
    _discard_proc()

    user, token = creds(mw.col)
    base = _server_base(mw)
    _base = base
    # Clear a server orphaned by a previous run (crash/force-quit) that still
    # holds the lock; never our own live child (guarded in _reap_stale_server).
    _reap_stale_server(base)
    port = _stable_port(mw.col)
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
        _discard_proc()
        _port = None
        return False
    _port = port
    _write_pidfile(base, _proc.pid)

    if not _await_ready(base, port):
        return False
    touch_activity()
    _run_side_effects(mw, port, on_ready)
    return True


def _run_side_effects(
    mw: aqt.AnkiQt, port: int, on_ready: Callable[[], None] | None = None
) -> None:
    """Advertise mDNS and bring up the USB tunnel off the Qt main thread.

    Both are best-effort: the sync only needs the server listening, so the adb
    subprocess calls (``adb devices`` at 5s, ``adb reverse`` at 10s) and the
    mDNS registration must never block the Sync click. This runs on the
    no-collection executor so it never contends with a collection op, and it
    refreshes the cached USB status the UI reads. ``on_ready`` (if any) fires on
    the main thread once the tunnel result is known.
    """
    global _usb_status
    _usb_status = _USB_STATUS_CHECKING

    def work() -> None:
        _advertise_mdns(lan_ip(), port)
        set_usb_status(*setup_usb_tunnel(port))

    def done(fut: Any) -> None:
        if callable(on_ready):
            try:
                on_ready()
            except Exception:
                pass

    try:
        mw.taskman.run_in_background(work, done, uses_collection=False)
    except Exception:
        pass


def stop_server() -> None:
    global _port, _base, _last_activity, _usb_status
    _withdraw_mdns()
    _discard_proc()
    _port = None
    _last_activity = None
    _usb_status = _USB_STATUS_CHECKING
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


def set_usb_status(ok: bool, status: str) -> None:
    """Record the latest USB tunnel result for the UI to read without an adb call."""
    global _usb_status
    _usb_status = (ok, status)


def usb_status_cached() -> tuple[bool, str]:
    """The last-known USB tunnel result, refreshed off-thread by the side-effect
    task; used by the sidebar chip and Sync screen so rendering never blocks on
    the adb subprocess timeouts."""
    return _usb_status


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
    for manual fallback). Includes ``usb_url`` for adb-reverse sync and ``exp``,
    a short-lived deadline (epoch millis) the phone enforces at scan time so a
    stale screenshot of the code cannot pair later."""
    user, token = creds(mw.col)
    return {
        "v": 1,
        "url": phone_url(),
        "usb_url": local_url(),
        "user": user,
        "token": token,
        "exp": int(time.time() * 1000) + PAIRING_CODE_TTL_MS,
    }


# The client-sync orchestration (login to the local server, run the native
# Anki sync, mark paired) lives in speedrun.py, which owns the saved original
# toolbar-sync handler; this module stays focused on the server + credentials.


def status(mw: aqt.AnkiQt) -> dict:
    """State for the sidebar sync chip."""
    if is_running():
        usb_ok, _ = usb_status_cached()
        if usb_ok:
            return {
                "state": "ok",
                "label": "USB sync ready",
                "detail": local_url(),
            }
        return {"state": "ok", "label": "Sharing on LAN", "detail": phone_url()}
    if mw.col is not None and is_paired(mw.col):
        return {"state": "idle", "label": "Sync with phone", "detail": "Tap to start"}
    return {"state": "idle", "label": "Sync with phone", "detail": "Not connected"}
