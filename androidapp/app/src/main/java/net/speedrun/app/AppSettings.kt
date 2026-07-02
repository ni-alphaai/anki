// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

enum class ThemeMode { System, Light, Dark }

/** Tiny app-local preference store (appearance only, for now). */
object AppSettings {
    private const val PREFS = "speedrun_prefs"
    private const val KEY_THEME = "theme_mode"

    var themeMode by mutableStateOf(ThemeMode.System)
        private set

    fun load(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        themeMode = runCatching {
            ThemeMode.valueOf(prefs.getString(KEY_THEME, ThemeMode.System.name)!!)
        }.getOrDefault(ThemeMode.System)
    }

    fun setThemeMode(context: Context, mode: ThemeMode) {
        themeMode = mode
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_THEME, mode.name).apply()
    }
}
