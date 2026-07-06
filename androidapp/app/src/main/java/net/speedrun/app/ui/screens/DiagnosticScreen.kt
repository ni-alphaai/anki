// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import net.speedrun.app.AppSettings
import net.speedrun.app.EngineRepository
import net.speedrun.app.Mcat
import net.speedrun.app.QuestionItemUi
import net.speedrun.app.Readiness
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.SecondaryButton
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SegmentedControl
import net.speedrun.app.ui.SessionTopBar
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.VoiceExplainSheet
import net.speedrun.app.ui.theme.Radius
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import net.speedrun.app.ui.theme.body
import net.speedrun.app.ui.theme.bodyLg
import net.speedrun.app.ui.theme.caption
import net.speedrun.app.ui.theme.subhead
import net.speedrun.app.ui.theme.title

private const val DIAGNOSTIC_SESSION = "onboarding-diagnostic"
private val diagConfidence = listOf("Low" to 0.35f, "Medium" to 0.6f, "High" to 0.85f)

private data class SectionResult(val short: String, val correct: Int, val total: Int)

private sealed interface DiagPhase {
    data object Intro : DiagPhase
    data object Quiz : DiagPhase
    data class Report(val sections: List<SectionResult>, val correct: Int, val total: Int) :
        DiagPhase
}

private fun sectionShortForSubject(subject: String): String? =
    Mcat.SECTIONS.firstOrNull { subject in it.subjects }?.short

/**
 * First-run placement diagnostic: a short mixed set of exam-style questions that
 * seeds the performance + coverage signals, then an honest per-section read.
 * Reuses the practice bank; readiness stays provisional until the give-up gate.
 */
@Composable
fun DiagnosticScreen(onDone: () -> Unit) {
    val context = LocalContext.current
    var phase by remember { mutableStateOf<DiagPhase>(DiagPhase.Intro) }

    fun finish() {
        AppSettings.setDiagnosticDone(context, true)
    }

    when (val p = phase) {
        DiagPhase.Intro -> DiagIntro(
            onStart = { phase = DiagPhase.Quiz },
            onSkip = { finish(); onDone() },
        )
        DiagPhase.Quiz -> DiagQuiz(
            onClose = { finish(); onDone() },
            onFinish = { sections, correct, total ->
                finish()
                phase = DiagPhase.Report(sections, correct, total)
            },
        )
        is DiagPhase.Report -> DiagReport(p, onDone = onDone)
    }
}

@Composable
private fun DiagIntro(onStart: () -> Unit, onSkip: () -> Unit) {
    val c = Speedrun.colors
    Column(Modifier.fillMaxSize().background(c.background)) {
        SessionTopBar(onClose = onSkip)
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState())
                .padding(horizontal = Space.l),
        ) {
            Spacer(Modifier.height(Space.s))
            Text("Quick placement check", color = c.textPrimary, style = MaterialTheme.typography.title)
            Text(
                "A short, mixed set of exam-style questions across the MCAT sections. " +
                    "It seeds your performance and coverage signals so your dashboard " +
                    "starts from real evidence, not a blank slate.",
                color = c.textSecondary,
                style = MaterialTheme.typography.body,
                modifier = Modifier.padding(top = Space.xs, bottom = Space.l),
            )
            SpeedrunCard {
                SectionLabel("Placement quiz")
                Text(
                    "Your answers are honest inputs to the three signals. Readiness " +
                        "stays provisional until it has enough evidence — this is a " +
                        "starting read, not a final score.",
                    color = c.textSecondary,
                    style = MaterialTheme.typography.body,
                    modifier = Modifier.padding(top = Space.xs),
                )
            }
            Spacer(Modifier.height(Space.l))
            PrimaryButton("Start placement quiz", onClick = onStart)
            Spacer(Modifier.height(Space.s))
            SecondaryButton("Skip for now", onClick = onSkip)
        }
    }
}

