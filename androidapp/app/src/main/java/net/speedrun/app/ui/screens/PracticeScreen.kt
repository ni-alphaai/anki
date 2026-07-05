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
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import net.speedrun.app.Diagnosis
import net.speedrun.app.EngineRepository
import net.speedrun.app.Feedback
import net.speedrun.app.Mcat
import net.speedrun.app.PracticeBankSummaryUi
import net.speedrun.app.QuestionItemUi
import net.speedrun.app.ui.CompletionState
import net.speedrun.app.ui.DiagnosisView
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
import net.speedrun.app.ui.theme.heading
import net.speedrun.app.ui.theme.label
import net.speedrun.app.ui.theme.subhead
import net.speedrun.app.ui.theme.title

private val confidenceLevels = listOf("Low" to 0.35f, "Medium" to 0.6f, "High" to 0.85f)

/** The Practice screen's internal navigation: section landing -> drill-down -> runner. */
private sealed interface PracticeView {
    data object Landing : PracticeView
    data class Section(val key: String) : PracticeView
    data class Runner(val topics: List<String>) : PracticeView
}

@Composable
fun PracticeScreen(
    onDone: () -> Unit,
    // When provided (e.g. the end-of-session reasoning round), skip the section
    // landing and run these questions directly.
    loader: (suspend () -> List<QuestionItemUi>)? = null,
) {
    if (loader != null) {
        PracticeRunner(onClose = onDone, onFinish = onDone, loader = loader)
        return
    }
    // When opened from a topic ("Practice this topic"), skip the section landing
    // and run that topic's subjects directly instead of the mixed diagnostic.
    var view by remember {
        val subjects = net.speedrun.app.ui.Selection.practiceSubjects
        net.speedrun.app.ui.Selection.practiceSubjects = emptyList() // consume once
        mutableStateOf<PracticeView>(
            if (subjects.isNotEmpty()) PracticeView.Runner(subjects) else PracticeView.Landing,
        )
    }
    when (val v = view) {
        PracticeView.Landing -> PracticeLanding(
            onClose = onDone,
            onMixed = { view = PracticeView.Runner(emptyList()) },
            onSection = { key -> view = PracticeView.Section(key) },
        )
        is PracticeView.Section -> PracticeSection(
            sectionKey = v.key,
            onClose = onDone,
            onBack = { view = PracticeView.Landing },
            onStart = { topics -> view = PracticeView.Runner(topics) },
        )
        is PracticeView.Runner -> PracticeRunner(
            onClose = onDone,
            onFinish = { view = PracticeView.Landing },
            loader = { EngineRepository.practiceQuestionsForTopics(v.topics) },
        )
    }
}

// --- Section landing --------------------------------------------------------

@Composable
private fun PracticeLanding(
    onClose: () -> Unit,
    onMixed: () -> Unit,
    onSection: (String) -> Unit,
) {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var summary by remember { mutableStateOf<PracticeBankSummaryUi?>(null) }
    var importing by remember { mutableStateOf(false) }

    suspend fun reload() {
        summary = runCatching { EngineRepository.practiceBankSummary() }.getOrNull()
    }
    LaunchedEffect(Unit) { reload() }

    val onAddMmlu = {
        if (!importing) {
            importing = true
            scope.launch {
                runCatching { EngineRepository.importMmluAsset(context) }
                reload()
                importing = false
            }
        }
        Unit
    }

    Column(Modifier.fillMaxSize().background(c.background)) {
        SessionTopBar(onClose = onClose)
        val s = summary
        when {
            s == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = c.accent)
            }
            s.total == 0 -> EmptyPractice(importing, onAddMmlu, onClose)
            else -> Column(
                Modifier.fillMaxWidth().verticalScroll(rememberScrollState())
                    .padding(horizontal = Space.l),
            ) {
                Spacer(Modifier.height(Space.s))
                Text("Practice", color = c.textPrimary, style = MaterialTheme.typography.title)
                Text(
                    "Held-out, exam-style questions by MCAT section — these feed your performance signal and calibration.",
                    color = c.textSecondary,
                    style = MaterialTheme.typography.body,
                    modifier = Modifier.padding(top = Space.xs, bottom = Space.l),
                )
                // Mixed diagnostic quick-start.
                SpeedrunCard(Modifier.clickable { onMixed() }) {
                    Text("QUICK START", color = c.accent, style = MaterialTheme.typography.label)
                    Text("Mixed diagnostic", color = c.textPrimary, style = MaterialTheme.typography.subhead, modifier = Modifier.padding(top = Space.xs))
                    Text(
                        "A spread of up to 20 questions across every section — good for a baseline.",
                        color = c.textSecondary, style = MaterialTheme.typography.body,
                        modifier = Modifier.padding(top = Space.xs),
                    )
                }
                Spacer(Modifier.height(Space.l))
                Mcat.SECTIONS.forEach { section ->
                    SectionCard(section, s.sectionCount(section)) { onSection(section.key) }
                    Spacer(Modifier.height(Space.m))
                }
                Spacer(Modifier.height(Space.xxl))
            }
        }
    }
}

