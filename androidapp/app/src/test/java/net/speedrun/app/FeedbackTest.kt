// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * D7 withhold gate parity with the desktop (`_should_withhold_feedback`) and the
 * engine (`should_withhold_correctness`): withhold only when the experiment is
 * ON and the student is proficient (>= 0.8).
 */
class FeedbackTest {
    @Test
    fun disabledNeverWithholds() {
        assertFalse(Feedback.shouldWithhold(1.0f, enabled = false))
        assertFalse(Feedback.shouldWithhold(0.0f, enabled = false))
    }

    @Test
    fun enabledWithholdsOnlyForProficient() {
        assertTrue(Feedback.shouldWithhold(0.8f, enabled = true))
        assertTrue(Feedback.shouldWithhold(0.95f, enabled = true))
        assertFalse(Feedback.shouldWithhold(0.79f, enabled = true))
        assertFalse(Feedback.shouldWithhold(0.0f, enabled = true))
    }
}
