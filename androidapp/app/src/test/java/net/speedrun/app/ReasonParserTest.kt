// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import net.speedrun.app.ui.parseNeedItems
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-JVM unit tests for [parseNeedItems], the reason-checklist parser extracted
 * from `ReadinessVerdict`. Runs on the host JVM via `:app:testDebugUnitTest`.
 */
class ReasonParserTest {
    @Test
    fun parsesTheEngineNeedList() {
        val reason =
            "not enough evidence: need graded attempts 0/30, " +
                "exam-style attempts 0/20, topic coverage 12/50, calibration 0/40"
        assertEquals(
            listOf(
                "graded attempts 0/30",
                "exam-style attempts 0/20",
                "topic coverage 12/50",
                "calibration 0/40",
            ),
            parseNeedItems(reason),
        )
    }

    @Test
    fun parsesASingleNeedItem() {
        assertEquals(
            listOf("graded attempts 0/30"),
            parseNeedItems("not enough evidence: need graded attempts 0/30"),
        )
    }

    @Test
    fun blankReasonYieldsNoItems() {
        assertTrue(parseNeedItems("").isEmpty())
    }

    @Test
    fun reasonWithoutNeedClauseYieldsNoItems() {
        assertTrue(parseNeedItems("Keep reviewing to unlock your score.").isEmpty())
    }

    @Test
    fun trimsWhitespaceAndDropsEmptySegments() {
        // Trailing ", " would otherwise leave a blank segment; it is filtered out.
        assertEquals(
            listOf("graded attempts 0/30", "exam-style attempts 0/20"),
            parseNeedItems("x: need graded attempts 0/30,  exam-style attempts 0/20, "),
        )
    }
}
