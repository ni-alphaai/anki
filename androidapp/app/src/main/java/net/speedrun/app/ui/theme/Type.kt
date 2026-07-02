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

/** Fraunces - a warm, high-contrast display serif reserved for the big titles. */
val Display = FontFamily(fraunces(500), fraunces(600), fraunces(700))

val SpeedrunTypography = Typography(
    headlineLarge = TextStyle(fontFamily = Display, fontWeight = FontWeight.SemiBold, fontSize = 34.sp, lineHeight = 40.sp),
    titleLarge = TextStyle(fontFamily = Display, fontWeight = FontWeight.SemiBold, fontSize = 24.sp, lineHeight = 30.sp),
    titleMedium = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, lineHeight = 22.sp),
    bodyLarge = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.Normal, fontSize = 17.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.Normal, fontSize = 15.sp, lineHeight = 20.sp),
    labelLarge = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, lineHeight = 20.sp),
    labelMedium = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.Medium, fontSize = 13.sp, lineHeight = 18.sp),
    labelSmall = TextStyle(fontFamily = GeistSans, fontWeight = FontWeight.Medium, fontSize = 12.sp, lineHeight = 16.sp),
)
