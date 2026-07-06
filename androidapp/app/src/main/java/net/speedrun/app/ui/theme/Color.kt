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

// "Paper & print" palette on an iOS-HIG structure (grouped canvas + cells,
// three-tier text ramp, hairline separators). Light = Pampas cream paper with
// the signature Crail peach accent; Dark = charcoal paper with an accent blue.
// The data-signal palette stays distinct functional hues (memory blue,
// performance green, coverage gray, reasoning violet, passage teal) so a chart
// color never doubles as the brand. Rating buttons keep semantic feedback colors.
val LightSpeedrunColors = SpeedrunColors(
    background = Color(0xFFFAF9F5), // Pampas cream paper (grouped canvas)
    surface = Color(0xFFFFFFFF), // grouped cells
    surfaceElevated = Color(0xFFFFFFFF),
    separator = Color(0xFFE8E6DC), // light-gray hairline
    border = Color(0xFFDCD9CE), // outlined-control border
    textPrimary = Color(0xFF141413), // charcoal ink
    textSecondary = Color(0xFF6B6862),
    textTertiary = Color(0xFFA6A299),
    accent = Color(0xFFC15F3C), // Crail peach
    primary = Color(0xFFC15F3C),
    onPrimary = Color(0xFFFFFFFF),
    memory = Color(0xFF2E7BF6), // data signal (distinct from the brand accent)
    performance = Color(0xFF22C55E),
    readinessGood = Color(0xFF22C55E),
    readinessWarn = Color(0xFFE0900B), // amber
    readinessBad = Color(0xFFEF4444),
    coverageTrack = Color(0xFF8A94A6),
    reasoning = Color(0xFF7C5CFC),
    passage = Color(0xFF0E9AA7),
    again = Color(0xFFEF4444),
    hard = Color(0xFFE0900B),
    good = Color(0xFF22C55E),
    easy = Color(0xFF2E7BF6),
    onSignal = Color(0xFF141413),
    isDark = false,
)

val DarkSpeedrunColors = SpeedrunColors(
    background = Color(0xFF141413), // charcoal paper (grouped canvas, dark)
    surface = Color(0xFF1E1D1B), // grouped cells
    surfaceElevated = Color(0xFF232220),
    separator = Color(0xFF302F2C), // hairline (dark)
    border = Color(0xFF3A3833),
    textPrimary = Color(0xFFFAF9F5), // Pampas cream
    textSecondary = Color(0xFFB0AEA5), // mid gray
    textTertiary = Color(0xFF7C7970),
    accent = Color(0xFF6A9BCC), // accent blue (the dark/print pairing)
    primary = Color(0xFF6A9BCC),
    onPrimary = Color(0xFFFFFFFF),
    memory = Color(0xFF4B93FF),
    performance = Color(0xFF788C5D), // forest green (dark secondary)
    readinessGood = Color(0xFF788C5D),
    readinessWarn = Color(0xFFFBBF24),
    readinessBad = Color(0xFFFF6B6B),
    coverageTrack = Color(0xFF6B7280),
    reasoning = Color(0xFFA78BFA),
    passage = Color(0xFF2DD4BF),
    again = Color(0xFFFF6B6B),
    hard = Color(0xFFFBBF24),
    good = Color(0xFF30D158),
    easy = Color(0xFF4B93FF),
    onSignal = Color(0xFF141413),
    isDark = true,
)

val LocalSpeedrunColors = staticCompositionLocalOf { LightSpeedrunColors }