@Composable
private fun SectionCard(section: Mcat.Section, count: Int, onClick: () -> Unit) {
    val c = Speedrun.colors
    val hasBank = section.subjects.isNotEmpty()
    val enabled = hasBank && count > 0
    SpeedrunCard(Modifier.clickable(enabled = enabled) { onClick() }) {
        Text(section.short, color = c.textPrimary, style = MaterialTheme.typography.subhead)
        Text(
            section.full,
            color = c.textSecondary,
            style = MaterialTheme.typography.caption,
            modifier = Modifier.padding(top = 2.dp),
        )
        val caption = when {
            !hasBank -> "Passage practice — from reading"
            count == 0 -> "No questions — add a pack from Library"
            else -> "$count question${if (count != 1) "s" else ""}"
        }
        Text(
            caption,
            color = if (enabled) c.accent else c.textTertiary,
            style = MaterialTheme.typography.body,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(top = Space.s),
        )
    }
}

// --- Section drill-down -----------------------------------------------------

@Composable
private fun PracticeSection(
    sectionKey: String,
    onClose: () -> Unit,
    onBack: () -> Unit,
    onStart: (List<String>) -> Unit,
) {
    val c = Speedrun.colors
    val section = Mcat.sectionByKey(sectionKey)
    var summary by remember { mutableStateOf<PracticeBankSummaryUi?>(null) }
    LaunchedEffect(sectionKey) {
        summary = runCatching { EngineRepository.practiceBankSummary() }.getOrNull()
    }
    if (section == null) {
        onBack()
        return
    }
    val counts = summary?.byTopic ?: emptyMap()
    val sectionCount = section.subjects.sumOf { counts[it] ?: 0 }

    Column(Modifier.fillMaxSize().background(c.background)) {
        SessionTopBar(onClose = onClose)
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState())
                .padding(horizontal = Space.l),
        ) {
            Text(
                "← All sections",
                color = c.accent,
                style = MaterialTheme.typography.body,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clickable { onBack() }.padding(vertical = Space.s),
            )
            Text(section.short, color = c.textPrimary, style = MaterialTheme.typography.title)
            Text(
                section.full,
                color = c.textSecondary,
                style = MaterialTheme.typography.body,
                modifier = Modifier.padding(top = Space.xs, bottom = Space.l),
            )
            if (section.subjects.size > 1 && sectionCount > 0) {
                StartRow(
                    "Practice whole section",
                    "$sectionCount questions across ${section.subjects.size} subjects",
                    enabled = true,
                ) { onStart(section.subjects.filter { (counts[it] ?: 0) > 0 }) }
                Spacer(Modifier.height(Space.m))
            }
            section.subjects.forEach { subject ->
                val n = counts[subject] ?: 0
                StartRow(
                    Mcat.subjectLabel(subject),
                    if (n > 0) "$n question${if (n != 1) "s" else ""}" else "No questions yet",
                    enabled = n > 0,
                ) { onStart(listOf(subject)) }
                Spacer(Modifier.height(Space.m))
            }
            Spacer(Modifier.height(Space.xxl))
        }
    }
}

@Composable
private fun StartRow(title: String, subtitle: String, enabled: Boolean, onClick: () -> Unit) {
    val c = Speedrun.colors
    SpeedrunCard(Modifier.clickable(enabled = enabled) { onClick() }) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(title, color = c.textPrimary, style = MaterialTheme.typography.subhead)
                Text(
                    subtitle,
                    color = c.textSecondary,
                    style = MaterialTheme.typography.caption,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
            Text(
                if (enabled) "Start" else "—",
                color = if (enabled) c.accent else c.textTertiary,
                style = MaterialTheme.typography.body,
                fontWeight = FontWeight.SemiBold,
            )
        }
    }
}

