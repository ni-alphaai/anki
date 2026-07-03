// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import net.speedrun.app.R

@OptIn(ExperimentalTextApi::class)
private fun geist(weight: Int) = Font(
    resId = R.font.geist_var,
    weight = FontWeight(weight),
    variationSettings = FontVariation.Settings(FontVariation.weight(weight)),
)

@OptIn(ExperimentalTextApi::class)
private fun fraunces(weight: Int) = Font(
    resId = R.font.fraunces_var,
    weight = FontWeight(weight),
    variationSettings = FontVariation.Settings(FontVariation.weight(weight)),
)

/** Geist - the crisp product sans: body, labels, numbers, and every control. */
val GeistSans = FontFamily(geist(400), geist(500), geist(600), geist(700))

/** Fraunces - the warm, high-contrast display serif: titles and the readout. */
val Display = FontFamily(fraunces(500), fraunces(600), fraunces(700))

/**
 * Type roles from the shared Readout spec, mapped onto Material3's slots so every
 * `Text` can consume a role via `MaterialTheme.typography.*` (see the semantic
 * aliases below) and nothing hardcodes `fontSize`. Fraunces carries the readout +
 * titles; Geist carries all UI/body. Readouts use tabular lining figures so digits
 * don't jitter.
 *
 *   readout  40/44 600 Fraunces (tnum) -> displayLarge
 *   title    30/34 600 Fraunces         -> headlineLarge
 *   heading  20/26 600 Geist            -> titleLarge
 *   subhead  17/24 600 Geist            -> titleMedium
 *   bodyLg   17/26 400 Geist            -> bodyLarge
 *   body     15/22 400 Geist            -> bodyMedium
 *   label    13/16 500 Geist (UPPER)    -> labelMedium
 *   caption  12/16 400 Geist            -> bodySmall
 */
val SpeedrunTypography = Typography(
    displayLarge = TextStyle(
        fontFamily = Display,
        fontWeight = FontWeight.SemiBold,
        fontSize = 40.sp,
        lineHeight = 44.sp,
        fontFeatureSettings = "tnum, lnum",
    ),
    headlineLarge = TextStyle(fontFamily = Display, fontWeight = FontWeight.SemiBold, fontSize = 30.sp, lineHeight = 34.sp),
    titleLarge = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.SemiBold, fontSize = 20.sp, lineHeight = 26.sp),
    titleMedium = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, lineHeight = 24.sp),
    bodyLarge = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.Normal, fontSize = 17.sp, lineHeight = 26.sp),
    bodyMedium = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.Normal, fontSize = 15.sp, lineHeight = 22.sp),
    bodySmall = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.Normal, fontSize = 12.sp, lineHeight = 16.sp),
    labelLarge = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, lineHeight = 20.sp),
    labelMedium = TextStyle(
        fontFamily = GeistSans,
        fontWeight = FontWeight.Medium,
        fontSize = 13.sp,
        lineHeight = 16.sp,
        letterSpacing = 0.04.em,
    ),
    labelSmall = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.Medium, fontSize = 12.sp, lineHeight = 16.sp),
)

// Semantic aliases so components read type by meaning (spec role names) rather
// than Material slot names: `MaterialTheme.typography.title`, `...readout`, etc.
val Typography.readout: TextStyle get() = displayLarge
val Typography.title: TextStyle get() = headlineLarge
val Typography.heading: TextStyle get() = titleLarge
val Typography.subhead: TextStyle get() = titleMedium
val Typography.bodyLg: TextStyle get() = bodyLarge
val Typography.body: TextStyle get() = bodyMedium
val Typography.label: TextStyle get() = labelMedium
val Typography.caption: TextStyle get() = bodySmall

/** Geist tabular figures for non-hero counters/stats (so digits stay aligned). */
val Typography.stat: TextStyle get() = titleMedium.copy(fontFeatureSettings = "tnum, lnum")
