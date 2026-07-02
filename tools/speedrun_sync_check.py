#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Headless two-way collection-sync check against a self-hosted anki-sync-server.

Proves the desktop side of the sync test (projectspec 7b) without the GUI:
two independent collections sync through the shared Rust sync engine + the
self-hosted server, and edits flow both ways with nothing lost or duplicated.

  SYNC_ENDPOINT=http://127.0.0.1:8080/ SYNC_USERNAME=demo SYNC_PASSWORD=demo \\
    PYTHONPATH=out/pylib:pylib out/pyenv/bin/python tools/speedrun_sync_check.py

(or run tools/speedrun_sync_check.sh). Exits non-zero on the first failure.
"""

from __future__ import annotations

import os
import tempfile

from anki.collection import Collection

ENDPOINT = os.environ.get("SYNC_ENDPOINT", "http://127.0.0.1:8080/")
USERNAME = os.environ.get("SYNC_USERNAME", "demo")
PASSWORD = os.environ.get("SYNC_PASSWORD", "demo")

PROBE_A = "SYNCPROBEALPHA"
PROBE_B = "SYNCPROBEBETA"


def _fresh_collection() -> tuple[Collection, str]:
    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    os.unlink(path)  # Collection() creates it fresh
    return Collection(path), path


def _add_note(col: Collection, front: str) -> None:
    model = col.models.by_name("Basic")
    note = col.new_note(model)
    note["Front"] = front
    note["Back"] = "probe"
    col.add_note(note, col.decks.id("Default"))


def _note_count(col: Collection, needle: str) -> int:
    return len(col.find_notes(f'"{needle}"'))


def _sync(col: Collection, path: str, label: str) -> Collection:
    """Run one sync, resolving an initial full up/down; returns the live col
    (reopened after a full download, which replaces the local file)."""
    auth = col.sync_login(USERNAME, PASSWORD, ENDPOINT)
    out = col.sync_collection(auth, False)
    req = out.required
    if req == out.NO_CHANGES:
        # sync_collection performs the incremental merge in-call; NO_CHANGES
        # means "no *full* sync needed", i.e. the incremental sync is complete.
        print(f"  [{label}] incremental sync complete (no full sync needed)")
    elif req == out.NORMAL_SYNC:
        print(f"  [{label}] normal sync (incremental merge)")
    elif req == out.FULL_UPLOAD:
        col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
        print(f"  [{label}] full upload (seeded the server)")
    elif req == out.FULL_DOWNLOAD:
        col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
        col.close()
        col = Collection(path)
        print(f"  [{label}] full download (mirrored the server)")
    else:
        raise SystemExit(f"FAIL: [{label}] unexpected sync state {req}")
    return col


def main() -> int:
    print(f"Sync check against {ENDPOINT} as {USERNAME}")
    col_a, path_a = _fresh_collection()
    col_b, path_b = _fresh_collection()
    failed = False
    try:
        print("\n[1] Collection A adds a card and syncs up")
        _add_note(col_a, PROBE_A)
        col_a = _sync(col_a, path_a, "A")

        print("\n[2] Collection B (independent) syncs down")
        col_b = _sync(col_b, path_b, "B")
        got = _note_count(col_b, PROBE_A)
        print(f"      B sees A's card: {got == 1} ({got})")
        failed |= got != 1

        print("\n[3] B adds its own card and syncs; A syncs back (two-way)")
        _add_note(col_b, PROBE_B)
        col_b = _sync(col_b, path_b, "B")
        col_a = _sync(col_a, path_a, "A")

        a_has_a, a_has_b = _note_count(col_a, PROBE_A), _note_count(col_a, PROBE_B)
        b_has_a, b_has_b = _note_count(col_b, PROBE_A), _note_count(col_b, PROBE_B)
        total_a, total_b = col_a.note_count(), col_b.note_count()
        print(f"      A has [A,B]=[{a_has_a},{a_has_b}] total={total_a}")
        print(f"      B has [A,B]=[{b_has_a},{b_has_b}] total={total_b}")

        # Both cards land on both devices exactly once: nothing lost, nothing doubled.
        failed |= not (a_has_a == 1 and a_has_b == 1 and b_has_a == 1 and b_has_b == 1)
        failed |= not (total_a == 2 and total_b == 2)

        print("\n" + ("speedrun sync check: FAIL" if failed else "speedrun sync check: PASS (two-way, no loss, no duplication)"))
        return 1 if failed else 0
    finally:
        for c in (col_a, col_b):
            try:
                c.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
