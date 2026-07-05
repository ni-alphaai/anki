// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

/**
 * D7 delayed-feedback experiment gate (client reveal only).
 *
 * Mirrors the desktop `aqt.speedrun._should_withhold_feedback` and the Rust
 * `speedrun::feedback::should_withhold_correctness`: withhold the *immediate
 * correctness reveal* only when the experiment is explicitly enabled AND the
 * student is already proficient. The attempt is still recorded either way - this
 * only decides what the UI shows now vs. defers to the delayed surface.
 *
 * Skill-gated feedback *timing* is NOT evidence-established; this ships off by
 * default as a labeled experiment.
 */
object Feedback {
    /** At/above this exam-style accuracy a student counts as proficient. */
    const val PROFICIENT_THRESHOLD = 0.8f

    fun shouldWithhold(performance: Float, enabled: Boolean): Boolean =
        enabled && performance >= PROFICIENT_THRESHOLD
}
