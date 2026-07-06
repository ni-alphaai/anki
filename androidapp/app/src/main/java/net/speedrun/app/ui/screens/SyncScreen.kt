// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import android.content.Context
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.launch
import net.speedrun.app.AppSettings
import net.speedrun.app.EngineRepository
import net.speedrun.app.PortraitCaptureActivity
import net.speedrun.app.SyncConflictPolicy
import net.speedrun.app.SyncDiscovery
import net.speedrun.app.SyncPairing
import net.speedrun.app.SyncUrl
import net.speedrun.app.SyncResult
import net.speedrun.app.ui.AppTextField
import net.speedrun.app.ui.DetailTopBar
import net.speedrun.app.ui.GroupFootnote
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.SecondaryButton
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SegmentedControl
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.TertiaryButton
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import net.speedrun.app.ui.theme.body
import net.speedrun.app.ui.theme.caption
import net.speedrun.app.ui.theme.subhead
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Sync as its own pushed screen (previously a large card crammed into Settings):
 * pair via QR, see paired/last-synced status, sync now, and resolve conflicts.
 */
@Composable
fun SyncScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    Column(
        Modifier.fillMaxSize().background(Speedrun.colors.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        DetailTopBar(title = "Sync", onBack = onBack)
        Spacer(Modifier.height(Space.l))
        // AnkiWeb (cloud) is the recommended, primary sync - no desktop or shared
        // network needed - so it leads; desktop/LAN pairing is the fallback below.
        SectionLabel("Sync with AnkiWeb (recommended)")
        AnkiWebSyncSection(context)
        GroupFootnote(
            "AnkiWeb is Anki's free cloud sync - no desktop or same-network needed. " +
                "Sign in with your AnkiWeb account and your reviews and practice " +
                "history sync across devices.",
        )
        Spacer(Modifier.height(Space.l))
        SectionLabel("Sync with desktop (offline / same network)")
        SyncSection(context)
        GroupFootnote(
            "USB sync is best on guest Wi-Fi: plug the phone into this Mac, enable " +
                "USB debugging, tap Sync via USB on the phone, and scan the desktop QR " +
                "or enter the USB server URL (http://127.0.0.1:<port>/).",
        )
        Spacer(Modifier.height(Space.xxl))
    }
}

/**
 * AnkiWeb (cloud) sign-in + sync. Unlike the desktop pairing above, this needs
 * no LAN/USB link - the phone talks to AnkiWeb directly. Speedrun's practice
 * history (sr_attempts) rides along because [EngineRepository.sync] note-encodes
 * it before every sync (AnkiWeb keeps notes, drops the custom chunk). The
 * password is used to log in and is never stored; only the email is remembered.
 */
