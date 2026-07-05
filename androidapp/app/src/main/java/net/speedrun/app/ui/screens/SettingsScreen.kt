// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import net.speedrun.app.AppSettings
import net.speedrun.app.EngineRepository
import net.speedrun.app.ExamProfileUi
import net.speedrun.app.McatScale
import net.speedrun.app.ThemeMode
import net.speedrun.app.ui.AppSwitch
import net.speedrun.app.ui.DetailTopBar
import net.speedrun.app.ui.GroupFootnote
import net.speedrun.app.ui.RowDivider
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SegmentedControl
import net.speedrun.app.ui.SettingsGroup
import net.speedrun.app.ui.SettingsRow
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    onEditExam: () -> Unit,
    onOpenSync: () -> Unit,
    onRetakeDiagnostic: () -> Unit,
) {
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
        SettingsGroup {
            Box(Modifier.padding(Space.m)) {
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
        }
        Spacer(Modifier.height(Space.xl))

        SectionLabel("Exam")
        SettingsGroup {
            SettingsRow(
                title = "Exam date & target",
                subtitle = examSummary(profile),
                showChevron = true,
                onClick = onEditExam,
            )
            RowDivider()
            SettingsRow(
                title = "Placement diagnostic",
                value = "Retake",
                showChevron = true,
                onClick = onRetakeDiagnostic,
            )
        }
        Spacer(Modifier.height(Space.xl))

        SectionLabel("Study")
        SettingsGroup {
            SettingsRow(
                title = "Auto reasoning round",
                trailing = {
                    AppSwitch(
                        checked = AppSettings.autoReasoningRound,
                        onCheckedChange = { AppSettings.setAutoReasoningRound(context, it) },
                    )
                },
            )
            RowDivider()
            SettingsRow(
                title = "Delayed feedback (experimental)",
                subtitle = "If you're already proficient, bank correctness and reveal it later",
                trailing = {
                    AppSwitch(
                        checked = AppSettings.delayedFeedbackExperiment,
                        onCheckedChange = {
                            AppSettings.setDelayedFeedbackExperiment(context, it)
                        },
                    )
                },
            )
            RowDivider()
            SettingsRow(title = "Daily limits", value = "Per-deck preset")
        }
        GroupFootnote(
            "Auto reasoning jumps into a short reasoning check right after a deck's " +
                "reviews. Daily limits follow each deck's preset - adjust them in the " +
                "desktop app; the phone shares the same collection.",
        )
        Spacer(Modifier.height(Space.xl))

        SectionLabel("Sync")
        SettingsGroup {
            SettingsRow(
                title = "Sync with desktop",
                subtitle = syncSubtitle(),
                showChevron = true,
                onClick = onOpenSync,
            )
        }
        Spacer(Modifier.height(Space.xl))

        SectionLabel("About")
        SettingsGroup {
            SettingsRow(title = "Exam", value = "MCAT (${McatScale.MIN}–${McatScale.MAX})")
            RowDivider()
            SettingsRow(title = "Engine", value = "Shared Rust core")
            RowDivider()
            SettingsRow(title = "Version", value = "0.1")
            RowDivider()
            SettingsRow(title = "License", value = "AGPL-3.0-or-later")
        }
        Spacer(Modifier.height(Space.xxl))
    }
}

private fun syncSubtitle(): String {
    if (!AppSettings.isPaired) return "Not paired"
    val last = AppSettings.lastSyncedMs
    val when_ = if (last <= 0L) "not synced yet"
        else "synced " + SimpleDateFormat("MMM d, HH:mm", Locale.getDefault()).format(Date(last))
    return "Paired · $when_"
}

private fun examSummary(p: ExamProfileUi?): String {
    if (p == null || !p.isSet) return "Not set"
    val parts = mutableListOf<String>()
    if (p.examDateMs > 0) {
        parts += SimpleDateFormat("MMM d, yyyy", Locale.getDefault()).format(Date(p.examDateMs))
    }
    if (p.targetScore > 0) parts += "${p.targetScore}"
    return parts.joinToString(" · ").ifBlank { "Not set" }
}
