#!/usr/bin/env python
# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tiny headless sync client used as the 'desktop' second device in the 7b test.

Usage (with the built pylib on PYTHONPATH):
  sr_dev.py <collection_path> syncdown|sync|counts|addnote "<text>"
"""

from __future__ import annotations

import os
import sys
import time

from anki import scheduler_pb2
from anki.collection import Collection

ENDPOINT = os.environ.get("SYNC_ENDPOINT", "http://127.0.0.1:8080/")
USER = os.environ.get("SYNC_USERNAME", "demo")
PW = os.environ.get("SYNC_PASSWORD", "demo")


def do_sync(col: Collection, path: str):
    auth = col.sync_login(USER, PW, ENDPOINT)
    out = col.sync_collection(auth, False)
    r = out.required
    if r == out.FULL_UPLOAD:
        col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
        return col, "full_upload"
    if r == out.FULL_DOWNLOAD:
        col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
        col.close()
        return Collection(path), "full_download"
    return col, ("no_changes" if r == out.NO_CHANGES else "incremental")


def counts(col: Collection) -> dict:
    return {
        "cards": col.card_count(),
        "notes": col.note_count(),
        "revlog": col.db.scalar("select count() from revlog") or 0,
    }


def main() -> int:
    path, cmd = sys.argv[1], sys.argv[2]
    col = Collection(path)
    try:
        if cmd in ("syncdown", "sync"):
            col, how = do_sync(col, path)
            print(f"sync: {how}")
        elif cmd == "addnote":
            text = sys.argv[3]
            m = col.models.by_name("Basic")
            n = col.new_note(m)
            n["Front"] = text
            n["Back"] = "from desktop"
            col.add_note(n, col.decks.id("Default"))
            print(f"added note: {text}")
        elif cmd == "review":
            n = int(sys.argv[3])
            done = 0
            for _ in range(n):
                q = col.sched.get_queued_cards(fetch_limit=1)
                if not q.cards:
                    break
                qc = q.cards[0]
                ans = scheduler_pb2.CardAnswer(
                    card_id=qc.card.id,
                    current_state=qc.states.current,
                    new_state=qc.states.good,
                    rating=scheduler_pb2.CardAnswer.GOOD,
                    answered_at_millis=int(time.time() * 1000),
                    milliseconds_taken=3000,
                )
                col.sched.answer_card(ans)
                done += 1
            print(f"reviewed: {done}")
        print(f"counts: {counts(col)}")
        return 0
    finally:
        col.close()


if __name__ == "__main__":
    raise SystemExit(main())
