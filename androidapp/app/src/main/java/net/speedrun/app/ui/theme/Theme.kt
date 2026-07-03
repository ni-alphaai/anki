// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp

/** Constrained spacing scale (4/8/12/16/20/24/32) - no arbitrary values. */
object Space {
    val xs = 4.dp
    val s = 8.dp
    val m = 12.dp
    val l = 16.dp
    val xl = 20.dp
    val xxl = 24.dp
    val xxxl = 32.dp
}

/** Radii from the shared spec: card 20 - control/input 12 - CTA/chips pill (999). */
object Radius {
    val card = 20.dp
    val control = 12.dp
    val pill = 999.dp
}

/**
 * Material3 shape scale driven by [Radius] so raw M3 widgets (text fields, menus,
 * sheets) pick up the design language instead of the default rounding. inputs/
 * controls -> 12, cards/containers -> 20.
 */
val SpeedrunShapes = Shapes(
    extraSmall = RoundedCornerShape(Radius.control),
    small = RoundedCornerShape(Radius.control),
    medium = RoundedCornerShape(Radius.card),
    large = RoundedCornerShape(Radius.card),
    extraLarge = RoundedCornerShape(Radius.card),
)

/** Read semantic colors by meaning: `Speedrun.colors.textSecondary`. */
object Speedrun {
    val colors: SpeedrunColors
        @Composable @ReadOnlyComposable get() = LocalSpeedrunColors.current
}

@Composable
fun SpeedrunTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val colors = if (darkTheme) DarkSpeedrunColors else LightSpeedrunColors

    val material = if (darkTheme) {
        darkColorScheme(
            primary = colors.primary,
            onPrimary = colors.onPrimary,
            background = colors.background,
            onBackground = colors.textPrimary,
            surface = colors.surface,
            onSurface = colors.textPrimary,
            surfaceVariant = colors.surfaceElevated,
            onSurfaceVariant = colors.textSecondary,
            outline = colors.separator,
            error = colors.readinessBad,
        )
    } else {
        lightColorScheme(
            primary = colors.primary,
            onPrimary = colors.onPrimary,
            background = colors.background,
            onBackground = colors.textPrimary,
            surface = colors.surface,
            onSurface = colors.textPrimary,
            surfaceVariant = colors.surfaceElevated,
            onSurfaceVariant = colors.textSecondary,
            outline = colors.separator,
            error = colors.readinessBad,
        )
    }

    MaterialTheme(
        colorScheme = material,
        typography = SpeedrunTypography,
        shapes = SpeedrunShapes,
    ) {
        CompositionLocalProvider(
            LocalSpeedrunColors provides colors,
            // Every Text defaults to Geist unless it opts into the Display serif.
            LocalTextStyle provides TextStyle(fontFamily = GeistSans),
            content = content,
        )
    }
}
