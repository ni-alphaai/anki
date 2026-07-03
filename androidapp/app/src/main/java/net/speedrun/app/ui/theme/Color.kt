// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * Semantic design tokens (Apple-clean, grayscale-first) that Material3's
 * ColorScheme doesn't cover: layered surfaces, three-tier text, hairline
 * separators, and the Speedrun signal palette. Provided via a CompositionLocal
 * so screens read colors by meaning, never as hardcoded hex.
 */
@Immutable
data class SpeedrunColors(
    val background: Color,
    val surface: Color,
    val surfaceElevated: Color,
    val separator: Color,
    // A slightly stronger hairline for outlined controls (secondary button, nav)
    // so they read as tappable against the canvas without a heavy border.
    val border: Color,
    val textPrimary: Color,
    val textSecondary: Color,
    val textTertiary: Color,
    val accent: Color,
    // Filled primary action (the accent, not near-black ink) + its content color.
    val primary: Color,
    val onPrimary: Color,
    // Three signals
    val memory: Color,
    val performance: Color,
    val readinessGood: Color,
    val readinessWarn: Color,
    val readinessBad: Color,
    // The third (neutral) signal track on the readiness gauge; never green/blue.
    val coverageTrack: Color,
    // Kind-aware diagnosis accents (spec: reasoning=violet, passage=teal;
    // memory reuses `memory` blue, test-taking reuses `readinessWarn` amber).
    val reasoning: Color,
    val passage: Color,
    // Rating buttons (Again / Hard / Good / Easy)
    val again: Color,
    val hard: Color,
    val good: Color,
    val easy: Color,
    // Content color for text/icons sitting on a filled `accent` (primary) fill.
    val onSignal: Color,
    val isDark: Boolean,
)

/**
 * The correct on-color for text/icons sitting on a *filled data color*
 * (per spec `onSignal`): dark ink on amber/yellow and green, white on blue/red.
 * Fixes the white-on-amber contrast bug - never hardcode `Color.White` on a
 * rating fill. Matched semantically (amber == warn == hard, green == perf ==
 * good) so it holds in both light and dark.
 */
fun SpeedrunColors.onColor(fill: Color): Color =
    if (fill == hard || fill == good || fill == readinessWarn || fill == readinessGood) {
        Color(0xFF1C1B19)
    } else {
        Color(0xFFFFFFFF)
    }

// Calm "warm instrument" system: a soft warm-neutral canvas (not a clinical cool
// gray), white cards lifted by gentle shadow, and a warm near-black "ink" for
// text only - the harsh black is retired from fills. The confident blue accent is
// the one interactive throughline (primary CTA, links, active nav). Blue (memory)
// and green (performance) are the data accents; the readiness ring pairs them as
// two arcs - the memory-to-performance bridge.
val LightSpeedrunColors = SpeedrunColors(
    background = Color(0xFFF0EFEB),
    surface = Color(0xFFFFFFFF),
    surfaceElevated = Color(0xFFFFFFFF),
    separator = Color(0xFFE6E4DD),
    border = Color(0xFFDBD9D1),
    textPrimary = Color(0xFF1C1B19),
    textSecondary = Color(0xFF6E6C68),
    textTertiary = Color(0xFFA6A49E),
    accent = Color(0xFF2E7BF6),
    primary = Color(0xFF2E7BF6),
    onPrimary = Color(0xFFFFFFFF),
    memory = Color(0xFF2E7BF6),
    performance = Color(0xFF22C55E),
    readinessGood = Color(0xFF22C55E),
    readinessWarn = Color(0xFFE0900B),
    readinessBad = Color(0xFFEF4444),
    coverageTrack = Color(0xFF8A94A6),
    reasoning = Color(0xFF7C5CFC),
    passage = Color(0xFF0E9AA7),
    again = Color(0xFFEF4444),
    hard = Color(0xFFE0900B),
    good = Color(0xFF22C55E),
    easy = Color(0xFF2E7BF6),
    onSignal = Color(0xFFFFFFFF),
    isDark = false,
)

val DarkSpeedrunColors = SpeedrunColors(
    background = Color(0xFF0C0D0F),
    surface = Color(0xFF17181B),
    surfaceElevated = Color(0xFF1E2024),
    separator = Color(0xFF2A2D31),
    border = Color(0xFF3A3D42),
    textPrimary = Color(0xFFF2F3F5),
    textSecondary = Color(0xFF9AA0A8),
    textTertiary = Color(0xFF6B7078),
    accent = Color(0xFF4B93FF),
    primary = Color(0xFF4B93FF),
    onPrimary = Color(0xFF0C0D0F),
    memory = Color(0xFF4B93FF),
    performance = Color(0xFF30D158),
    readinessGood = Color(0xFF30D158),
    readinessWarn = Color(0xFFFBBF24),
    readinessBad = Color(0xFFFF6B6B),
    coverageTrack = Color(0xFF6B7280),
    reasoning = Color(0xFFA78BFA),
    passage = Color(0xFF2DD4BF),
    again = Color(0xFFFF6B6B),
    hard = Color(0xFFFBBF24),
    good = Color(0xFF30D158),
    easy = Color(0xFF4B93FF),
    onSignal = Color(0xFFFFFFFF),
    isDark = true,
)

val LocalSpeedrunColors = staticCompositionLocalOf { LightSpeedrunColors }
