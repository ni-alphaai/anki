# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Tests for the deterministic MCAT content-category classifier (the offline
core of the hybrid grouping; the AI residual pass is exercised separately)."""

from __future__ import annotations

from aqt import speedrun_grouping as grouping


def _section_of(cid: str | None) -> str:
    return grouping.library.topic_meta().get(cid or "", {}).get("section", "")


class TestClassifier:
    def test_biochem_text_lands_in_bio_biochem(self) -> None:
        cid, score = grouping.classify_text(
            "Amino acids form peptide bonds; the enzyme's active site and the "
            "protein's tertiary structure determine catalysis."
        )
        assert cid is not None
        assert score > 0
        assert _section_of(cid) == "Bio/Biochem"

    def test_physics_text_lands_in_chem_phys(self) -> None:
        cid, _score = grouping.classify_text(
            "A block accelerates under a net force; its kinetic energy and "
            "momentum change as velocity increases down the incline."
        )
        assert cid is not None
        assert _section_of(cid) == "Chem/Phys"

    def test_psych_text_lands_in_psych_soc(self) -> None:
        cid, _score = grouping.classify_text(
            "Operant conditioning shapes behavior through reinforcement; "
            "classical conditioning pairs a neutral stimulus with a response."
        )
        assert cid is not None
        assert _section_of(cid) == "Psych/Soc"

    def test_empty_text_is_unclassified(self) -> None:
        cid, score = grouping.classify_text("")
        assert cid is None
        assert score == 0.0

    def test_generic_text_scores_low(self) -> None:
        # No topical vocabulary -> should not clear the confidence threshold.
        _cid, score = grouping.classify_text("the this that with and for")
        assert score < grouping._MIN_SCORE
