# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Regression tests for "Clear study data" on the Sync-with-phone screen.

Two bugs are guarded here:

1. Clearing must actually empty the Speedrun evidence layer (``sr_attempts`` +
   the cached ``sr_readiness`` snapshots) and leave readiness honestly
   abstaining, without touching the user's cards/decks.
2. Clearing must keep the user on the themed Speedrun surface. The old flow
   called the native ``deckBrowser.refresh()`` (an async repaint that clobbered
   the themed screen a tick later), dropping the user onto Anki's deck list.

The data test builds a real headless collection (like the Rust service tests);
the redirect test inspects the source, mirroring
``test_speedrun_desktop_invariants``. Neither constructs a QApplication.
"""

from __future__ import annotations

import inspect

from anki.collection import Collection
from aqt import speedrun


def _make_col(tmp_path) -> Collection:
    return Collection(str(tmp_path / "collection.anki2"))


# --- bug 2: the clear actually empties the evidence ------------------------


class TestClearStudyEvidence:
    def test_clear_empties_attempts_and_readiness_abstains(self, tmp_path) -> None:
        from anki import speedrun_pb2 as pb

        col = _make_col(tmp_path)
        try:
            nt = col.models.by_name("Basic")
            note = col.new_note(nt)
            note["Front"] = "q"
            note["Back"] = "a"
            col.add_note(note, col.decks.id("Default"))
            cid = note.card_ids()[0]
            col._backend.record_attempt(
                pb.RecordAttemptRequest(
                    card_id=cid,
                    note_id=note.id,
                    session_id="s",
                    answered_at_ms=1_700_000_000_000,
                    took_ms=5000,
                    question_type=1,
                    correct=True,
                    data="{}",
                )
            )
            col._backend.compute_readiness()
            assert col.db.scalar("select count(*) from sr_attempts") == 1

            removed = speedrun._clear_study_evidence(col)

            # evidence is gone: attempts wiped and the count is reported back
            assert removed == 1
            assert col.db.scalar("select count(*) from sr_attempts") == 0
            # readiness is recomputed and, with no attempts, honestly abstains
            snap = col._backend.get_readiness_snapshot()
            assert snap.sufficient is False
            # only the evidence layer is cleared: the card/deck stays put
            assert col.card_count() == 1
        finally:
            col.close()

    def test_clear_resets_sample_flag(self, tmp_path) -> None:
        col = _make_col(tmp_path)
        try:
            col.set_config(speedrun.library._CFG_SAMPLE, True)
            speedrun._clear_study_evidence(col)
            assert col.get_config(speedrun.library._CFG_SAMPLE, False) is False
        finally:
            col.close()

    def test_clear_is_idempotent_on_empty_evidence(self, tmp_path) -> None:
        col = _make_col(tmp_path)
        try:
            assert speedrun._clear_study_evidence(col) == 0
            assert col.db.scalar("select count(*) from sr_attempts") == 0
            assert col._backend.get_readiness_snapshot().sufficient is False
        finally:
            col.close()


# --- bug 1: stay on the themed surface, no native deck browser -------------


class TestStaysOnThemedSurface:
    """The clear re-renders the Speedrun screen in place; it must never drive a
    native state change (``mw.reset`` / ``moveToState`` / the async native
    ``deckBrowser`` refresh) that repaints Anki's deck list over the screen."""

    def test_clear_does_not_trigger_native_redirect(self) -> None:
        src = inspect.getsource(speedrun._clear_for_sync_test)
        for native in (
            "mw.reset(",
            "moveToState",
            "deckBrowser",
            "library._refresh",
            "clear_study_data_for_sync_test",
        ):
            assert native not in src, native

    def test_clear_re_renders_the_themed_screen_in_place(self) -> None:
        src = inspect.getsource(speedrun._clear_for_sync_test)
        assert "_show_sync_pair(" in src
        assert "_render_sidebar(" in src
