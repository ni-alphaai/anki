// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import net.speedrun.app.ui.ReadinessVerdict
import net.speedrun.app.ui.theme.SpeedrunTheme
import org.junit.Rule
import org.junit.Test

/**
 * Instrumented Compose UI tests for [ReadinessVerdict], rendered in isolation via
 * [createComposeRule]. The component is fed a hand-built [Readiness] data class,
 * so there is no engine, collection, or native `.so` dependency -- MainActivity
 * is deliberately never launched.
 *
 * Runs on the connected device via `:app:connectedDebugAndroidTest` (controller).
 */
class ReadinessVerdictUiTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun abstainStateShowsHonestChecklistAndWeakestDimension() {
        composeRule.setContent {
            SpeedrunTheme {
                ReadinessVerdict(readiness = ABSTAIN)
            }
        }

        // The engine's run-on reason is rendered as a tidy checklist.
        composeRule.onNodeWithText("graded attempts 0/30").assertIsDisplayed()
        composeRule.onNodeWithText("exam-style attempts 0/20").assertIsDisplayed()
        // The honest empty readout + the named weakest dimension.
        composeRule.onNodeWithText("no score yet").assertIsDisplayed()
        composeRule.onNodeWithText("Weakest dimension: Memory").assertIsDisplayed()
    }

    @Test
    fun sufficientStateShowsProjectedScore() {
        composeRule.setContent {
            SpeedrunTheme {
                ReadinessVerdict(readiness = SUFFICIENT)
            }
        }

        composeRule.onNodeWithText("508").assertIsDisplayed()
        composeRule.onNodeWithText("projected MCAT").assertIsDisplayed()
    }

    private companion object {
        val ABSTAIN =
            Readiness(
                memory = 0.2f,
                performance = 0f,
                coverage = 0.1f,
                recallPerfGap = 0f,
                readinessScaled = 0,
                low = 0,
                high = 0,
                sufficient = false,
                memorySufficient = false,
                performanceSufficient = false,
                blockingDimension = "memory",
                reason = "not enough evidence: need graded attempts 0/30, exam-style attempts 0/20",
            )

        val SUFFICIENT =
            Readiness(
                memory = 0.8f,
                performance = 0.7f,
                coverage = 0.9f,
                recallPerfGap = 0.1f,
                readinessScaled = 508,
                low = 500,
                high = 516,
                sufficient = true,
                memorySufficient = true,
                performanceSufficient = true,
                blockingDimension = "none",
                reason = "",
            )
    }
}
