// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
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

object Radius {
    val card = 24.dp
    val button = 16.dp
    val pill = 999.dp
}

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
            primary = colors.accent,
            onPrimary = colors.onSignal,
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
            primary = colors.accent,
            onPrimary = colors.onSignal,
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
    ) {
        CompositionLocalProvider(
            LocalSpeedrunColors provides colors,
            // Every Text defaults to Geist unless it opts into the Display serif.
            LocalTextStyle provides TextStyle(fontFamily = GeistSans),
            content = content,
        )
    }
}
