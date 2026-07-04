// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import kotlinx.coroutines.launch
import net.speedrun.app.CalibrationUi
import net.speedrun.app.CoverageUi
import net.speedrun.app.EngineRepository
import net.speedrun.app.FeedbackReportUi
import net.speedrun.app.PerformanceUi
import net.speedrun.app.Readiness
import net.speedrun.app.ui.KeyValueRow
import net.speedrun.app.ui.ScreenHeader
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SignalBar
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.pct
import net.speedrun.app.ui.theme.Radius
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import net.speedrun.app.ui.theme.body
import net.speedrun.app.ui.theme.caption
import net.speedrun.app.ui.theme.readout
import net.speedrun.app.ui.theme.subhead

@Composable
fun StatsScreen() {
    val c = Speedrun.colors
    var readiness by remember { mutableStateOf<Readiness?>(null) }
    var coverage by remember { mutableStateOf<CoverageUi?>(null) }
    var calibration by remember { mutableStateOf<CalibrationUi?>(null) }
    var perf by remember { mutableStateOf<PerformanceUi?>(null) }
    var feedback by remember { mutableStateOf<FeedbackReportUi?>(null) }

    val scope = rememberCoroutineScope()
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        scope.launch {
            readiness = runCatching { EngineRepository.readiness() }.getOrNull()
            coverage = runCatching { EngineRepository.coverage() }.getOrNull()
            calibration = runCatching { EngineRepository.calibration() }.getOrNull()
            perf = runCatching { EngineRepository.performance() }.getOrNull()
            feedback = runCatching { EngineRepository.feedbackReport() }.getOrNull()
        }
    }

    Column(
        Modifier.fillMaxSize().background(c.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        ScreenHeader("Progress")
        Text(
            "The three signals in depth: how well-calibrated your recall is, where your misses come from, and what the deck covers.",
            color = c.textSecondary,
            style = MaterialTheme.typography.body,
            modifier = Modifier.padding(top = Space.xs, bottom = Space.l),
        )

        SignalTiles(readiness)
        Spacer(Modifier.height(Space.xl))

        SectionLabel("Calibration")
        CalibrationCard(calibration)
        Spacer(Modifier.height(Space.xl))

        SectionLabel("Misses by cause")
        MissesCard(feedback)
        Spacer(Modifier.height(Space.xl))

        SectionLabel("Coverage map")
        CoverageMapCard(coverage, feedback)
        Spacer(Modifier.height(Space.xl))

        SectionLabel("Recall → performance")
        PerformanceCard(perf)
        Spacer(Modifier.height(Space.xxl))
    }
}

@Composable
private fun SignalTiles(r: Readiness?) {
    val c = Speedrun.colors
    Row(horizontalArrangement = Arrangement.spacedBy(Space.m)) {
        SignalTile("Memory", r?.memory, r?.memorySufficient ?: true, c.memory, Modifier.weight(1f))
        SignalTile("Performance", r?.performance, r?.performanceSufficient ?: true, c.performance, Modifier.weight(1f))
        SignalTile("Coverage", r?.coverage, true, c.coverageTrack, Modifier.weight(1f))
    }
}

@Composable
private fun SignalTile(label: String, value: Float?, ok: Boolean, color: Color, modifier: Modifier) {
    val c = Speedrun.colors
    // A compact custom cell (tighter padding than SpeedrunCard) so a long label
    // like "Performance" fits on one line in the narrow three-up row.
    Column(
        modifier
            .clip(RoundedCornerShape(Radius.card))
            .background(c.surfaceElevated)
            .border(0.5.dp, c.separator, RoundedCornerShape(Radius.card))
            .padding(Space.m),
    ) {
        val shown = when {
            value == null -> "—"
            !ok -> "thin"
            else -> pct(value)
        }
        Text(shown, color = c.textPrimary, style = MaterialTheme.typography.subhead, fontSize = 24.sp, fontWeight = FontWeight.Bold, maxLines = 1)
        Text(label, color = c.textSecondary, style = MaterialTheme.typography.caption, fontSize = 11.sp, maxLines = 1, softWrap = false, overflow = TextOverflow.Visible, modifier = Modifier.padding(top = 3.dp, bottom = Space.s))
        Box(Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(Radius.control)).background(c.separator)) {
            Box(
                Modifier.fillMaxWidth((value ?: 0f).coerceIn(0f, 1f)).height(6.dp)
                    .clip(RoundedCornerShape(Radius.control))
                    .background(if (ok) color else c.textTertiary),
            )
        }
    }
}

