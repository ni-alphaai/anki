// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import kotlinx.coroutines.launch
import net.speedrun.app.ExamPlanUi
import net.speedrun.app.EngineRepository
import net.speedrun.app.Readiness
import net.speedrun.app.ui.ExamPlanCard
import net.speedrun.app.ui.ReadinessVerdict
import net.speedrun.app.ui.ScreenHeader
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import net.speedrun.app.ui.theme.body

/**
 * The readiness dashboard: the honest verdict instrument plus the exam plan.
 * Deliberately its own destination (separate from the deck list) so "where do I
 * stand" is one glanceable screen rather than something buried below the decks.
 */
@Composable
fun DashboardScreen() {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    var readiness by remember { mutableStateOf<Readiness?>(null) }
    var plan by remember { mutableStateOf<ExamPlanUi?>(null) }

    // Reload on resume so the verdict reflects reviews done on other screens.
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        scope.launch {
            readiness = runCatching { EngineRepository.readiness() }.getOrNull()
            plan = runCatching { EngineRepository.examPlan() }.getOrNull()
        }
    }

    Column(
        Modifier.fillMaxSize().background(c.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        ScreenHeader(title = "Dashboard")
        Spacer(Modifier.height(Space.m))

        when (val r = readiness) {
            null -> SpeedrunCard {
                Text("Reading your signals\u2026", color = c.textSecondary, style = MaterialTheme.typography.body)
            }
            else -> ReadinessVerdict(r)
        }

        plan?.takeIf { it.hasProfile }?.let {
            Spacer(Modifier.height(Space.xl))
            ExamPlanCard(it)
        }

        Spacer(Modifier.height(Space.xxl))
    }
}
