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
    val textPrimary: Color,
    val textSecondary: Color,
    val textTertiary: Color,
    val accent: Color,
    // Three signals
    val memory: Color,
    val performance: Color,
    val readinessGood: Color,
    val readinessWarn: Color,
    val readinessBad: Color,
    // Rating buttons (Again / Hard / Good / Easy)
    val again: Color,
    val hard: Color,
    val good: Color,
    val easy: Color,
    // The review card is always a light "paper" surface for legibility
    val cardPaper: Color,
    val onSignal: Color,
    val isDark: Boolean,
)

// Clean, high-contrast system: a cool off-white canvas, white cards, and a
// near-black "ink" that doubles as the primary button/nav fill (it inverts in
// dark mode). Blue (memory) and green (performance) are the data accents; the
// readiness ring is a blue->green gradient - the memory-to-performance bridge.
val LightSpeedrunColors = SpeedrunColors(
    background = Color(0xFFF2F3F5),
    surface = Color(0xFFFFFFFF),
    surfaceElevated = Color(0xFFFFFFFF),
    separator = Color(0xFFE7EAEE),
    textPrimary = Color(0xFF16181D),
    textSecondary = Color(0xFF6B7280),
    textTertiary = Color(0xFFA2A8B0),
    accent = Color(0xFF2E7BF6),
    memory = Color(0xFF2E7BF6),
    performance = Color(0xFF22C55E),
    readinessGood = Color(0xFF22C55E),
    readinessWarn = Color(0xFFE0900B),
    readinessBad = Color(0xFFEF4444),
    again = Color(0xFFEF4444),
    hard = Color(0xFFE0900B),
    good = Color(0xFF22C55E),
    easy = Color(0xFF2E7BF6),
    cardPaper = Color(0xFFFFFFFF),
    onSignal = Color(0xFFFFFFFF),
    isDark = false,
)

val DarkSpeedrunColors = SpeedrunColors(
    background = Color(0xFF0C0D0F),
    surface = Color(0xFF17181B),
    surfaceElevated = Color(0xFF212327),
    separator = Color(0xFF2A2D31),
    textPrimary = Color(0xFFF2F3F5),
    textSecondary = Color(0xFF9AA0A8),
    textTertiary = Color(0xFF6B7078),
    accent = Color(0xFF4B93FF),
    memory = Color(0xFF4B93FF),
    performance = Color(0xFF30D158),
    readinessGood = Color(0xFF30D158),
    readinessWarn = Color(0xFFFBBF24),
    readinessBad = Color(0xFFFF6B6B),
    again = Color(0xFFFF6B6B),
    hard = Color(0xFFFBBF24),
    good = Color(0xFF30D158),
    easy = Color(0xFF4B93FF),
    cardPaper = Color(0xFF17181B),
    onSignal = Color(0xFFFFFFFF),
    isDark = true,
)

val LocalSpeedrunColors = staticCompositionLocalOf { LightSpeedrunColors }