@Composable
private fun AnkiWebSyncSection(context: Context) {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    var email by remember { mutableStateOf(AppSettings.ankiwebEmail) }
    var password by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<SyncResult?>(null) }
    var syncing by remember { mutableStateOf(false) }

    fun sync(upload: Boolean? = null) {
        val user = email.trim()
        val pass = password
        if (user.isBlank() || pass.isBlank() || syncing) return
        AppSettings.setAnkiwebEmail(context, user)
        syncing = true
        result = null
        scope.launch {
            var r: SyncResult = if (upload == null) {
                EngineRepository.sync(SyncUrl.ANKIWEB_ENDPOINT, user, pass)
            } else {
                EngineRepository.resolveSyncConflict(SyncUrl.ANKIWEB_ENDPOINT, user, pass, upload = upload)
            }
            // Honor the saved conflict preference (same as desktop sync), so the
            // first full sync isn't a dead-end prompt.
            val policy = AppSettings.syncConflictPolicy
            if (r is SyncResult.Conflict && policy != SyncConflictPolicy.Ask) {
                val preferPhone = policy == SyncConflictPolicy.PreferPhone
                val resolved = EngineRepository.resolveSyncConflict(
                    SyncUrl.ANKIWEB_ENDPOINT, user, pass, upload = preferPhone,
                )
                r = if (resolved is SyncResult.Ok) {
                    SyncResult.Ok(autoResolvedConflictMessage(preferPhone))
                } else {
                    resolved
                }
            }
            if (r is SyncResult.Ok) {
                AppSettings.setLastSynced(context, System.currentTimeMillis())
                AppSettings.refreshFromConfig(context)
            }
            result = r
            syncing = false
        }
    }

    SpeedrunCard {
        Text("Sync with AnkiWeb", color = c.textPrimary, style = MaterialTheme.typography.subhead)
        Text(
            "Sign in with your AnkiWeb account to sync over the cloud.",
            color = c.textSecondary,
            style = MaterialTheme.typography.body,
            modifier = Modifier.padding(top = Space.xs, bottom = Space.m),
        )
        Text(
            "Last synced: ${lastSyncedLabel(AppSettings.lastSyncedMs)}",
            color = c.textTertiary,
            style = MaterialTheme.typography.caption,
            modifier = Modifier.padding(bottom = Space.m),
        )
        AppTextField(value = email, onValueChange = { email = it }, label = "AnkiWeb email")
        Spacer(Modifier.height(Space.s))
        AppTextField(
            value = password,
            onValueChange = { password = it },
            label = "Password",
            placeholder = "Not stored on this device",
            visualTransformation = PasswordVisualTransformation(),
        )
        Spacer(Modifier.height(Space.m))
        PrimaryButton(
            text = if (syncing) "Syncing…" else "Sync with AnkiWeb",
            enabled = !syncing && email.isNotBlank() && password.isNotBlank(),
        ) {
            sync()
        }
        result?.let { r ->
            Spacer(Modifier.height(Space.s))
            val (msg, color) = when (r) {
                is SyncResult.Ok -> r.message to c.readinessGood
                is SyncResult.Conflict -> r.message to c.readinessWarn
                is SyncResult.Error -> "Sync failed: ${r.message}" to c.readinessBad
            }
            Text(msg, color = color, style = MaterialTheme.typography.body)
            if (r is SyncResult.Conflict) {
                Spacer(Modifier.height(Space.s))
                PrimaryButton("Use phone data", enabled = !syncing) { sync(upload = true) }
                Spacer(Modifier.height(Space.xs))
                SecondaryButton("Use AnkiWeb data", enabled = !syncing) { sync(upload = false) }
            }
        }
    }
}

