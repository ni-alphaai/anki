// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject
import org.json.JSONTokener

enum class ThemeMode { System, Light, Dark }

/**
 * How a two-sided sync conflict is resolved. [Ask] prompts each time (default);
 * the others auto-resolve by always keeping one side and overwriting the other.
 */
enum class SyncConflictPolicy { Ask, PreferPhone, PreferDesktop }

/**
 * The synced-config contract shared with the desktop. These keys and value
 * encodings MUST match qt/aqt/speedrun.py exactly: behavioral preferences live
 * in the collection config, which rides Anki's native sync, so toggling one on
 * either device and syncing converges it on the other. Encoding is pure JSON
 * (the same shape `col.set_config` writes), kept as small pure functions so it
 * can be round-tripped in a host unit test without the engine or a device.
 */
object SpeedrunConfig {
    // Behavioral-preference keys (verified against qt/aqt/speedrun.py:
    // _CFG_AUTO_ROUND, _CFG_DELAYED_FB, _CFG_SYNC_CONFLICT, _CFG_DIAGNOSTIC).
    const val KEY_AUTO_ROUND = "speedrunAutoReasoningRound"
    const val KEY_DELAYED_FB = "speedrunDelayedFeedbackExperiment"
    const val KEY_SYNC_CONFLICT = "speedrunSyncConflictPolicy"
    const val KEY_DIAGNOSTIC = "speedrunDiagnosticDone"

    // Desktop's on-conflict string encoding (_SYNC_CONFLICT_* in speedrun.py).
    const val CONFLICT_ASK = "ask"
    const val CONFLICT_PHONE = "phone"
    const val CONFLICT_DESKTOP = "desktop"

    /** A bool config value as JSON (`true` / `false`), matching `col.set_config`. */
    fun encodeBool(value: Boolean): String = if (value) "true" else "false"

    /** Decode a JSON bool config value; [default] for unset/garbage. */
    fun decodeBool(json: String?, default: Boolean): Boolean =
        when (json?.trim()) {
            "true" -> true
            "false" -> false
            else -> default
        }

    /** The desktop's plain string for a policy ("ask" / "phone" / "desktop"). */
    fun policyToString(policy: SyncConflictPolicy): String = when (policy) {
        SyncConflictPolicy.Ask -> CONFLICT_ASK
        SyncConflictPolicy.PreferPhone -> CONFLICT_PHONE
        SyncConflictPolicy.PreferDesktop -> CONFLICT_DESKTOP
    }

    /** Map the desktop's stored string back to a policy; defaults to [SyncConflictPolicy.Ask]. */
    fun policyFromString(value: String?): SyncConflictPolicy = when (value?.trim()) {
        CONFLICT_PHONE -> SyncConflictPolicy.PreferPhone
        CONFLICT_DESKTOP -> SyncConflictPolicy.PreferDesktop
        else -> SyncConflictPolicy.Ask
    }

    /** The conflict policy as a JSON string config value (e.g. `"ask"`, quoted). */
    fun encodeConflictPolicy(policy: SyncConflictPolicy): String =
        JSONObject.quote(policyToString(policy))

    /** Decode a JSON string config value into a policy (unwraps the quotes). */
    fun decodeConflictPolicy(json: String?): SyncConflictPolicy =
        policyFromString(decodeString(json))

    /** Parse a JSON string value (e.g. `"ask"` -> `ask`); null if not a string. */
    fun decodeString(json: String?): String? {
        if (json == null) return null
        return runCatching { JSONTokener(json).nextValue() as? String }.getOrNull()
    }
}

/** Tiny app-local preference store. */
object AppSettings {
    private const val PREFS = "speedrun_prefs"
    private const val KEY_THEME = "theme_mode"
    private const val KEY_AUTO_ROUND = "auto_reasoning_round"
    private const val KEY_DELAYED_FB = "delayed_feedback_experiment"
    private const val KEY_EXAMPLE_LOADED = "example_deck_loaded"
    private const val KEY_DIAGNOSTIC_DONE = "diagnostic_done"
    private const val KEY_SYNC_URL = "sync_url"
    private const val KEY_SYNC_LAN_URL = "sync_lan_url"
    private const val KEY_SYNC_USB_URL = "sync_usb_url"
    private const val KEY_SYNC_USER = "sync_username"
    private const val KEY_SYNC_TOKEN = "sync_token"
    private const val KEY_ANKIWEB_EMAIL = "ankiweb_email"
    private const val KEY_ANKIWEB_HKEY = "ankiweb_hkey"
    private const val KEY_ANKIWEB_ENDPOINT = "ankiweb_endpoint"
    private const val KEY_SYNC_USB = "sync_via_usb"
    private const val KEY_LAST_SYNCED = "last_synced_ms"
    private const val KEY_SYNC_CONFLICT_POLICY = "sync_conflict_policy"

