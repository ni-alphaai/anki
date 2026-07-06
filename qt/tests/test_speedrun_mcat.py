# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for the MCAT section/subject taxonomy (pure, no Qt/backend)."""

from __future__ import annotations

from aqt import speedrun_mcat as mcat


class TestSections:
    def test_four_scored_sections(self) -> None:
        keys = [s["key"] for s in mcat.SECTIONS]
        assert keys == ["chem_phys", "cars", "bio_biochem", "psych_soc"]

    def test_section_by_key(self) -> None:
        assert mcat.section_by_key("bio_biochem")["short"] == "Bio/Biochem"
        assert mcat.section_by_key("nope") is None

    def test_cars_is_a_reasoning_section_with_a_bank(self) -> None:
        # CARS now carries a passage-question bank (subject "cars") and is flagged
        # reasoning, so memory/coverage render as N/A while performance is real.
        assert mcat.section_by_key("cars")["subjects"] == ["cars"]
        assert mcat.is_reasoning_section("cars") is True


class TestSubjects:
    def test_known_subject_labels(self) -> None:
        assert mcat.subject_label("general_chemistry") == "General Chemistry"
        assert mcat.subject_label("psychology_sociology") == "Psychology / Sociology"

    def test_unknown_subject_falls_back_to_titlecase(self) -> None:
        assert mcat.subject_label("organic_chemistry") == "Organic Chemistry"

    def test_every_bank_subject_maps_to_a_section(self) -> None:
        # The subject tags used by the bundled pack + deck heuristics.
        for subject in (
            "biology",
            "biochemistry",
            "general_chemistry",
            "physics",
            "psychology_sociology",
        ):
            assert mcat.section_key_for_subject(subject) is not None

    def test_unmapped_subject_has_no_section(self) -> None:
        assert mcat.section_key_for_subject("basket_weaving") is None
