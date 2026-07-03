// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
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
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import kotlinx.coroutines.launch
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.PathEffect
import net.speedrun.app.CalibrationUi
import net.speedrun.app.CoverageUi
import net.speedrun.app.EngineRepository
import net.speedrun.app.PerformanceUi
import net.speedrun.app.ui.KeyValueRow
import net.speedrun.app.ui.ScreenHeader
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SignalBar
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.pct
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import net.speedrun.app.ui.theme.body
import net.speedrun.app.ui.theme.caption
import net.speedrun.app.ui.theme.subhead

@Composable
fun StatsScreen() {
    val c = Speedrun.colors
    var coverage by remember { mutableStateOf<CoverageUi?>(null) }
    var calibration by remember { mutableStateOf<CalibrationUi?>(null) }
    var perf by remember { mutableStateOf<PerformanceUi?>(null) }

    val scope = rememberCoroutineScope()
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        scope.launch {
            coverage = runCatching { EngineRepository.coverage() }.getOrNull()
            calibration = runCatching { EngineRepository.calibration() }.getOrNull()
            perf = runCatching { EngineRepository.performance() }.getOrNull()
        }
    }

    Column(
        Modifier.fillMaxSize().background(c.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        ScreenHeader("Progress")
        Spacer(Modifier.height(Space.l))

        SectionLabel("Coverage")
        CoverageCard(coverage)
        Spacer(Modifier.height(Space.xxl))

        SectionLabel("Calibration")
        CalibrationCard(calibration)
        Spacer(Modifier.height(Space.xxl))

        SectionLabel("Recall \u2192 performance")
        PerformanceCard(perf)
        Spacer(Modifier.height(Space.xxl))
    }
}

@Composable
private fun CoverageCard(cov: CoverageUi?) {
    val c = Speedrun.colors
    SpeedrunCard {
        if (cov == null) {
            Text("Loading\u2026", color = c.textSecondary, style = MaterialTheme.typography.body)
            return@SpeedrunCard
        }
        SignalBar("Outline covered", cov.coverage, pct(cov.coverage), c.readinessGood)
        Spacer(Modifier.height(Space.m))
        KeyValueRow("Topics", "${cov.topicsCovered} / ${cov.topicsTotal}")
        KeyValueRow("Weighted", pct(cov.weightedCoverage))
        Text(
            "Coverage gates the score: readiness abstains when high-weight topics are missing.",
            color = c.textSecondary,
            style = MaterialTheme.typography.caption,
            modifier = Modifier.padding(top = Space.s),
        )
    }
}

@Composable
private fun CalibrationCard(cal: CalibrationUi?) {
    val c = Speedrun.colors
    SpeedrunCard {
        if (cal == null) {
            Text("Loading\u2026", color = c.textSecondary, style = MaterialTheme.typography.body)
            return@SpeedrunCard
        }
        if (!cal.sufficient || cal.bins.isEmpty()) {
            Text("Not enough predictions yet", color = c.textPrimary, style = MaterialTheme.typography.subhead)
            Text(
                cal.note.ifBlank {
                    "A reliability curve appears once there are enough graded predictions to score honestly."
                },
                color = c.textSecondary,
                style = MaterialTheme.typography.body,
                modifier = Modifier.padding(top = Space.xs),
            )
            if (cal.n > 0) {
                Spacer(Modifier.height(Space.m))
                KeyValueRow("Predictions", cal.n.toString())
            }
            return@SpeedrunCard
        }

        ReliabilityChart(cal)
        Spacer(Modifier.height(Space.m))
        KeyValueRow("Predictions", cal.n.toString())
        KeyValueRow("Brier", "%.3f".format(cal.brier))
        KeyValueRow("Log loss", "%.3f".format(cal.logLoss))
        Text(
            "Dots on the diagonal mean predicted probability matched reality.",
            color = c.textSecondary,
            style = MaterialTheme.typography.caption,
            modifier = Modifier.padding(top = Space.s),
        )
    }
}

@Composable
private fun ReliabilityChart(cal: CalibrationUi) {
    val c = Speedrun.colors
    val maxCount = (cal.bins.maxOfOrNull { it.count } ?: 1).coerceAtLeast(1)
    Canvas(
        Modifier.fillMaxWidth().aspectRatio(1.4f)
            .padding(top = Space.s),
    ) {
        val w = size.width
        val h = size.height
        // Perfect-calibration diagonal (dashed).
        drawLine(
            color = c.separator,
            start = Offset(0f, h),
            end = Offset(w, 0f),
            strokeWidth = 2f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(12f, 10f)),
        )
        // Frame.
        drawLine(c.separator, Offset(0f, 0f), Offset(0f, h), 2f)
        drawLine(c.separator, Offset(0f, h), Offset(w, h), 2f)
        // Bin points: x = predicted, y = observed (inverted).
        cal.bins.forEach { b ->
            val x = b.meanPredicted.coerceIn(0f, 1f) * w
            val y = (1f - b.meanOutcome.coerceIn(0f, 1f)) * h
            val r = 4f + 8f * (b.count.toFloat() / maxCount)
            drawCircle(color = c.accent, radius = r, center = Offset(x, y))
        }
    }
}

@Composable
private fun PerformanceCard(perf: PerformanceUi?) {
    val c = Speedrun.colors
    SpeedrunCard {
        if (perf == null) {
            Text("Loading\u2026", color = c.textSecondary, style = MaterialTheme.typography.body)
            return@SpeedrunCard
        }
        SignalBar("Recall", perf.recallRate, pct(perf.recallRate), c.memory)
        Spacer(Modifier.height(Space.m))
        SignalBar(
            "Performance",
            perf.performanceRate,
            if (perf.sufficient) pct(perf.performanceRate) else "thin",
            c.performance,
            dimmed = !perf.sufficient,
        )
        Spacer(Modifier.height(Space.m))
        Text(
            if (perf.sufficient) {
                "The gap (${pct(perf.gap)}) is what memory alone hides: recall without applied performance."
            } else {
                perf.note.ifBlank { "Add held-out exam-style questions to measure applied performance." }
            },
            color = c.textSecondary,
            style = MaterialTheme.typography.caption,
        )
    }
}
