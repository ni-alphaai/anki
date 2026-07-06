# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Regression tests for high-level Speedrun desktop sync orchestration.

These stay source-level because the bug is a GUI navigation race: opening the
Sync screen for the first time seeds the embedded server asynchronously, and the
normal sync completion path was navigating away from the QR screen before the
phone could pair.
"""

from __future__ import annotations

from pathlib import Path

_SPEEDRUN = Path(__file__).resolve().parents[1] / "aqt" / "speedrun.py"
_SPEEDRUN_SYNC = Path(__file__).resolve().parents[1] / "aqt" / "speedrun_sync.py"


def _source() -> str:
    return _SPEEDRUN.read_text(encoding="utf-8")


def _sync_source() -> str:
    return _SPEEDRUN_SYNC.read_text(encoding="utf-8")


def test_first_time_pairing_seed_keeps_the_sync_screen_visible() -> None:
    src = _source()

    assert "land_home" in src, "sync completion must expose a navigation toggle"
    assert (
        "_sync_to_local(\n            mw,\n            on_done=lambda: _show_sync_pair"
    ) in src
    assert "land_home=False" in src


def test_directional_sync_logs_in_without_preliminary_auto_sync() -> None:
    src = _source()
    directional = src.split("def _sync_directional_local", 1)[1].split(
        "def _sync_pull_from_phone", 1
    )[0]

    assert "_login_to_local(" in directional
    assert "_sync_to_local(" not in directional


def test_slow_server_ready_path_still_advertises_mdns() -> None:
    """A server that is alive but whose loopback readiness probe is blocked (e.g.
    a firewall dropping the SYN) must still be treated as up, and the up path
    advertises mDNS + sets up the USB tunnel, so a slow/firewalled start is never
    left unadvertised."""
    src = _sync_source()

    # Readiness falls back to "process alive" rather than a hard failure.
    await_ready = src.split("def _await_ready", 1)[1].split("def start_server", 1)[0]
    assert "_proc.poll() is None" in await_ready

    # When readiness succeeds (including that fallback), the server is advertised
    # and the tunnel is set up (both now off-thread via _run_side_effects).
    started = src.split("if not _await_ready(base, port):", 1)[1].split(
        "def stop_server", 1
    )[0]
    assert "_advertise_mdns(lan_ip(), port)" in started
    assert "setup_usb_tunnel(port)" in started


def test_sync_click_does_not_block_on_adb() -> None:
    """The seconds-long Sync freeze was the adb subprocess timeouts (``adb
    devices`` at 5s, ``adb reverse`` at 10s) plus mDNS running inline on the Qt
    thread inside ``start_server``. They must be dispatched to a background task,
    and the UI must render USB state from the cache, never a blocking adb call.
    """
    sync_src = _sync_source()
    ui_src = _source()

    # The slow adb + mDNS work lives in a background helper, dispatched off the
    # collection executor so it never contends with a collection op.
    side_effects = sync_src.split("def _run_side_effects", 1)[1].split(
        "def stop_server", 1
    )[0]
    assert "run_in_background(" in side_effects
    assert "uses_collection=False" in side_effects
    assert "setup_usb_tunnel(port)" in side_effects
    assert "_advertise_mdns(lan_ip(), port)" in side_effects

    # start_server hands that work to _run_side_effects instead of calling adb /
    # mDNS inline on the calling (UI) thread.
    started = sync_src.split("if not _await_ready(base, port):", 1)[1].split(
        "def _run_side_effects", 1
    )[0]
    assert "_run_side_effects(mw, port" in started
    assert "setup_usb_tunnel(" not in started
    assert "_advertise_mdns(" not in started

    # The sidebar chip renders from the cache, not a blocking adb reverse --list.
    status_section = sync_src.split("def status(", 1)[1]
    assert "usb_status_cached()" in status_section
    assert "usb_tunnel_active()" not in status_section

    # The Sync screen renders USB status from the cache too.
    assert "srsync.usb_status_cached()" in ui_src
    assert "usb_ok, usb_status = srsync.setup_usb_tunnel()" not in ui_src