@Composable
private fun MissesCard(fb: FeedbackReportUi?) {
    val c = Speedrun.colors
    SpeedrunCard {
        if (fb == null || fb.total == 0) {
            Text("No exam-style attempts yet", color = c.textPrimary, style = MaterialTheme.typography.subhead)
            Text(
                "Answer practice questions to see where your misses come from.",
                color = c.textSecondary, style = MaterialTheme.typography.body,
                modifier = Modifier.padding(top = Space.xs),
            )
            return@SpeedrunCard
        }
        val kinds = listOf(
            Triple("Memory", fb.memoryMisses, c.memory),
            Triple("Reasoning", fb.reasoningMisses, c.reasoning),
            Triple("Passage", fb.passageMisses, c.passage),
            Triple("Test-taking", fb.testTakingMisses, c.readinessWarn),
        )
        val scale = (kinds.maxOf { it.second }).coerceAtLeast(1)
        Row(verticalAlignment = Alignment.Bottom) {
            Text("${fb.correct}/${fb.total}", color = c.textPrimary, style = MaterialTheme.typography.readout, fontWeight = FontWeight.Bold)
            Text(
                "correct · ${kinds.sumOf { it.second }} misses",
                color = c.textSecondary, style = MaterialTheme.typography.body,
                modifier = Modifier.padding(start = Space.s, bottom = 4.dp),
            )
        }
        Spacer(Modifier.height(Space.s))
        kinds.forEach { (name, count, color) ->
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 5.dp)) {
                Text(name, color = c.textSecondary, style = MaterialTheme.typography.caption, fontSize = 12.sp, maxLines = 1, modifier = Modifier.width(104.dp))
                Box(Modifier.weight(1f).height(14.dp).clip(RoundedCornerShape(Radius.control)).background(c.separator)) {
                    Box(
                        Modifier.fillMaxWidth((count.toFloat() / scale).coerceIn(0f, 1f)).height(14.dp)
                            .clip(RoundedCornerShape(Radius.control)).background(color),
                    )
                }
                Text("$count", color = c.textPrimary, style = MaterialTheme.typography.body, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(start = Space.s).width(20.dp))
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CoverageMapCard(cov: CoverageUi?, fb: FeedbackReportUi?) {
    val c = Speedrun.colors
    SpeedrunCard {
        if (cov == null || cov.topics.isEmpty()) {
            Text("No topic outline yet", color = c.textPrimary, style = MaterialTheme.typography.subhead)
            Text(
                "Import the MCAT content library from Library to build the coverage map.",
                color = c.textSecondary, style = MaterialTheme.typography.body,
                modifier = Modifier.padding(top = Space.xs),
            )
            return@SpeedrunCard
        }
        Row(verticalAlignment = Alignment.Bottom) {
            Text("${cov.topicsCovered}/${cov.topicsTotal}", color = c.textPrimary, style = MaterialTheme.typography.readout, fontWeight = FontWeight.Bold)
            Text(
                "topics · ${pct(cov.weightedCoverage)} weighted",
                color = c.textSecondary, style = MaterialTheme.typography.body,
                modifier = Modifier.padding(start = Space.s, bottom = 4.dp),
            )
        }
        Spacer(Modifier.height(Space.m))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(5.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
            cov.topics.forEach { t ->
                Box(
                    Modifier.size(15.dp).clip(RoundedCornerShape(3.dp))
                        .background(if (t.covered) c.readinessGood else c.separator),
                )
            }
        }
        Spacer(Modifier.height(Space.m))
        Row(horizontalArrangement = Arrangement.spacedBy(Space.l)) {
            LegendDot("covered", c.readinessGood)
            LegendDot("not yet", c.separator)
        }
        val weak = fb?.weakTopics?.filter { it.isNotBlank() }?.take(8).orEmpty()
        if (weak.isNotEmpty()) {
            Spacer(Modifier.height(Space.l))
            Text("WEAKEST TOPICS", color = c.textSecondary, style = MaterialTheme.typography.caption, modifier = Modifier.padding(bottom = Space.s))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                weak.forEach { topic ->
                    Box(
                        Modifier.clip(RoundedCornerShape(Radius.pill)).background(c.surface)
                            .padding(horizontal = 11.dp, vertical = 5.dp),
                    ) {
                        Text(topic, color = c.textSecondary, style = MaterialTheme.typography.caption)
                    }
                }
            }
        }
    }
}

@Composable
private fun LegendDot(label: String, color: Color) {
    val c = Speedrun.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(11.dp).clip(RoundedCornerShape(3.dp)).background(color))
        Spacer(Modifier.width(Space.xs))
        Text(label, color = c.textSecondary, style = MaterialTheme.typography.caption)
    }
}

@Composable
private fun CalibrationCard(cal: CalibrationUi?) {
    val c = Speedrun.colors
    SpeedrunCard {
        if (cal == null) {
            Text("Loading…", color = c.textSecondary, style = MaterialTheme.typography.body)
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
            "Dots on the diagonal mean predicted probability matched reality; dot size = number of predictions.",
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
        drawLine(
            color = c.separator,
            start = Offset(0f, h),
            end = Offset(w, 0f),
            strokeWidth = 2f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(12f, 10f)),
        )
        drawLine(c.separator, Offset(0f, 0f), Offset(0f, h), 2f)
        drawLine(c.separator, Offset(0f, h), Offset(w, h), 2f)
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
            Text("Loading…", color = c.textSecondary, style = MaterialTheme.typography.body)
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
