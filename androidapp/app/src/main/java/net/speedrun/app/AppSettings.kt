// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

enum class ThemeMode { System, Light, Dark }

/** Tiny app-local preference store. */
object AppSettings {
    private const val PREFS = "speedrun_prefs"
    private const val KEY_THEME = "theme_mode"
    private const val KEY_AUTO_ROUND = "auto_reasoning_round"
    private const val KEY_EXAMPLE_LOADED = "example_deck_loaded"

    var themeMode by mutableStateOf(ThemeMode.System)
        private set

    /** Auto-launch the end-of-session reasoning round instead of offering it. */
    var autoReasoningRound by mutableStateOf(false)
        private set

    /** Whether the bundled example deck has been auto-loaded once (first run). */
    var exampleLoaded by mutableStateOf(false)
        private set

    fun load(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        themeMode = runCatching {
            ThemeMode.valueOf(prefs.getString(KEY_THEME, ThemeMode.System.name)!!)
        }.getOrDefault(ThemeMode.System)
        autoReasoningRound = prefs.getBoolean(KEY_AUTO_ROUND, false)
        exampleLoaded = prefs.getBoolean(KEY_EXAMPLE_LOADED, false)
    }

    fun setExampleLoaded(context: Context, on: Boolean) {
        exampleLoaded = on
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putBoolean(KEY_EXAMPLE_LOADED, on).apply()
    }

    fun setThemeMode(context: Context, mode: ThemeMode) {
        themeMode = mode
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_THEME, mode.name).apply()
    }

    fun setAutoReasoningRound(context: Context, on: Boolean) {
        autoReasoningRound = on
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putBoolean(KEY_AUTO_ROUND, on).apply()
    }
}
