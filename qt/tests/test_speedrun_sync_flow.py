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
    src = _sync_source()
    fallback = src.split('tooltip("Sync server did not become ready in time.")', 1)[
        1
    ].split("return False", 1)[0]

    assert "_advertise_mdns(lan_ip(), port)" in fallback
