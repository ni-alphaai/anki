// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import net.speedrun.app.AppSettings
import net.speedrun.app.EngineRepository
import net.speedrun.app.ExamProfileUi
import net.speedrun.app.ThemeMode
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.theme.Display
import net.speedrun.app.ui.theme.Radius
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
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
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back", tint = c.accent)
            }
        }
        Text(
            "Settings",
            color = c.textPrimary,
            fontFamily = Display,
            fontSize = 30.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(start = Space.xs),
        )
        Spacer(Modifier.height(Space.l))

        SectionLabel("Appearance")
        SpeedrunCard {
            Segmented(
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
                    Text("Exam date & target", color = c.textPrimary, fontSize = 17.sp)
                    Text(examSummary(profile), color = c.textSecondary, fontSize = 15.sp)
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
                    Text("Auto reasoning round", color = c.textPrimary, fontSize = 17.sp)
                    Text(
                        "After you finish a deck's reviews, jump straight into a short reasoning check on those concepts (otherwise it's offered).",
                        color = c.textSecondary,
                        fontSize = 15.sp,
                        modifier = Modifier.padding(top = Space.xs),
                    )
                }
                Spacer(Modifier.width(Space.m))
                Switch(
                    checked = AppSettings.autoReasoningRound,
                    onCheckedChange = { AppSettings.setAutoReasoningRound(context, it) },
                )
            }
        }
        Spacer(Modifier.height(Space.m))
        SpeedrunCard {
            Text("Daily limits", color = c.textPrimary, fontSize = 17.sp)
            Text(
                "New and review limits follow each deck's preset. Adjust them in the desktop app; the phone shares the same collection.",
                color = c.textSecondary,
                fontSize = 15.sp,
                modifier = Modifier.padding(top = Space.xs),
            )
        }
        Spacer(Modifier.height(Space.xxl))

        SectionLabel("About")
        SpeedrunCard {
            AboutRow("Exam", "MCAT (472\u2013528)")
            AboutRow("Engine", "Shared Anki/Speedrun core (Rust)")
            AboutRow("Version", "0.1")
            AboutRow("License", "AGPL-3.0-or-later")
        }
        Spacer(Modifier.height(Space.xxl))
    }
}

@Composable
private fun Segmented(options: List<String>, selectedIndex: Int, onSelect: (Int) -> Unit) {
    val c = Speedrun.colors
    Row(
        Modifier.fillMaxWidth()
            .clip(RoundedCornerShape(Radius.button))
            .background(c.separator)
            .padding(3.dp),
    ) {
        options.forEachIndexed { i, label ->
            val selected = i == selectedIndex
            Box(
                Modifier.weight(1f)
                    .clip(RoundedCornerShape(10.dp))
                    .background(if (selected) c.surface else Color.Transparent)
                    .clickable { onSelect(i) }
                    .padding(vertical = 8.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    label,
                    color = if (selected) c.textPrimary else c.textSecondary,
                    fontSize = 14.sp,
                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                )
            }
        }
    }
}

@Composable
private fun AboutRow(label: String, value: String) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = Space.s),
    ) {
        Text(label, color = Speedrun.colors.textSecondary, fontSize = 15.sp, modifier = Modifier.weight(1f))
        Text(value, color = Speedrun.colors.textPrimary, fontSize = 15.sp, fontWeight = FontWeight.Medium)
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
