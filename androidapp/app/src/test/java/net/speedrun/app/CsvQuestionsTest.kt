// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The CSV question-bank importer must map flexible exports into clean items and
 *  skip anything malformed, so a bad row never becomes a broken question. */
class CsvQuestionsTest {
    @Test
    fun letterAnswerAndSubjectLabel() {
        val q = CsvQuestions.parse(
            "subject,stem,option_a,option_b,option_c,option_d,correct,explanation\n" +
                "General Chemistry,pH of pure water?,5,6,7,8,C,Neutral is 7\n",
        )
        assertEquals(1, q.size)
        assertEquals("general_chemistry", q[0].topic) // label -> canonical tag
        assertEquals(listOf("5", "6", "7", "8"), q[0].options)
        assertEquals(2, q[0].correctIndex) // "C"
        assertEquals("Neutral is 7", q[0].explanation)
    }

    @Test
    fun oneBasedNumberAndBareOptionColumns() {
        val q = CsvQuestions.parse(
            "topic,question,a,b,c,d,answer\n" +
                "biology,Powerhouse of the cell?,Nucleus,Mitochondria,Ribosome,Golgi,2\n",
        )
        assertEquals("biology", q[0].topic)
        assertEquals(1, q[0].correctIndex) // 1-based "2"
    }

    @Test
    fun answerGivenAsText() {
        val q = CsvQuestions.parse(
            "subject,stem,option_a,option_b,correct\nphysics,Unit of force?,Newton,Joule,Newton\n",
        )
        assertEquals(0, q[0].correctIndex)
    }

    @Test
    fun quotedFieldWithComma() {
        val q = CsvQuestions.parse(
            "subject,stem,option_a,option_b,correct\nbiology,\"Which, exactly?\",Yes,No,A\n",
        )
        assertEquals(1, q.size)
        assertEquals("Which, exactly?", q[0].stem)
    }

    @Test
    fun incompleteRowsAreSkipped() {
        val q = CsvQuestions.parse(
            "subject,stem,option_a,option_b,correct\n" +
                "physics,,Newton,Joule,A\n" + // no stem
                "physics,One option only,Newton,,A\n" + // < 2 options
                "physics,No answer,Newton,Joule,\n", // no correct
        )
        assertTrue(q.isEmpty())
    }

    @Test
    fun correctIndexResolution() {
        assertEquals(1, CsvQuestions.correctIndex("B", listOf("x", "y", "z")))
        assertEquals(2, CsvQuestions.correctIndex("3", listOf("x", "y", "z"))) // 1-based
        assertEquals(0, CsvQuestions.correctIndex("0", listOf("x", "y"))) // explicit 0-based
        assertNull(CsvQuestions.correctIndex("Z", listOf("x", "y"))) // out of range
        assertNull(CsvQuestions.correctIndex("", listOf("x", "y")))
    }
}
