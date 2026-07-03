// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import net.speedrun.app.AppSettings
import net.speedrun.app.EngineRepository
import net.speedrun.app.ExamProfileUi
import net.speedrun.app.McatScale
import net.speedrun.app.ThemeMode
import net.speedrun.app.ui.AppSwitch
import net.speedrun.app.ui.AppTextField
import net.speedrun.app.ui.DetailTopBar
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.SecondaryButton
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SegmentedControl
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import net.speedrun.app.ui.theme.body
import net.speedrun.app.ui.theme.caption
import net.speedrun.app.ui.theme.subhead
import android.content.Context
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.text.input.PasswordVisualTransformation
import kotlinx.coroutines.launch
import net.speedrun.app.SyncResult
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun SettingsScreen(onBack: () -> Unit, onEditExam: () -> Unit) {
    val c = Speedrun.colors
    val context = LocalContext.current
    var profile by remember { mutableStateOf<ExamProfileUi?>(null) }

    LaunchedEffect(Unit) {
        profile = runCatching { EngineRepository.examProfile() }.getOrNull()
    }

    Column(
        Modifier.fillMaxSize().background(c.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        DetailTopBar(title = "Settings", onBack = onBack)
        Spacer(Modifier.height(Space.l))

        SectionLabel("Appearance")
        SpeedrunCard {
            SegmentedControl(
                options = listOf("System", "Light", "Dark"),
                selectedIndex = when (AppSettings.themeMode) {
                    ThemeMode.System -> 0
                    ThemeMode.Light -> 1
                    ThemeMode.Dark -> 2
                },
                onSelect = { i ->
                    AppSettings.setThemeMode(
                        context,
                        when (i) {
                            1 -> ThemeMode.Light
                            2 -> ThemeMode.Dark
                            else -> ThemeMode.System
                        },
                    )
                },
            )
        }
        Spacer(Modifier.height(Space.xxl))

        SectionLabel("Exam")
        SpeedrunCard {
            Row(
                Modifier.fillMaxWidth().clickable { onEditExam() }.padding(vertical = Space.s),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("Exam date & target", color = c.textPrimary, style = MaterialTheme.typography.subhead)
                    Text(examSummary(profile), color = c.textSecondary, style = MaterialTheme.typography.body)
                }
                Icon(
                    Icons.AutoMirrored.Filled.KeyboardArrowRight,
                    contentDescription = null,
                    tint = c.textTertiary,
                )
            }
        }
        Spacer(Modifier.height(Space.xxl))

        SectionLabel("Study")
        SpeedrunCard {
            Row(
                Modifier.fillMaxWidth().padding(vertical = Space.xs),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("Auto reasoning round", color = c.textPrimary, style = MaterialTheme.typography.subhead)
                    Text(
                        "After you finish a deck's reviews, jump straight into a short reasoning check on those concepts (otherwise it's offered).",
                        color = c.textSecondary,
                        style = MaterialTheme.typography.body,
                        modifier = Modifier.padding(top = Space.xs),
                    )
                }
                Spacer(Modifier.width(Space.m))
                AppSwitch(
                    checked = AppSettings.autoReasoningRound,
                    onCheckedChange = { AppSettings.setAutoReasoningRound(context, it) },
                )
            }
        }
        Spacer(Modifier.height(Space.m))
        SpeedrunCard {
            Text("Daily limits", color = c.textPrimary, style = MaterialTheme.typography.subhead)
            Text(
                "New and review limits follow each deck's preset. Adjust them in the desktop app; the phone shares the same collection.",
                color = c.textSecondary,
                style = MaterialTheme.typography.body,
                modifier = Modifier.padding(top = Space.xs),
            )
        }
        Spacer(Modifier.height(Space.xxl))

        SectionLabel("Sync")
        SyncSection(context)
        Spacer(Modifier.height(Space.xxl))

        SectionLabel("About")
        SpeedrunCard {
            AboutRow("Exam", "MCAT (${McatScale.MIN}\u2013${McatScale.MAX})")
            AboutRow("Engine", "Shared Anki/Speedrun core (Rust)")
            AboutRow("Version", "0.1")
            AboutRow("License", "AGPL-3.0-or-later")
        }
        Spacer(Modifier.height(Space.xxl))
    }
}

