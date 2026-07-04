# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Unit tests for the CSV question-bank importer (pure parsing, no backend)."""

from __future__ import annotations

import os
import tempfile

from aqt import speedrun_library as lib


def _write_csv(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


class TestCsvToPack:
    def test_letter_answer_and_subject_label(self) -> None:
        path = _write_csv(
            "subject,stem,option_a,option_b,option_c,option_d,correct,explanation\n"
            "General Chemistry,pH of pure water?,5,6,7,8,C,Neutral is 7\n"
        )
        try:
            pack = lib._csv_to_pack(path)
        finally:
            os.unlink(path)
        assert len(pack["questions"]) == 1
        q = pack["questions"][0]
        assert q["topic"] == "general_chemistry"  # label -> canonical tag
        assert q["options"] == ["5", "6", "7", "8"]
        assert q["correct_index"] == 2  # "C"
        assert q["explanation"] == "Neutral is 7"
        assert q["provenance"] == 0

    def test_one_based_number_and_bare_option_columns(self) -> None:
        path = _write_csv(
            "topic,question,a,b,c,d,answer\n"
            "biology,Powerhouse of the cell?,Nucleus,Mitochondria,Ribosome,Golgi,2\n"
        )
        try:
            pack = lib._csv_to_pack(path)
        finally:
            os.unlink(path)
        q = pack["questions"][0]
        assert q["topic"] == "biology"
        assert q["correct_index"] == 1  # 1-based "2"

    def test_answer_given_as_text(self) -> None:
        path = _write_csv(
            "subject,stem,option_a,option_b,correct\n"
            "physics,Unit of force?,Newton,Joule,Newton\n"
        )
        try:
            pack = lib._csv_to_pack(path)
        finally:
            os.unlink(path)
        assert pack["questions"][0]["correct_index"] == 0

    def test_incomplete_rows_are_skipped(self) -> None:
        path = _write_csv(
            "subject,stem,option_a,option_b,correct\n"
            "physics,,Newton,Joule,A\n"  # no stem
            "physics,One option only,Newton,,A\n"  # < 2 options
            "physics,No answer,Newton,Joule,\n"  # no correct
        )
        try:
            pack = lib._csv_to_pack(path)
        finally:
            os.unlink(path)
        assert pack["questions"] == []


class TestCsvCorrectIndex:
    def test_letter(self) -> None:
        assert lib._csv_correct_index("B", ["x", "y", "z"]) == 1

    def test_one_based_number(self) -> None:
        assert lib._csv_correct_index("3", ["x", "y", "z"]) == 2

    def test_explicit_zero_based(self) -> None:
        assert lib._csv_correct_index("0", ["x", "y"]) == 0

    def test_out_of_range_letter(self) -> None:
        assert lib._csv_correct_index("Z", ["x", "y"]) is None

    def test_empty(self) -> None:
        assert lib._csv_correct_index("", ["x", "y"]) is None