@Composable
private fun SyncSection(context: Context) {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    var result by remember { mutableStateOf<SyncResult?>(null) }
    var syncing by remember { mutableStateOf(false) }
    var showManual by remember { mutableStateOf(false) }
    var url by remember { mutableStateOf(AppSettings.syncUrl) }
    var username by remember { mutableStateOf(AppSettings.syncUsername) }
    var password by remember { mutableStateOf("") }

    fun effectiveToken(typed: String): String =
        typed.ifBlank { AppSettings.syncToken }

    fun runSync(
        u: String,
        user: String,
        pass: String,
        persistPairing: Boolean = false,
        block: suspend () -> SyncResult,
    ) {
        val token = effectiveToken(pass)
        if (u.isBlank() || user.isBlank() || token.isBlank() || syncing) return
        syncing = true
        result = null
        scope.launch {
            var r = block()
            // Honor the saved conflict preference: instead of prompting, auto-resolve
            // in the chosen direction using the same credentials that reported the
            // conflict, then report exactly which copy won so the overwrite is
            // never silent. Ask keeps the explicit choice buttons below.
            val policy = AppSettings.syncConflictPolicy
            if (r is SyncResult.Conflict && policy != SyncConflictPolicy.Ask) {
                val preferPhone = policy == SyncConflictPolicy.PreferPhone
                val resolved = EngineRepository.resolveSyncConflict(u, user, token, upload = preferPhone)
                r = if (resolved is SyncResult.Ok) {
                    SyncResult.Ok(autoResolvedConflictMessage(preferPhone))
                } else {
                    resolved
                }
            }
            if (r is SyncResult.Ok) {
                AppSettings.setLastSynced(context, System.currentTimeMillis())
                if (persistPairing) {
                    AppSettings.setPairing(context, u, username, token)
                }
                // Behavioral preferences ride the synced collection config, so a
                // change made on the other device only takes effect once we
                // re-read it here (this covers normal, full, and conflict-
                // resolved syncs - they all land in this success branch).
                AppSettings.refreshFromConfig(context)
            }
            result = r
            syncing = false
        }
    }

    fun currentCredentials(): Triple<String, String, String> {
        if (showManual) {
            return Triple(url.trim(), username.trim(), effectiveToken(password))
        }
        if (AppSettings.isPaired) {
            return Triple(
                AppSettings.resolveEffectiveUrl(),
                AppSettings.syncUsername,
                AppSettings.syncToken,
            )
        }
        return Triple(url.trim(), username.trim(), effectiveToken(password))
    }

    fun runDirectionalSync(uploadPhone: Boolean) {
        val (u, user, pass) = currentCredentials()
        runSync(u, user, pass) {
            EngineRepository.resolveSyncConflict(u, user, pass, upload = uploadPhone)
        }
    }

    val scanLauncher = rememberLauncherForActivityResult(ScanContract()) { res ->
        val contents = res.contents ?: return@rememberLauncherForActivityResult
        val p = SyncPairing.parse(contents)
        if (p == null) {
            result = SyncResult.Error("That isn't a Speedrun pairing code.")
            return@rememberLauncherForActivityResult
        }
        if (p.isExpired()) {
            result = SyncResult.Error(
                "This pairing code has expired. On the desktop open Sync with phone " +
                    "to show a fresh code, then scan again.",
            )
            return@rememberLauncherForActivityResult
        }
        val syncUrl = p.resolveUrl(AppSettings.syncViaUsb)
        AppSettings.setPairing(context, p.url, p.usbUrl, p.user, p.token)
        url = syncUrl
        username = p.user
        password = p.token
        runSync(syncUrl, p.user, p.token) {
            EngineRepository.sync(syncUrl, p.user, p.token)
        }
    }

    fun launchScan() {
        val opts = ScanOptions().apply {
            setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            setPrompt("Scan the code on your desktop's Sync screen")
            setBeepEnabled(false)
            // Lock to the capture activity's manifest orientation (portrait).
            setOrientationLocked(true)
            setCaptureActivity(PortraitCaptureActivity::class.java)
        }
        scanLauncher.launch(opts)
    }

    fun runUsbSync() {
        AppSettings.setSyncViaUsb(context, true)
        if (!AppSettings.isPaired) {
            launchScan()
            return
        }
        val usbUrl = AppSettings.resolveUsbUrl()
        if (usbUrl.isBlank()) {
            result = SyncResult.Error(
                "No USB URL saved. Plug in USB, then scan the desktop QR or enter " +
                    "the http://127.0.0.1:<port>/ URL manually.",
            )
            return
        }
        url = usbUrl
        runSync(usbUrl, AppSettings.syncUsername, AppSettings.syncToken) {
            EngineRepository.sync(usbUrl, AppSettings.syncUsername, AppSettings.syncToken)
        }
    }

    fun runWifiSync() {
        AppSettings.setSyncViaUsb(context, false)
        val lanUrl = AppSettings.resolveLanUrl()
        if (AppSettings.isPaired && lanUrl.isNotBlank()) {
            runSync(lanUrl, AppSettings.syncUsername, AppSettings.syncToken) {
                EngineRepository.sync(lanUrl, AppSettings.syncUsername, AppSettings.syncToken)
            }
            return
        }
        val (u, user, pass) = currentCredentials()
        runSync(u, user, pass) {
            EngineRepository.sync(u, user, pass)
        }
    }

    // One primary Sync action; the selected transport (USB / Wi-Fi) picks the path.
    // The directional "use phone / use desktop" choice only appears on a real
    // conflict, below, so it never clutters the default screen.
    fun runPairedSync() {
        if (AppSettings.syncViaUsb) runUsbSync() else runWifiSync()
    }

    SpeedrunCard {
        Text("Sync with desktop", color = c.textPrimary, style = MaterialTheme.typography.subhead)
        Text(
            "Plug in USB for guest Wi-Fi, or use Wi-Fi when on the same network.",
            color = c.textSecondary,
            style = MaterialTheme.typography.body,
            modifier = Modifier.padding(top = Space.xs, bottom = Space.m),
        )

        if (AppSettings.isPaired) {
            LaunchedEffect(Unit) {
                SyncDiscovery.findServer(context) { found ->
                    if (found.isNotBlank()) {
                        AppSettings.updateLanUrl(context, found)
                    }
                }
            }
            Text(
                "Paired with ${AppSettings.resolveEffectiveUrl()}",
                color = c.textPrimary,
                style = MaterialTheme.typography.body,
            )
            Text(
                "Last synced: ${lastSyncedLabel(AppSettings.lastSyncedMs)}",
                color = c.textTertiary,
                style = MaterialTheme.typography.caption,
                modifier = Modifier.padding(top = Space.xs, bottom = Space.m),
            )
            if (!showManual) {
                Text(
                    "Connection",
                    color = c.textSecondary,
                    style = MaterialTheme.typography.caption,
                    modifier = Modifier.padding(bottom = Space.xs),
                )
                SegmentedControl(
                    options = listOf("USB", "Wi-Fi"),
                    selectedIndex = if (AppSettings.syncViaUsb) 0 else 1,
                    onSelect = { AppSettings.setSyncViaUsb(context, it == 0) },
                )
                Spacer(Modifier.height(Space.m))
                PrimaryButton(
                    text = if (syncing) "Syncing…" else "Sync now",
                    enabled = !syncing,
                ) {
                    runPairedSync()
                }
                Spacer(Modifier.height(Space.s))
                SecondaryButton("Scan a new code", enabled = !syncing) { launchScan() }
                Spacer(Modifier.height(Space.xs))
                TertiaryButton("Enter manually", enabled = !syncing) {
                    url = AppSettings.syncUrl
                    username = AppSettings.syncUsername
                    password = ""
                    showManual = true
                }
                Spacer(Modifier.height(Space.m))
                ConflictPolicySelector(context)
            }
        } else {
            PrimaryButton(
                text = if (syncing) "Syncing…" else "Scan desktop code",
                enabled = !syncing,
            ) {
                runUsbSync()
            }
            Spacer(Modifier.height(Space.xs))
            TertiaryButton(if (showManual) "Hide manual entry" else "Enter manually") {
                showManual = !showManual
            }
        }

        if (showManual) {
            ManualPairingFields(
                url = url,
                onUrlChange = { url = it },
                username = username,
                onUsernameChange = { username = it },
                password = password,
                onPasswordChange = { password = it },
                showStoredKeyHint = AppSettings.isPaired && password.isBlank(),
            )
            Spacer(Modifier.height(Space.m))
            PrimaryButton(
                text = if (syncing) "Syncing…" else if (AppSettings.isPaired) "Save & sync" else "Pair & sync",
                enabled = !syncing,
            ) {
                val u = url.trim()
                val user = username.trim()
                val token = effectiveToken(password)
                runSync(u, user, password, persistPairing = true) {
                    EngineRepository.sync(u, user, token)
                }
            }
            if (AppSettings.isPaired) {
                Spacer(Modifier.height(Space.xs))
                TertiaryButton("Cancel", enabled = !syncing) { showManual = false }
            }
        }

        result?.let { r ->
            Spacer(Modifier.height(Space.s))
            val (msg, color) = when (r) {
                is SyncResult.Ok -> r.message to c.readinessGood
                is SyncResult.Conflict -> r.message to c.readinessWarn
                is SyncResult.Error -> "Sync failed: ${r.message}" to c.readinessBad
            }
            Text(msg, color = color, style = MaterialTheme.typography.body)
            if (r is SyncResult.Conflict) {
                Spacer(Modifier.height(Space.s))
                PrimaryButton("Use phone data", enabled = !syncing) {
                    runDirectionalSync(uploadPhone = true)
                }
                Spacer(Modifier.height(Space.xs))
                SecondaryButton("Use desktop data", enabled = !syncing) {
                    runDirectionalSync(uploadPhone = false)
                }
            }
        }
    }
}

