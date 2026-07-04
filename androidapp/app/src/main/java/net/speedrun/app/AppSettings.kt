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
    private const val KEY_DIAGNOSTIC_DONE = "diagnostic_done"
    private const val KEY_SYNC_URL = "sync_url"
    private const val KEY_SYNC_LAN_URL = "sync_lan_url"
    private const val KEY_SYNC_USB_URL = "sync_usb_url"
    private const val KEY_SYNC_USER = "sync_username"
    private const val KEY_SYNC_TOKEN = "sync_token"
    private const val KEY_SYNC_USB = "sync_via_usb"
    private const val KEY_LAST_SYNCED = "last_synced_ms"

    var themeMode by mutableStateOf(ThemeMode.System)
        private set

    /** Auto-launch the end-of-session reasoning round instead of offering it. */
    var autoReasoningRound by mutableStateOf(false)
        private set

    /** Whether the bundled example deck has been auto-loaded once (first run). */
    var exampleLoaded by mutableStateOf(false)
        private set

    /** Whether the onboarding placement diagnostic has been offered (first run). */
    var diagnosticDone by mutableStateOf(false)
        private set

    /** Self-hosted sync server URL (legacy / display fallback). */
    var syncUrl by mutableStateOf("")
        private set

    /** LAN URL from the desktop QR (Wi-Fi fallback). */
    var syncLanUrl by mutableStateOf("")
        private set

    /** USB loopback URL from the desktop QR (``127.0.0.1`` via adb reverse). */
    var syncUsbUrl by mutableStateOf("")
        private set

    /** Sync username (from QR pairing, or typed for manual sign-in). */
    var syncUsername by mutableStateOf("")
        private set

    /**
     * Sync token from QR pairing (the server "password"). Stored so a paired
     * device syncs with one tap; blank when only a manual URL/user is set.
     */
    var syncToken by mutableStateOf("")
        private set

    /** True once the device has scanned a pairing QR (url + token present). */
    val isPaired: Boolean
        get() = syncToken.isNotBlank() && resolveEffectiveUrl().isNotBlank()

    /** Pick the USB or LAN URL at sync time (not only when the QR was scanned). */
    fun resolveEffectiveUrl(): String {
        if (syncViaUsb && syncUsbUrl.isNotBlank()) return syncUsbUrl
        if (syncLanUrl.isNotBlank()) return syncLanUrl
        return syncUrl
    }

    /** Pick the USB loopback URL saved from the desktop QR. */
    fun resolveUsbUrl(): String {
        if (syncUsbUrl.isNotBlank()) return syncUsbUrl
        return syncUrl.takeIf { it.contains("127.0.0.1") }.orEmpty()
    }

    /** Pick the LAN URL saved from the desktop QR or mDNS discovery. */
    fun resolveLanUrl(): String {
        if (syncLanUrl.isNotBlank()) return syncLanUrl
        return syncUrl.takeIf { !it.contains("127.0.0.1") }.orEmpty()
    }

    /** Prefer the USB loopback URL (``127.0.0.1`` via adb reverse) over guest Wi-Fi LAN. */
    var syncViaUsb by mutableStateOf(true)
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
        diagnosticDone = prefs.getBoolean(KEY_DIAGNOSTIC_DONE, false)
        syncUrl = prefs.getString(KEY_SYNC_URL, "") ?: ""
        syncLanUrl = prefs.getString(KEY_SYNC_LAN_URL, "") ?: ""
        syncUsbUrl = prefs.getString(KEY_SYNC_USB_URL, "") ?: ""
        // Migrate older builds that only stored a single URL.
        if (syncLanUrl.isBlank() && syncUsbUrl.isBlank() && syncUrl.isNotBlank()) {
            if (syncUrl.contains("127.0.0.1")) {
                syncUsbUrl = syncUrl
            } else {
                syncLanUrl = syncUrl
            }
        }
        scrubInvalidSyncUrls()
        syncUsername = prefs.getString(KEY_SYNC_USER, "") ?: ""
        syncToken = prefs.getString(KEY_SYNC_TOKEN, "") ?: ""
        syncViaUsb = prefs.getBoolean(KEY_SYNC_USB, true)
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

    fun setDiagnosticDone(context: Context, on: Boolean) {
        diagnosticDone = on
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putBoolean(KEY_DIAGNOSTIC_DONE, on).apply()
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

    fun setSyncViaUsb(context: Context, on: Boolean) {
        syncViaUsb = on
        syncUrl = resolveEffectiveUrl()
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_SYNC_USB, on)
            .putString(KEY_SYNC_URL, syncUrl)
            .apply()
    }

    /** Persist the sync server URL + username (password is never stored). */
    fun setSyncSettings(context: Context, url: String, username: String) {
        val normalized = SyncUrl.normalize(url)
        syncLanUrl = normalized
        syncUrl = resolveEffectiveUrl()
        syncUsername = username
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_SYNC_LAN_URL, syncLanUrl)
            .putString(KEY_SYNC_URL, syncUrl)
            .putString(KEY_SYNC_USER, username)
            .apply()
    }

    /** Update the LAN URL after mDNS discovery (keeps the USB URL intact). */
    fun updateLanUrl(context: Context, url: String) {
        val normalized = SyncUrl.normalize(url)
        if (!SyncUrl.isValid(normalized)) return
        syncLanUrl = normalized
        syncUrl = resolveEffectiveUrl()
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_SYNC_LAN_URL, syncLanUrl)
            .putString(KEY_SYNC_URL, syncUrl)
            .apply()
    }

    /** Persist a full QR pairing (LAN + USB URLs, user + token). */
    fun setPairing(
        context: Context,
        lanUrl: String,
        usbUrl: String,
        username: String,
        token: String,
    ) {
        syncLanUrl = lanUrl.trim().let { if (it.isBlank()) "" else SyncUrl.normalize(it) }
        syncUsbUrl = usbUrl.trim().let { if (it.isBlank()) "" else SyncUrl.normalize(it) }
        syncUsername = username
        syncToken = token
        scrubInvalidSyncUrls()
        syncUrl = resolveEffectiveUrl()
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_SYNC_LAN_URL, syncLanUrl)
            .putString(KEY_SYNC_USB_URL, syncUsbUrl)
            .putString(KEY_SYNC_URL, syncUrl)
            .putString(KEY_SYNC_USER, username)
            .putString(KEY_SYNC_TOKEN, token)
            .apply()
    }

    private fun scrubInvalidSyncUrls() {
        if (!SyncUrl.isValid(syncLanUrl)) syncLanUrl = ""
        if (!SyncUrl.isValid(syncUsbUrl)) syncUsbUrl = ""
        if (!SyncUrl.isValid(syncUrl)) syncUrl = ""
    }

    /** Legacy pairing helper (single resolved URL). */
    fun setPairing(context: Context, url: String, username: String, token: String) {
        val normalized = SyncUrl.normalize(url)
        if (normalized.contains("127.0.0.1")) {
            setPairing(context, "", normalized, username, token)
        } else {
            setPairing(context, normalized, "", username, token)
        }
    }
}