@Composable
private fun DiagQuiz(
    onClose: () -> Unit,
    onFinish: (List<SectionResult>, Int, Int) -> Unit,
) {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()

    var questions by remember { mutableStateOf<List<QuestionItemUi>?>(null) }
    var index by remember { mutableIntStateOf(0) }
    var selected by remember { mutableStateOf<Int?>(null) }
    var answered by remember { mutableStateOf(false) }
    var confidence by remember { mutableStateOf<Float?>(null) }
    var pendingExplanation by remember { mutableStateOf("") }
    var showVoice by remember { mutableStateOf(false) }
    var shownAt by remember { mutableStateOf(0L) }
    // Per-section tallies keyed by section short label.
    val stats = remember { mutableMapOf<String, IntArray>() }

    LaunchedEffect(Unit) {
        questions = runCatching { EngineRepository.diagnosticQuestions() }.getOrDefault(emptyList())
        shownAt = System.currentTimeMillis()
    }

    fun buildResults(): Triple<List<SectionResult>, Int, Int> {
        val sections = Mcat.SECTIONS.mapNotNull { sec ->
            stats[sec.short]?.let { SectionResult(sec.short, it[0], it[1]) }
        }
        val correct = sections.sumOf { it.correct }
        val total = sections.sumOf { it.total }
        return Triple(sections, correct, total)
    }

    val list = questions
    Column(Modifier.fillMaxSize().background(c.background)) {
        SessionTopBar(
            onClose = onClose,
            counter = list?.takeIf { it.isNotEmpty() && index < it.size }
                ?.let { "Question ${index + 1} of ${it.size}" },
        )
        if (list != null && list.isNotEmpty() && index < list.size) {
            LinearProgressIndicator(
                progress = { (index.toFloat() / list.size).coerceIn(0f, 1f) },
                modifier = Modifier.fillMaxWidth().height(3.dp),
                color = c.accent,
                trackColor = c.separator,
            )
        }
        when {
            list == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = c.accent)
            }
            list.isEmpty() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(Modifier.padding(Space.xxl), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("No questions available yet", color = c.textPrimary, style = MaterialTheme.typography.subhead)
                    Spacer(Modifier.height(Space.l))
                    SecondaryButton("Skip", onClick = onClose)
                }
            }
            index >= list.size -> {
                val (sections, correct, total) = buildResults()
                LaunchedEffect(Unit) { onFinish(sections, correct, total) }
            }
            else -> {
                val q = list[index]
                Column(
                    Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())
                        .padding(horizontal = Space.l),
                ) {
                    Spacer(Modifier.height(Space.m))
                    SectionLabel(q.topic.replace('_', ' '))
                    SpeedrunCard {
                        Text(q.stem, color = c.textPrimary, style = MaterialTheme.typography.subhead)
                    }
                    Spacer(Modifier.height(Space.l))
                    q.options.forEachIndexed { i, opt ->
                        DiagOption(
                            label = opt,
                            letter = ('A' + i),
                            selected = selected == i,
                            answered = answered,
                            isCorrect = i == q.correctIndex,
                        ) { if (!answered) selected = i }
                        Spacer(Modifier.height(Space.s))
                    }
                    if (answered) {
                        Spacer(Modifier.height(Space.s))
                        SpeedrunCard {
                            Text(
                                "Answer: ${('A' + q.correctIndex)}. ${q.options.getOrNull(q.correctIndex).orEmpty()}",
                                color = c.good,
                                style = MaterialTheme.typography.body,
                                fontWeight = FontWeight.SemiBold,
                            )
                            if (q.explanation.isNotBlank()) {
                                Text(
                                    q.explanation,
                                    color = c.textSecondary,
                                    style = MaterialTheme.typography.body,
                                    modifier = Modifier.padding(top = Space.xs),
                                )
                            }
                        }
                    }
                    Spacer(Modifier.height(Space.l))
                }
                Column(Modifier.padding(horizontal = Space.l).padding(bottom = Space.l)) {
                    if (!answered) {
                        Text("How confident?", color = c.textSecondary, style = MaterialTheme.typography.caption, modifier = Modifier.padding(bottom = Space.xs))
                        SegmentedControl(
                            options = diagConfidence.map { it.first },
                            selectedIndex = diagConfidence.indexOfFirst { it.second == confidence },
                            onSelect = { i -> confidence = diagConfidence[i].second },
                        )
                        Spacer(Modifier.height(Space.s))
                        SelfExplainRow(pendingExplanation.isNotBlank()) { showVoice = true }
                        Spacer(Modifier.height(Space.s))
                        PrimaryButton("Submit answer", enabled = selected != null) {
                            val sel = selected ?: return@PrimaryButton
                            answered = true
                            val correct = sel == q.correctIndex
                            val short = sectionShortForSubject(q.topic) ?: "Other"
                            val stat = stats.getOrPut(short) { IntArray(2) }
                            stat[1] += 1
                            if (correct) stat[0] += 1
                            val took = System.currentTimeMillis() - shownAt
                            val conf = confidence
                            val expl = pendingExplanation
                            scope.launch {
                                runCatching {
                                    EngineRepository.recordQuestionAttempt(
                                        q, sel, took, conf, expl, session = DIAGNOSTIC_SESSION,
                                    )
                                }
                            }
                        }
                    } else {
                        PrimaryButton(if (index + 1 >= list.size) "Finish" else "Next question") {
                            index += 1
                            selected = null
                            answered = false
                            confidence = null
                            pendingExplanation = ""
                            shownAt = System.currentTimeMillis()
                        }
                    }
                }
            }
        }
    }

    if (showVoice) {
        VoiceExplainSheet(
            initial = pendingExplanation,
            onDismiss = { showVoice = false },
            onCapture = { pendingExplanation = it; showVoice = false },
        )
    }
}