/**
 * The persisted "on conflict" preference, as a compact caption + segmented
 * control that mirrors the Connection (USB / Wi-Fi) picker above it. The helper
 * line spells out the overwrite so choosing an auto-resolve is an informed act.
 */
@Composable
private fun ConflictPolicySelector(context: Context) {
    val c = Speedrun.colors
    val policy = AppSettings.syncConflictPolicy
    Text(
        "On conflict",
        color = c.textSecondary,
        style = MaterialTheme.typography.caption,
        modifier = Modifier.padding(bottom = Space.xs),
    )
    SegmentedControl(
        options = listOf("Ask", "Phone", "Desktop"),
        selectedIndex = when (policy) {
            SyncConflictPolicy.Ask -> 0
            SyncConflictPolicy.PreferPhone -> 1
            SyncConflictPolicy.PreferDesktop -> 2
        },
        onSelect = { i ->
            AppSettings.setSyncConflictPolicy(
                context,
                when (i) {
                    1 -> SyncConflictPolicy.PreferPhone
                    2 -> SyncConflictPolicy.PreferDesktop
                    else -> SyncConflictPolicy.Ask
                },
            )
        },
    )
    Text(
        when (policy) {
            SyncConflictPolicy.Ask -> "You'll choose which copy to keep."
            SyncConflictPolicy.PreferPhone -> "Auto-keeps this phone; overwrites the desktop copy."
            SyncConflictPolicy.PreferDesktop -> "Auto-keeps the desktop; overwrites this phone's copy."
        },
        color = c.textTertiary,
        style = MaterialTheme.typography.caption,
        modifier = Modifier.padding(top = Space.xs),
    )
}

