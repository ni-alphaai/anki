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
    private const val KEY_SYNC_URL = "sync_url"
    private const val KEY_SYNC_USER = "sync_username"
    private const val KEY_LAST_SYNCED = "last_synced_ms"

    var themeMode by mutableStateOf(ThemeMode.System)
        private set

    /** Auto-launch the end-of-session reasoning round instead of offering it. */
    var autoReasoningRound by mutableStateOf(false)
        private set

    /** Whether the bundled example deck has been auto-loaded once (first run). */
    var exampleLoaded by mutableStateOf(false)
        private set

    /** Self-hosted sync server URL (e.g. http://192.168.1.20:8080/). */
    var syncUrl by mutableStateOf("")
        private set

    /** Sync username (the password is entered at sync time, never stored). */
    var syncUsername by mutableStateOf("")
        private set

    /** Epoch millis of the last successful sync (0 = never). */
    var lastSyncedMs by mutableStateOf(0L)
        private set

    fun load(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        themeMode = runCatching {
            ThemeMode.valueOf(prefs.getString(KEY_THEME, ThemeMode.System.name)!!)
        }.getOrDefault(ThemeMode.System)
        autoReasoningRound = prefs.getBoolean(KEY_AUTO_ROUND, false)
        exampleLoaded = prefs.getBoolean(KEY_EXAMPLE_LOADED, false)
        syncUrl = prefs.getString(KEY_SYNC_URL, "") ?: ""
        syncUsername = prefs.getString(KEY_SYNC_USER, "") ?: ""
        lastSyncedMs = prefs.getLong(KEY_LAST_SYNCED, 0L)
    }

    /** Record the time of a successful sync (drives the "last synced" affordance). */
    fun setLastSynced(context: Context, ms: Long) {
        lastSyncedMs = ms
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putLong(KEY_LAST_SYNCED, ms).apply()
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

    /** Persist the sync server URL + username (password is never stored). */
    fun setSyncSettings(context: Context, url: String, username: String) {
        syncUrl = url
        syncUsername = username
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_SYNC_URL, url)
            .putString(KEY_SYNC_USER, username)
            .apply()
    }
}
