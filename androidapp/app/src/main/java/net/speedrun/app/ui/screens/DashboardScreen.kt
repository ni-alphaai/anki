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
import androidx.compose.runtime.LaunchedEffect
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
import net.speedrun.app.FeedbackReportUi
import net.speedrun.app.NextActionKind
import net.speedrun.app.NextActionUi
import net.speedrun.app.Readiness
import net.speedrun.app.nextAction
import net.speedrun.app.ui.ExamPlanCard
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.ReadinessVerdict
import net.speedrun.app.ui.ScreenHeader
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import net.speedrun.app.ui.theme.body
import net.speedrun.app.ui.theme.label
import net.speedrun.app.ui.theme.subhead

/**
 * The readiness dashboard: the honest verdict instrument plus the exam plan.
 * Deliberately its own destination (separate from the deck list) so "where do I
 * stand" is one glanceable screen rather than something buried below the decks.
 */
@Composable
fun DashboardScreen(
    onPractice: () -> Unit = {},
    onEditExam: () -> Unit = {},
    onOpenSection: (String) -> Unit = {},
) {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    var readiness by remember { mutableStateOf<Readiness?>(null) }
    var plan by remember { mutableStateOf<ExamPlanUi?>(null) }
    var feedback by remember { mutableStateOf<FeedbackReportUi?>(null) }
    var dash by remember { mutableStateOf<net.speedrun.app.TopicDashboardUi?>(null) }

    fun reload() {
        scope.launch {
            readiness = runCatching { EngineRepository.readiness() }.getOrNull()
            plan = runCatching { EngineRepository.examPlan() }.getOrNull()
            feedback = runCatching { EngineRepository.feedbackReport() }.getOrNull()
            dash = runCatching { EngineRepository.topicDashboard() }.getOrNull()
        }
    }

    // Reload on resume so the verdict reflects reviews done on other screens.
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { reload() }

    // Reload immediately after Library imports or sample seeding (desktop _refresh).
    LaunchedEffect(Unit) {
        EngineRepository.readinessRefresh.collect { reload() }
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
            else -> {
                ReadinessVerdict(r)
                Spacer(Modifier.height(Space.xl))
                NextActionCard(nextAction(r, plan), onPractice = onPractice, onEditExam = onEditExam)
            }
        }

        plan?.takeIf { it.hasProfile }?.let {
            Spacer(Modifier.height(Space.xl))
            ExamPlanCard(it)
        }

        feedback?.takeIf { it.total > 0 }?.let {
            Spacer(Modifier.height(Space.xl))
            SectionLabel("Feedback")
            FeedbackCard(it)
        }

        dash?.takeIf { it.hasTopics }?.let { d ->
            Spacer(Modifier.height(Space.xl))
            SectionLabel("MCAT topics")
            TopicSections(d, onOpenSection)
        }

        Spacer(Modifier.height(Space.xxl))
    }
}

/**
 * The single recommended next step, with an actionable CTA. Mirrors the desktop
 * "Next best action" card so both apps point the student at the same move.
 */
@Composable
private fun NextActionCard(na: NextActionUi, onPractice: () -> Unit, onEditExam: () -> Unit) {
    val c = Speedrun.colors
    SpeedrunCard {
        Text("NEXT BEST ACTION", color = c.accent, style = MaterialTheme.typography.label)
        Text(
            na.title,
            color = c.textPrimary,
            style = MaterialTheme.typography.subhead,
            modifier = Modifier.padding(top = Space.xs),
        )
        Text(
            na.detail,
            color = c.textSecondary,
            style = MaterialTheme.typography.body,
            modifier = Modifier.padding(top = Space.xs),
        )
        if (na.ctaLabel != null && na.kind != NextActionKind.NONE) {
            Spacer(Modifier.height(Space.m))
            PrimaryButton(na.ctaLabel) {
                when (na.kind) {
                    NextActionKind.PRACTICE -> onPractice()
                    NextActionKind.EDIT_EXAM -> onEditExam()
                    NextActionKind.NONE -> {}
                }
            }
        }
    }
}

/**
 * The end-of-session feedback report (Design 2 / D2): how many exam-style
 * questions were answered, the miss breakdown by root cause, and the weakest
 * topics. Mirrors the desktop reviewer's feedback report.
 */
@Composable
private fun FeedbackCard(fb: FeedbackReportUi) {
    val c = Speedrun.colors
    SpeedrunCard {
        Text(
            "Answered ${fb.total} exam-style question(s), ${fb.correct} correct.",
            color = c.textPrimary,
            style = MaterialTheme.typography.body,
        )
        val misses = listOf(
            "Memory" to fb.memoryMisses,
            "Reasoning" to fb.reasoningMisses,
            "Passage" to fb.passageMisses,
            "Test-taking" to fb.testTakingMisses,
        ).filter { it.second > 0 }
        if (misses.isNotEmpty()) {
            Spacer(Modifier.height(Space.s))
            Text(
                "Misses by cause \u2014 " +
                    misses.joinToString(", ") { "${it.first}: ${it.second}" } + ".",
                color = c.textSecondary,
                style = MaterialTheme.typography.body,
            )
        }
        if (fb.weakTopics.isNotEmpty()) {
            Spacer(Modifier.height(Space.s))
            Text(
                "Weakest topics: " + fb.weakTopics.take(5).joinToString(", ") + ".",
                color = c.textSecondary,
                style = MaterialTheme.typography.body,
            )
        }
    }
}