@Composable
private fun ManualPairingFields(
    url: String,
    onUrlChange: (String) -> Unit,
    username: String,
    onUsernameChange: (String) -> Unit,
    password: String,
    onPasswordChange: (String) -> Unit,
    showStoredKeyHint: Boolean,
) {
    val c = Speedrun.colors
    Spacer(Modifier.height(Space.s))
    AppTextField(
        value = url,
        onValueChange = onUrlChange,
        label = "Server URL",
        placeholder = "http://127.0.0.1:55413/",
    )
    Spacer(Modifier.height(Space.s))
    AppTextField(value = username, onValueChange = onUsernameChange, label = "Username")
    Spacer(Modifier.height(Space.s))
    AppTextField(
        value = password,
        onValueChange = onPasswordChange,
        label = "Key",
        placeholder = if (showStoredKeyHint) "Leave blank to keep saved key" else "From desktop Sync screen",
        visualTransformation = PasswordVisualTransformation(),
    )
    if (showStoredKeyHint) {
        Text(
            "Using the saved pairing key.",
            color = c.textTertiary,
            style = MaterialTheme.typography.caption,
            modifier = Modifier.padding(top = Space.xs),
        )
    }
}

/** Transparent success text after the saved preference auto-resolved a conflict. */
private fun autoResolvedConflictMessage(preferPhone: Boolean): String =
    if (preferPhone) {
        "Conflict resolved: kept this phone's data (your preference); the desktop copy was overwritten."
    } else {
        "Conflict resolved: kept the desktop's data (your preference); this phone's copy was overwritten."
    }

private fun lastSyncedLabel(ms: Long): String {
    if (ms <= 0L) return "Never"
    return SimpleDateFormat("MMM d, HH:mm", Locale.getDefault()).format(Date(ms))
}