    // Background scope for the fire-and-forget config writes that back the
    // synced behavioral settings. The in-memory state + SharedPreferences cache
    // update synchronously (so the UI reflects a toggle instantly); the config
    // write - which is what actually syncs - is enqueued here and serialized on
    // the engine's own single-writer dispatcher inside [EngineRepository].
    private val ioScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    var themeMode by mutableStateOf(ThemeMode.System)
        private set

    /** Auto-launch the end-of-session reasoning round instead of offering it. */
    var autoReasoningRound by mutableStateOf(false)
        private set

    /**
     * D7 experiment (default OFF, explicitly NOT evidence-established): for a
     * proficient student, withhold immediate correctness on practice questions
     * and defer it to the delayed feedback/progress surface. Mirrors the desktop
     * ``speedrunDelayedFeedbackExperiment`` flag.
     */
    var delayedFeedbackExperiment by mutableStateOf(false)
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

    /** AnkiWeb account email, remembered for convenience (password never stored). */
    var ankiwebEmail by mutableStateOf("")
        private set

    /** Persisted AnkiWeb session token (hkey) + endpoint, so the user stays
     * signed in and later syncs need no password. Blank when signed out. */
    var ankiwebHkey by mutableStateOf("")
        private set
    var ankiwebEndpoint by mutableStateOf("")
        private set

    /** True when a persisted AnkiWeb session exists. */
    val isAnkiwebSignedIn: Boolean
        get() = ankiwebHkey.isNotBlank()

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

    /**
     * What to do when a sync reports a genuine two-sided conflict. Default [Ask]
     * preserves the explicit "use phone / use desktop" choice; the prefer options
     * auto-resolve in one direction (and always report which copy was overwritten).
     */
    var syncConflictPolicy by mutableStateOf(SyncConflictPolicy.Ask)
        private set

    fun load(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        themeMode = runCatching {
            ThemeMode.valueOf(prefs.getString(KEY_THEME, ThemeMode.System.name)!!)
        }.getOrDefault(ThemeMode.System)
        autoReasoningRound = prefs.getBoolean(KEY_AUTO_ROUND, false)
        delayedFeedbackExperiment = prefs.getBoolean(KEY_DELAYED_FB, false)
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
        ankiwebEmail = prefs.getString(KEY_ANKIWEB_EMAIL, "") ?: ""
        ankiwebHkey = prefs.getString(KEY_ANKIWEB_HKEY, "") ?: ""
        ankiwebEndpoint = prefs.getString(KEY_ANKIWEB_ENDPOINT, "") ?: ""
        syncToken = prefs.getString(KEY_SYNC_TOKEN, "") ?: ""
        syncViaUsb = prefs.getBoolean(KEY_SYNC_USB, true)
        lastSyncedMs = prefs.getLong(KEY_LAST_SYNCED, 0L)
        syncConflictPolicy = runCatching {
            SyncConflictPolicy.valueOf(
                prefs.getString(KEY_SYNC_CONFLICT_POLICY, SyncConflictPolicy.Ask.name)!!,
            )
        }.getOrDefault(SyncConflictPolicy.Ask)
    }

    /**
     * Reconcile the synced behavioral preferences with the collection config -
     * the same keys the desktop uses. Call once the engine is open and again
     * after every successful sync so a preference changed on the other device
     * takes effect here.
     *
     * Per key: if config already holds a value, adopt it (config is the shared
     * source of truth) and refresh the local cache. If config has no value yet,
     * migrate a non-default local choice UP into config once so the user's
     * current setting isn't lost and propagates on the next sync; a default
     * local value is left unset so both platforms fall back to the same
     * documented default (and the collection isn't needlessly marked dirty).
     *
     * Robust by construction: if the collection isn't open or a key is missing,
     * [EngineRepository] returns null / no-ops and the local values stand.
     */
    suspend fun refreshFromConfig(context: Context) {
        autoReasoningRound = reconcileBool(
            context, SpeedrunConfig.KEY_AUTO_ROUND, KEY_AUTO_ROUND, autoReasoningRound, default = false,
        )
        delayedFeedbackExperiment = reconcileBool(
            context, SpeedrunConfig.KEY_DELAYED_FB, KEY_DELAYED_FB, delayedFeedbackExperiment, default = false,
        )
        diagnosticDone = reconcileBool(
            context, SpeedrunConfig.KEY_DIAGNOSTIC, KEY_DIAGNOSTIC_DONE, diagnosticDone, default = false,
        )
        syncConflictPolicy = reconcileConflictPolicy(context, syncConflictPolicy)
    }