@Composable
private fun SyncSection(context: Context) {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(AppSettings.syncUrl) }
    var username by remember { mutableStateOf(AppSettings.syncUsername) }
    var password by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<SyncResult?>(null) }
    var syncing by remember { mutableStateOf(false) }

    fun startSync(block: suspend () -> SyncResult) {
        if (url.isBlank() || username.isBlank() || syncing) return
        AppSettings.setSyncSettings(context, url.trim(), username.trim())
        syncing = true
        result = null
        scope.launch {
            val r = block()
            if (r is SyncResult.Ok) AppSettings.setLastSynced(context, System.currentTimeMillis())
            result = r
            syncing = false
        }
    }

    SpeedrunCard {
        Text("Self-hosted sync", color = c.textPrimary, style = MaterialTheme.typography.subhead)
        Text(
            "Sync this device's collection with your desktop through a self-hosted Anki sync server. Reviews flow both ways.",
            color = c.textSecondary,
            style = MaterialTheme.typography.body,
            modifier = Modifier.padding(top = Space.xs, bottom = Space.s),
        )
        AppTextField(
            value = url,
            onValueChange = { url = it },
            label = "Server URL",
            placeholder = "http://10.10.1.69:8080/",
        )
        Spacer(Modifier.height(Space.s))
        AppTextField(
            value = username,
            onValueChange = { username = it },
            label = "Username",
        )
        Spacer(Modifier.height(Space.s))
        AppTextField(
            value = password,
            onValueChange = { password = it },
            label = "Password",
            visualTransformation = PasswordVisualTransformation(),
        )
        Spacer(Modifier.height(Space.s))
        Text(
            "Last synced: ${lastSyncedLabel(AppSettings.lastSyncedMs)}",
            color = c.textTertiary,
            style = MaterialTheme.typography.caption,
        )
        Spacer(Modifier.height(Space.m))
        PrimaryButton(
            text = if (syncing) "Syncing\u2026" else "Sync now",
            enabled = !syncing,
        ) {
            startSync { EngineRepository.sync(url.trim(), username.trim(), password) }
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
                // Both sides changed: make the resolution explicit instead of failing.
                Spacer(Modifier.height(Space.s))
                PrimaryButton("Upload this device", enabled = !syncing) {
                    startSync {
                        EngineRepository.resolveSyncConflict(url.trim(), username.trim(), password, upload = true)
                    }
                }
                Spacer(Modifier.height(Space.xs))
                SecondaryButton("Use the server copy", enabled = !syncing) {
                    startSync {
                        EngineRepository.resolveSyncConflict(url.trim(), username.trim(), password, upload = false)
                    }
                }
            }
        }
    }
}

private fun lastSyncedLabel(ms: Long): String {
    if (ms <= 0L) return "Never"
    return SimpleDateFormat("MMM d, HH:mm", Locale.getDefault()).format(Date(ms))
}

@Composable
private fun AboutRow(label: String, value: String) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = Space.s),
    ) {
        Text(label, color = Speedrun.colors.textSecondary, style = MaterialTheme.typography.body, modifier = Modifier.weight(1f))
        Text(value, color = Speedrun.colors.textPrimary, style = MaterialTheme.typography.body, fontWeight = FontWeight.Medium)
    }
}

private fun examSummary(p: ExamProfileUi?): String {
    if (p == null || !p.isSet) return "Not set"
    val parts = mutableListOf<String>()
    if (p.examDateMs > 0) {
        parts += SimpleDateFormat("MMM d, yyyy", Locale.getDefault()).format(Date(p.examDateMs))
    }
    if (p.targetScore > 0) parts += "target ${p.targetScore}"
    return parts.joinToString(" \u00b7 ").ifBlank { "Not set" }
}
