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
import net.speedrun.app.SyncDiscovery
import net.speedrun.app.SyncPairing
import net.speedrun.app.SyncResult
import net.speedrun.app.ui.AppTextField
import net.speedrun.app.ui.DetailTopBar
import net.speedrun.app.ui.GroupFootnote
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.SecondaryButton
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SpeedrunCard
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
        SectionLabel("Sync with desktop")
        SyncSection(context)
        GroupFootnote(
            "USB sync is best on guest Wi-Fi: plug the phone into this Mac, enable " +
                "USB debugging, tap Sync via USB on the phone, and scan the desktop QR " +
                "or enter the USB server URL (http://127.0.0.1:<port>/).",
        )
        Spacer(Modifier.height(Space.xxl))
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
            val r = block()
            if (r is SyncResult.Ok) {
                AppSettings.setLastSynced(context, System.currentTimeMillis())
                if (persistPairing) {
                    AppSettings.setPairing(context, u, username, token)
                }
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
            setOrientationLocked(false)
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
                PrimaryButton(
                    text = if (syncing) "Syncing…" else "Sync via USB",
                    enabled = !syncing,
                ) {
                    runUsbSync()
                }
                Spacer(Modifier.height(Space.xs))
                SecondaryButton(
                    text = if (syncing) "Syncing…" else "Sync over Wi-Fi",
                    enabled = !syncing,
                ) {
                    runWifiSync()
                }
                Spacer(Modifier.height(Space.s))
                Text(
                    "Or pick which copy wins:",
                    color = c.textSecondary,
                    style = MaterialTheme.typography.caption,
                )
                Spacer(Modifier.height(Space.xs))
                SecondaryButton(
                    text = if (syncing) "Syncing…" else "Use phone data",
                    enabled = !syncing,
                ) {
                    runDirectionalSync(uploadPhone = true)
                }
                Spacer(Modifier.height(Space.xs))
                SecondaryButton(
                    text = if (syncing) "Syncing…" else "Use desktop data",
                    enabled = !syncing,
                ) {
                    runDirectionalSync(uploadPhone = false)
                }
                Spacer(Modifier.height(Space.s))
                SecondaryButton("Scan a new code", enabled = !syncing) { launchScan() }
                Spacer(Modifier.height(Space.xs))
                SecondaryButton("Enter manually", enabled = !syncing) {
                    url = AppSettings.syncUrl
                    username = AppSettings.syncUsername
                    password = ""
                    showManual = true
                }
            }
        } else {
            PrimaryButton(
                text = if (syncing) "Syncing…" else "Sync via USB",
                enabled = !syncing,
            ) {
                runUsbSync()
            }
            Spacer(Modifier.height(Space.xs))
            SecondaryButton(if (showManual) "Hide manual entry" else "Enter manually") {
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
                SecondaryButton("Cancel", enabled = !syncing) { showManual = false }
            } else {
                Spacer(Modifier.height(Space.s))
                Text(
                    "Or pick which copy wins:",
                    color = c.textSecondary,
                    style = MaterialTheme.typography.caption,
                )
                Spacer(Modifier.height(Space.xs))
                SecondaryButton(
                    text = if (syncing) "Syncing…" else "Use phone data",
                    enabled = !syncing,
                ) {
                    runDirectionalSync(uploadPhone = true)
                }
                Spacer(Modifier.height(Space.xs))
                SecondaryButton(
                    text = if (syncing) "Syncing…" else "Use desktop data",
                    enabled = !syncing,
                ) {
                    runDirectionalSync(uploadPhone = false)
                }
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
                val (cu, cuser, cpass) = currentCredentials()
                Spacer(Modifier.height(Space.s))
                PrimaryButton("Use phone data", enabled = !syncing) {
                    runSync(cu, cuser, cpass) {
                        EngineRepository.resolveSyncConflict(cu, cuser, cpass, upload = true)
                    }
                }
                Spacer(Modifier.height(Space.xs))
                SecondaryButton("Use desktop data", enabled = !syncing) {
                    runSync(cu, cuser, cpass) {
                        EngineRepository.resolveSyncConflict(cu, cuser, cpass, upload = false)
                    }
                }
            }
        }
    }
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

private fun lastSyncedLabel(ms: Long): String {
    if (ms <= 0L) return "Never"
    return SimpleDateFormat("MMM d, HH:mm", Locale.getDefault()).format(Date(ms))
}