// --- Question runner --------------------------------------------------------

@Composable
private fun PracticeRunner(
    onClose: () -> Unit,
    onFinish: () -> Unit,
    loader: suspend () -> List<QuestionItemUi>,
) {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    val haptic = LocalHapticFeedback.current

    var questions by remember { mutableStateOf<List<QuestionItemUi>?>(null) }
    var index by remember { mutableIntStateOf(0) }
    var selected by remember { mutableStateOf<Int?>(null) }
    var answered by remember { mutableStateOf(false) }
    var confidence by remember { mutableStateOf<Float?>(null) }
    var pendingExplanation by remember { mutableStateOf("") }
    var showVoice by remember { mutableStateOf(false) }
    var diagnosis by remember { mutableStateOf<Diagnosis?>(null) }
    var correctCount by remember { mutableIntStateOf(0) }
    var shownAt by remember { mutableStateOf(0L) }
    // D7: decide once whether to withhold immediate correctness (experiment on
    // AND the student already proficient). Mirrors the desktop dialog; the
    // attempt is still recorded, only the reveal is deferred.
    var withhold by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        questions = runCatching { loader() }.getOrDefault(emptyList())
        if (net.speedrun.app.AppSettings.delayedFeedbackExperiment) {
            val perf = runCatching { EngineRepository.performance().performanceRate }
                .getOrDefault(0f)
            withhold = Feedback.shouldWithhold(perf, enabled = true)
        }
        shownAt = System.currentTimeMillis()
    }

    val list = questions

    Column(Modifier.fillMaxSize().background(c.background)) {
        SessionTopBar(
            onClose = onClose,
            counter = list?.takeIf { it.isNotEmpty() && index < it.size }
                ?.let { "Question ${index + 1} of ${it.size}" },
        )
        if (list != null && list.isNotEmpty()) {
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
                Column(
                    Modifier.padding(Space.xxl),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text("No questions here yet", color = c.textPrimary, style = MaterialTheme.typography.heading)
                    Spacer(Modifier.height(Space.l))
                    SecondaryButton("Back", onClick = onFinish)
                }
            }
            index >= list.size -> {
                if (withhold) {
                    // D7: defer the score to the delayed surface instead of
                    // revealing it here.
                    CompletionState(
                        headline = "${list.size} banked",
                        headlineColor = c.accent,
                        title = "Answers banked",
                        message = "Delayed feedback is on. Your results feed your performance signal — see how you did on the Progress tab.",
                        primaryLabel = "Done",
                        onPrimary = onFinish,
                    )
                } else {
                    val pctVal = if (list.isEmpty()) 0 else (correctCount * 100 / list.size)
                    CompletionState(
                        headline = "$correctCount / ${list.size}",
                        headlineColor = c.performance,
                        title = "Practice complete ($pctVal%)",
                        message = "These answers now feed your performance signal and calibration on the Progress tab.",
                        primaryLabel = "Done",
                        onPrimary = onFinish,
                    )
                }
            }
            else -> {
                val q = list[index]
                Column(
                    Modifier.weight(1f).fillMaxWidth()
                        .verticalScroll(rememberScrollState())
                        .padding(horizontal = Space.l),
                ) {
                    Spacer(Modifier.height(Space.m))
                    SectionLabel(q.topic.replace('_', ' '))
                    SpeedrunCard {
                        Text(q.stem, color = c.textPrimary, style = MaterialTheme.typography.subhead)
                    }
                    Spacer(Modifier.height(Space.l))
                    q.options.forEachIndexed { i, opt ->
                        OptionRow(
                            label = opt,
                            letter = ('A' + i),
                            selected = selected == i,
                            answered = answered,
                            isCorrect = i == q.correctIndex,
                            // D7: when withholding, don't reveal which option was
                            // correct - just show the selection was recorded.
                            reveal = !withhold,
                        ) { if (!answered) selected = i }
                        Spacer(Modifier.height(Space.s))
                    }
                    if (answered) {
                        Spacer(Modifier.height(Space.s))
                        if (withhold) {
                            BankedFeedback()
                        } else {
                            AnswerFeedback(q)
                            diagnosis?.let {
                                Spacer(Modifier.height(Space.s))
                                DiagnosisView(it)
                            }
                        }
                    }
                    Spacer(Modifier.height(Space.l))
                }

                Column(Modifier.padding(horizontal = Space.l).padding(bottom = Space.l)) {
                    if (!answered) {
                        ConfidencePicker(confidence) { confidence = it }
                        Spacer(Modifier.height(Space.s))
                        SelfExplainRow(pendingExplanation.isNotBlank()) { showVoice = true }
                        Spacer(Modifier.height(Space.s))
                        PrimaryButton("Submit answer", enabled = selected != null) {
                            val sel = selected ?: return@PrimaryButton
                            haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                            answered = true
                            if (sel == q.correctIndex) correctCount++
                            val took = System.currentTimeMillis() - shownAt
                            val expl = pendingExplanation
                            val conf = confidence
                            scope.launch {
                                diagnosis = runCatching {
                                    EngineRepository.recordQuestionAttempt(q, sel, took, conf, expl)
                                }.getOrNull()?.takeIf { it.label != null }
                            }
                        }
                    } else {
                        PrimaryButton(if (index + 1 >= list.size) "Finish" else "Next question") {
                            index += 1
                            selected = null
                            answered = false
                            confidence = null
                            pendingExplanation = ""
                            diagnosis = null
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
private fun OptionRow(
    label: String,
    letter: Char,
    selected: Boolean,
    answered: Boolean,
    isCorrect: Boolean,
    // When false (D7 withhold), the answered state does not reveal correctness -
    // it only shows which option the student picked.
    reveal: Boolean = true,
    onClick: () -> Unit,
) {
    val c = Speedrun.colors
    val (bg, border) = when {
        answered && reveal && isCorrect -> c.good.copy(alpha = 0.15f) to c.good
        answered && reveal && selected && !isCorrect -> c.again.copy(alpha = 0.15f) to c.again
        answered && !reveal && selected -> c.accent.copy(alpha = 0.12f) to c.accent
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
        if (answered && reveal && isCorrect) {
            Icon(Icons.Filled.Check, contentDescription = "Correct", tint = c.good)
        }
    }
}

@Composable
private fun BankedFeedback() {
    val c = Speedrun.colors
    SpeedrunCard {
        Text(
            "Answer banked",
            color = c.accent,
            style = MaterialTheme.typography.body,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            "Delayed feedback is on. You'll find out whether you were right on the Progress tab, which nudges you to re-derive it.",
            color = c.textSecondary,
            style = MaterialTheme.typography.body,
            modifier = Modifier.padding(top = Space.xs),
        )
    }
}

@Composable
private fun AnswerFeedback(q: QuestionItemUi) {
    val c = Speedrun.colors
    SpeedrunCard {
        val correctText = q.options.getOrNull(q.correctIndex).orEmpty()
        Text(
            "Answer: ${('A' + q.correctIndex)}. $correctText",
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

@Composable
private fun ConfidencePicker(confidence: Float?, onSelect: (Float) -> Unit) {
    val c = Speedrun.colors
    Column {
        Text("How confident?", color = c.textSecondary, style = MaterialTheme.typography.caption, modifier = Modifier.padding(bottom = Space.xs))
        SegmentedControl(
            options = confidenceLevels.map { it.first },
            selectedIndex = confidenceLevels.indexOfFirst { it.second == confidence },
            onSelect = { i -> onSelect(confidenceLevels[i].second) },
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
private fun EmptyPractice(importing: Boolean, onAddMmlu: () -> Unit, onDone: () -> Unit) {
    val c = Speedrun.colors
    Column(
        Modifier.fillMaxSize().padding(Space.xxl),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("No practice questions yet", color = c.textPrimary, style = MaterialTheme.typography.heading)
        Text(
            "Add the open-licensed MMLU pack (2,231 college science + medicine MCQs) to start building your performance signal.",
            color = c.textSecondary,
            style = MaterialTheme.typography.body,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = Space.s, bottom = Space.xl),
        )
        PrimaryButton(
            text = if (importing) "Adding questions…" else "Add MMLU pack",
            enabled = !importing,
            onClick = onAddMmlu,
        )
        Spacer(Modifier.height(Space.s))
        SecondaryButton("Not now", onClick = onDone)
    }
}