    private suspend fun reconcileBool(
        context: Context,
        configKey: String,
        prefKey: String,
        localValue: Boolean,
        default: Boolean,
    ): Boolean {
        val raw = EngineRepository.getConfigJson(configKey)
        if (raw == null) {
            if (localValue != default) {
                EngineRepository.setConfigJson(configKey, SpeedrunConfig.encodeBool(localValue))
            }
            return localValue
        }
        val decoded = SpeedrunConfig.decodeBool(raw, default)
        persistBool(context, prefKey, decoded)
        return decoded
    }

    private suspend fun reconcileConflictPolicy(
        context: Context,
        localValue: SyncConflictPolicy,
    ): SyncConflictPolicy {
        val raw = EngineRepository.getConfigJson(SpeedrunConfig.KEY_SYNC_CONFLICT)
        if (raw == null) {
            if (localValue != SyncConflictPolicy.Ask) {
                EngineRepository.setConfigJson(
                    SpeedrunConfig.KEY_SYNC_CONFLICT,
                    SpeedrunConfig.encodeConflictPolicy(localValue),
                )
            }
            return localValue
        }
        val decoded = SpeedrunConfig.decodeConflictPolicy(raw)
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_SYNC_CONFLICT_POLICY, decoded.name).apply()
        return decoded
    }

    private fun persistBool(context: Context, key: String, value: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putBoolean(key, value).apply()
    }

    /**
     * Enqueue a synced-config write (fire-and-forget). No-ops safely when the
     * collection isn't open; the local cache still holds the choice, which a
     * later [refreshFromConfig] migrates up.
     */
    private fun writeConfig(configKey: String, json: String) {
        ioScope.launch { EngineRepository.setConfigJson(configKey, json) }
    }

    /** Record the time of a successful sync (drives the "last synced" affordance). */
    fun setLastSynced(context: Context, ms: Long) {
        lastSyncedMs = ms
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putLong(KEY_LAST_SYNCED, ms).apply()
    }

    /** Remember the AnkiWeb email (the password is never stored). */
    fun setAnkiwebEmail(context: Context, email: String) {
        ankiwebEmail = email
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_ANKIWEB_EMAIL, email).apply()
    }

    /** Persist an AnkiWeb sign-in: email + session token (hkey) + endpoint. The
     * hkey is a session key (not the password), so the user stays signed in. */
    fun setAnkiwebSession(context: Context, email: String, hkey: String, endpoint: String) {
        ankiwebEmail = email
        ankiwebHkey = hkey
        ankiwebEndpoint = endpoint
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(KEY_ANKIWEB_EMAIL, email)
            .putString(KEY_ANKIWEB_HKEY, hkey)
            .putString(KEY_ANKIWEB_ENDPOINT, endpoint)
            .apply()
    }

    /** Forget the AnkiWeb session (keeps the email for convenience). */
    fun clearAnkiwebSession(context: Context) {
        ankiwebHkey = ""
        ankiwebEndpoint = ""
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .remove(KEY_ANKIWEB_HKEY)
            .remove(KEY_ANKIWEB_ENDPOINT)
            .apply()
    }

    fun setExampleLoaded(context: Context, on: Boolean) {
        exampleLoaded = on
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putBoolean(KEY_EXAMPLE_LOADED, on).apply()
    }

    fun setDiagnosticDone(context: Context, on: Boolean) {
        diagnosticDone = on
        persistBool(context, KEY_DIAGNOSTIC_DONE, on)
        writeConfig(SpeedrunConfig.KEY_DIAGNOSTIC, SpeedrunConfig.encodeBool(on))
    }

    fun setThemeMode(context: Context, mode: ThemeMode) {
        // Device-local: appearance follows each device (and its OS theme), so it
        // is deliberately NOT routed through the synced collection config.
        themeMode = mode
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_THEME, mode.name).apply()
    }

    fun setSyncConflictPolicy(context: Context, policy: SyncConflictPolicy) {
        syncConflictPolicy = policy
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_SYNC_CONFLICT_POLICY, policy.name).apply()
        writeConfig(SpeedrunConfig.KEY_SYNC_CONFLICT, SpeedrunConfig.encodeConflictPolicy(policy))
    }

    fun setAutoReasoningRound(context: Context, on: Boolean) {
        autoReasoningRound = on
        persistBool(context, KEY_AUTO_ROUND, on)
        writeConfig(SpeedrunConfig.KEY_AUTO_ROUND, SpeedrunConfig.encodeBool(on))
    }

    fun setDelayedFeedbackExperiment(context: Context, on: Boolean) {
        delayedFeedbackExperiment = on
        persistBool(context, KEY_DELAYED_FB, on)
        writeConfig(SpeedrunConfig.KEY_DELAYED_FB, SpeedrunConfig.encodeBool(on))
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