@Composable
private fun SelfExplainRow(captured: Boolean, onClick: () -> Unit) {
    val c = Speedrun.colors
    Row(
        Modifier.fillMaxWidth()
            .clip(RoundedCornerShape(Radius.control))
            .background(c.accent.copy(alpha = 0.12f))
            .clickable { onClick() }
            .padding(vertical = 13.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            if (captured) Icons.Filled.Check else Icons.Filled.Mic,
            contentDescription = null,
            tint = c.accent,
        )
        Spacer(Modifier.width(Space.s))
        Text(
            if (captured) "Reasoning captured — edit" else "Self-explain before answering (optional)",
            color = c.accent,
            style = MaterialTheme.typography.body,
            fontWeight = FontWeight.SemiBold,
        )
    }
}

@Composable
private fun DiagOption(
    label: String,
    letter: Char,
    selected: Boolean,
    answered: Boolean,
    isCorrect: Boolean,
    onClick: () -> Unit,
) {
    val c = Speedrun.colors
    val (bg, border) = when {
        answered && isCorrect -> c.good.copy(alpha = 0.15f) to c.good
        answered && selected && !isCorrect -> c.again.copy(alpha = 0.15f) to c.again
        !answered && selected -> c.accent.copy(alpha = 0.12f) to c.accent
        else -> c.surface to c.separator
    }
    Row(
        Modifier.fillMaxWidth()
            .clip(RoundedCornerShape(Radius.control))
            .background(bg)
            .border(1.dp, border, RoundedCornerShape(Radius.control))
            .clickable(enabled = !answered) { onClick() }
            .padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            "$letter",
            color = c.textSecondary,
            style = MaterialTheme.typography.body,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(end = Space.m),
        )
        Text(label, color = c.textPrimary, style = MaterialTheme.typography.bodyLg, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun DiagReport(report: DiagPhase.Report, onDone: () -> Unit) {
    val c = Speedrun.colors
    var readiness by remember { mutableStateOf<Readiness?>(null) }
    LaunchedEffect(Unit) {
        readiness = runCatching { EngineRepository.readiness() }.getOrNull()
    }
    val pct = if (report.total > 0) report.correct * 100 / report.total else 0
    Column(Modifier.fillMaxSize().background(c.background)) {
        SessionTopBar(onClose = onDone)
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState())
                .padding(horizontal = Space.l),
        ) {
            Spacer(Modifier.height(Space.s))
            Text("Your placement read", color = c.textPrimary, style = MaterialTheme.typography.title)
            Text(
                "A first look at where you stand by section. This seeds performance and " +
                    "coverage; memory builds as you review, and readiness stays provisional " +
                    "until it has enough evidence.",
                color = c.textSecondary,
                style = MaterialTheme.typography.body,
                modifier = Modifier.padding(top = Space.xs, bottom = Space.l),
            )
            SpeedrunCard {
                SectionLabel("Overall")
                Text(
                    "${report.correct} / ${report.total} correct ($pct%)",
                    color = c.textPrimary,
                    style = MaterialTheme.typography.subhead,
                )
            }
            Spacer(Modifier.height(Space.m))
            SpeedrunCard {
                SectionLabel("By section")
                report.sections.forEach { s ->
                    val secPct = if (s.total > 0) s.correct * 100 / s.total else 0
                    Row(Modifier.fillMaxWidth().padding(top = Space.s)) {
                        Text(s.short, color = c.textPrimary, style = MaterialTheme.typography.body, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                        Text("${s.correct}/${s.total} · $secPct%", color = c.textSecondary, style = MaterialTheme.typography.body)
                    }
                    Box(
                        Modifier.fillMaxWidth().height(8.dp).padding(top = 4.dp)
                            .clip(RoundedCornerShape(Radius.control)).background(c.separator),
                    ) {
                        Box(
                            Modifier.fillMaxWidth(secPct / 100f).height(8.dp)
                                .clip(RoundedCornerShape(Radius.control)).background(c.performance),
                        )
                    }
                }
            }
            Spacer(Modifier.height(Space.m))
            SpeedrunCard {
                SectionLabel("Readiness")
                val r = readiness
                if (r != null && r.sufficient) {
                    Text("Initial readiness: ${r.readinessScaled}", color = c.textPrimary, style = MaterialTheme.typography.subhead)
                    Text("Likely range ${r.low}–${r.high} on the MCAT scale.", color = c.textSecondary, style = MaterialTheme.typography.body, modifier = Modifier.padding(top = Space.xs))
                } else {
                    Text("Building evidence", color = c.textPrimary, style = MaterialTheme.typography.subhead)
                    Text(
                        (r?.reason?.takeIf { it.isNotBlank() } ?: "Not enough evidence yet.") +
                            " Keep reviewing and practicing and a scored readiness will appear.",
                        color = c.textSecondary,
                        style = MaterialTheme.typography.body,
                        modifier = Modifier.padding(top = Space.xs),
                    )
                }
            }
            Spacer(Modifier.height(Space.l))
            PrimaryButton("Go to dashboard", onClick = onDone)
            Spacer(Modifier.height(Space.xxl))
        }
    }
}
