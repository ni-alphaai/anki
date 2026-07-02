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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import net.speedrun.app.EngineRepository
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.theme.Display
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun

private val weekOptions = listOf(
    "In 4 weeks" to 4,
    "In 8 weeks" to 8,
    "In 12 weeks" to 12,
    "In 16 weeks" to 16,
    "In 26 weeks" to 26,
)

private val targetOptions = listOf(
    500 to "Around the median",
    505 to "Top third",
    510 to "Competitive",
    515 to "Strong",
    520 to "Top decile",
    525 to "Elite",
)

@Composable
fun OnboardingScreen(onDone: () -> Unit) {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    var weeks by remember { mutableStateOf<Int?>(null) }
    var target by remember { mutableStateOf<Int?>(null) }

    Column(
        Modifier.fillMaxSize().background(c.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        Spacer(Modifier.height(Space.xxl))
        Text("Set your exam", color = c.textPrimary, fontFamily = Display, fontSize = 34.sp, fontWeight = FontWeight.Bold)
        Text(
            "Speedrun anchors your plan to a date and a target tier \u2014 not an abstract retention rate.",
            color = c.textSecondary,
            fontSize = 15.sp,
            modifier = Modifier.padding(top = Space.s),
        )
        Spacer(Modifier.height(Space.xl))

        SectionLabel("When is your exam?")
        SpeedrunCard {
            weekOptions.forEachIndexed { i, (label, w) ->
                SelectRow(label, null, selected = weeks == w) { weeks = w }
                if (i < weekOptions.lastIndex) Divider()
            }
        }
        Spacer(Modifier.height(Space.xxl))

        SectionLabel("Target score")
        SpeedrunCard {
            targetOptions.forEachIndexed { i, (score, hint) ->
                SelectRow(score.toString(), hint, selected = target == score) { target = score }
                if (i < targetOptions.lastIndex) Divider()
            }
        }
        Spacer(Modifier.height(Space.xxl))

        PrimaryButton("Continue", enabled = weeks != null && target != null) {
            val w = weeks ?: return@PrimaryButton
            val t = target ?: return@PrimaryButton
            val dateMs = System.currentTimeMillis() + w * 7L * 24 * 3600 * 1000
            scope.launch {
                runCatching { EngineRepository.setExamProfile(dateMs, t) }
                onDone()
            }
        }
        Spacer(Modifier.height(Space.m))
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Text(
                "Skip for now",
                color = c.accent,
                fontSize = 15.sp,
                fontWeight = FontWeight.Medium,
                textAlign = TextAlign.Center,
                modifier = Modifier.clickable { onDone() }.padding(Space.s),
            )
        }
        Spacer(Modifier.height(Space.xxl))
    }
}

@Composable
private fun SelectRow(title: String, subtitle: String?, selected: Boolean, onClick: () -> Unit) {
    val c = Speedrun.colors
    Row(
        Modifier.fillMaxWidth().clickable { onClick() }.padding(vertical = Space.m),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, color = c.textPrimary, fontSize = 17.sp)
            if (subtitle != null) {
                Text(subtitle, color = c.textSecondary, fontSize = 13.sp)
            }
        }
        if (selected) {
            Icon(Icons.Filled.Check, contentDescription = "Selected", tint = c.accent)
        }
    }
}

@Composable
private fun Divider() {
    Box(
        Modifier.fillMaxWidth().height(0.6.dp).background(Speedrun.colors.separator),
    )
}
