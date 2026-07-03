// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import net.speedrun.app.ui.pct
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Pure-JVM unit tests for the UI-facing domain models ([McatScale], [Readiness],
 * [Diagnosis]) and the [pct] formatter. No device/Compose needed - these run on
 * the host JVM via `:app:testDebugUnitTest`.
 */
class EngineModelsTest {
    // --- McatScale.fraction (472..528 -> 0..1, clamped) ---

    @Test
    fun mcatScaleFractionAtLowerBoundIsZero() {
        assertEquals(0f, McatScale.fraction(McatScale.MIN), 0f)
    }

    @Test
    fun mcatScaleFractionAtUpperBoundIsOne() {
        assertEquals(1f, McatScale.fraction(McatScale.MAX), 0f)
    }

    @Test
    fun mcatScaleFractionAtMidpointIsHalf() {
        // 500 is 28 points above 472, half of the 56-point range.
        assertEquals(0.5f, McatScale.fraction(500), 1e-6f)
    }

    @Test
    fun mcatScaleFractionClampsBelowMin() {
        assertEquals(0f, McatScale.fraction(400), 0f)
    }

    @Test
    fun mcatScaleFractionClampsAboveMax() {
        assertEquals(1f, McatScale.fraction(600), 0f)
    }

    // --- Readiness.weakestLabel (blockingDimension -> human label) ---

    @Test
    fun weakestLabelForMemory() {
        assertEquals("Memory", readiness(blockingDimension = "memory").weakestLabel)
    }

    @Test
    fun weakestLabelForPerformance() {
        assertEquals("Performance", readiness(blockingDimension = "performance").weakestLabel)
    }

    @Test
    fun weakestLabelForCoverage() {
        assertEquals("Coverage", readiness(blockingDimension = "coverage").weakestLabel)
    }

    @Test
    fun weakestLabelIsCaseInsensitiveForKnownDimensions() {
        assertEquals("Memory", readiness(blockingDimension = "MEMORY").weakestLabel)
    }

    @Test
    fun weakestLabelCapitalizesUnknownDimension() {
        assertEquals("Attempts", readiness(blockingDimension = "attempts").weakestLabel)
    }

    // --- Diagnosis.label / action (kind + routedAction -> text) ---

    @Test
    fun diagnosisLabelsPerKind() {
        assertEquals("Memory gap", Diagnosis(kind = 1, routedAction = 0).label)
        assertEquals("Reasoning gap", Diagnosis(kind = 2, routedAction = 0).label)
        assertEquals("Passage-comprehension gap", Diagnosis(kind = 3, routedAction = 0).label)
        assertEquals("Test-taking gap", Diagnosis(kind = 4, routedAction = 0).label)
    }

    @Test
    fun diagnosisLabelIsNullForNoneOrCorrect() {
        assertNull(Diagnosis(kind = 0, routedAction = 0).label)
        assertNull(Diagnosis(kind = 5, routedAction = 0).label)
    }

    @Test
    fun diagnosisActionsPerRoutedAction() {
        assertEquals(
            "It'll resurface sooner via spaced repetition.",
            Diagnosis(kind = 1, routedAction = 1).action,
        )
        assertEquals(
            "Next: concept-linked passage practice.",
            Diagnosis(kind = 2, routedAction = 2).action,
        )
        assertEquals(
            "Next: review your test-taking strategy.",
            Diagnosis(kind = 4, routedAction = 3).action,
        )
    }

    @Test
    fun diagnosisActionIsEmptyForNoRoute() {
        assertEquals("", Diagnosis(kind = 1, routedAction = 0).action)
    }

    // --- pct (0..1 -> "N%", clamped, truncated) ---

    @Test
    fun pctAtZero() {
        assertEquals("0%", pct(0f))
    }

    @Test
    fun pctAtOne() {
        assertEquals("100%", pct(1f))
    }

    @Test
    fun pctAtFraction() {
        assertEquals("50%", pct(0.5f))
    }

    @Test
    fun pctTruncatesTowardsZero() {
        assertEquals("99%", pct(0.999f))
    }

    @Test
    fun pctClampsOutOfRange() {
        assertEquals("100%", pct(2f))
        assertEquals("0%", pct(-1f))
    }

    private fun readiness(blockingDimension: String): Readiness =
        Readiness(
            memory = 0f,
            performance = 0f,
            coverage = 0f,
            recallPerfGap = 0f,
            readinessScaled = 0,
            low = 0,
            high = 0,
            sufficient = false,
            memorySufficient = false,
            performanceSufficient = false,
            blockingDimension = blockingDimension,
            reason = "",
        )
}
