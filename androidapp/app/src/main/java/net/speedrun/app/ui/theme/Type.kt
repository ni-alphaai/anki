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

/**
 * Geist - a clean grotesque sans standing in for SF Pro (SF can't ship off
 * Apple platforms). One family, weight-driven hierarchy - the iOS way - carries
 * everything from the large title to captions.
 */
val GeistSans = FontFamily(geist(400), geist(500), geist(600), geist(700))

/**
 * iOS-style type scale (SF Pro sizing/weights), mapped onto Material3's slots so
 * every `Text` consumes a role via `MaterialTheme.typography.*` (semantic aliases
 * below) and nothing hardcodes `fontSize`. One sans family throughout; hierarchy
 * comes from weight, not a serif. Readout/large-title use tight tracking + tabular
 * lining figures so the SF-display look holds and digits don't jitter.
 *
 *   readout  44/48 700 (tnum, tight)  -> displayLarge   (the gauge score)
 *   title    34/41 700 (large title)  -> headlineLarge
 *   heading  22/28 600 (title2/3)     -> titleLarge
 *   subhead  17/22 600 (headline)     -> titleMedium
 *   bodyLg   17/25 400 (body)         -> bodyLarge
 *   body     15/20 400 (subheadline)  -> bodyMedium
 *   button   17/22 600                -> labelLarge
 *   label    13/18 500 (footnote hdr) -> labelMedium
 *   caption  13/18 400 (footnote)     -> bodySmall
 */
val SpeedrunTypography = Typography(
    displayLarge = TextStyle(
        fontFamily = GeistSans,
        fontWeight = FontWeight.Bold,
        fontSize = 44.sp,
        lineHeight = 48.sp,
        letterSpacing = (-0.02).em,
        fontFeatureSettings = "tnum, lnum",
    ),
    headlineLarge = TextStyle(
        fontFamily = GeistSans,
        fontWeight = FontWeight.Bold,
        fontSize = 34.sp,
        lineHeight = 41.sp,
        letterSpacing = (-0.015).em,
    ),
    titleLarge = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.SemiBold, fontSize = 22.sp, lineHeight = 28.sp),
    titleMedium = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, lineHeight = 22.sp),
    bodyLarge = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.Normal, fontSize = 17.sp, lineHeight = 25.sp),
    bodyMedium = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.Normal, fontSize = 15.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.Normal, fontSize = 13.sp, lineHeight = 18.sp),
    labelLarge = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, lineHeight = 22.sp),
    labelMedium = TextStyle(
        fontFamily = GeistSans,
        fontWeight = FontWeight.Medium,
        fontSize = 13.sp,
        lineHeight = 18.sp,
        letterSpacing = 0.03.em,
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
