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
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import net.speedrun.app.Diagnosis
import net.speedrun.app.EngineRepository
import net.speedrun.app.QuestionItemUi
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.SecondaryButton
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.VoiceExplainSheet
import net.speedrun.app.ui.theme.Radius
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun

private val confidenceLevels = listOf("Low" to 0.35f, "Medium" to 0.6f, "High" to 0.85f)

@Composable
fun PracticeScreen(onDone: () -> Unit) {
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
    var importing by remember { mutableStateOf(false) }
    val context = LocalContext.current

    suspend fun load() {
        questions = runCatching { EngineRepository.practiceQuestions(limit = 20) }.getOrDefault(emptyList())
        shownAt = System.currentTimeMillis()
    }
    LaunchedEffect(Unit) { load() }

    val onAddMmlu = {
        if (!importing) {
            importing = true
            scope.launch {
                runCatching { EngineRepository.importMmluAsset(context) }
                index = 0
                correctCount = 0
                questions = null
                load()
                importing = false
            }
        }
        Unit
    }

    val list = questions

    Column(Modifier.fillMaxSize().background(c.background)) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = Space.s, vertical = Space.xs),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onDone) {
                Icon(Icons.Filled.Close, contentDescription = "Close", tint = c.textSecondary)
            }
            Spacer(Modifier.weight(1f))
            if (list != null && list.isNotEmpty() && index < list.size) {
                Text(
                    "Question ${index + 1} of ${list.size}",
                    color = c.textSecondary,
                    fontSize = 13.sp,
                    modifier = Modifier.padding(end = Space.m),
                )
            }
        }
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
            list.isEmpty() -> EmptyPractice(importing, onAddMmlu, onDone)
            index >= list.size -> Summary(correctCount, list.size, onDone)
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
                        Text(q.stem, color = c.textPrimary, fontSize = 18.sp, fontWeight = FontWeight.Medium)
                    }
                    Spacer(Modifier.height(Space.l))
                    q.options.forEachIndexed { i, opt ->
                        OptionRow(
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
                        AnswerFeedback(q, diagnosis)
                    }
                    Spacer(Modifier.height(Space.l))
                }

                Column(Modifier.padding(horizontal = Space.l).padding(bottom = Space.l)) {
                    if (!answered) {
                        ConfidenceRow(confidence) { confidence = it }
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
            .clip(RoundedCornerShape(Radius.button))
            .background(bg)
            .border(1.dp, border, RoundedCornerShape(Radius.button))
            .clickable(enabled = !answered) { onClick() }
            .padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            "$letter",
            color = c.textSecondary,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(end = Space.m),
        )
        Text(label, color = c.textPrimary, fontSize = 16.sp, modifier = Modifier.weight(1f))
        if (answered && isCorrect) {
            Icon(Icons.Filled.Check, contentDescription = "Correct", tint = c.good)
        }
    }
}

@Composable
private fun AnswerFeedback(q: QuestionItemUi, diagnosis: Diagnosis?) {
    val c = Speedrun.colors
    SpeedrunCard {
        val correctText = q.options.getOrNull(q.correctIndex).orEmpty()
        Text(
            "Answer: ${('A' + q.correctIndex)}. $correctText",
            color = c.good,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
        )
        if (q.explanation.isNotBlank()) {
            Text(
                q.explanation,
                color = c.textSecondary,
                fontSize = 15.sp,
                modifier = Modifier.padding(top = Space.xs),
            )
        }
        diagnosis?.label?.let { label ->
            Spacer(Modifier.height(Space.s))
            Text(label, color = c.textPrimary, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
            if (diagnosis.action.isNotBlank()) {
                Text(diagnosis.action, color = c.textSecondary, fontSize = 13.sp)
            }
        }
    }
}

@Composable
private fun ConfidenceRow(confidence: Float?, onSelect: (Float) -> Unit) {
    val c = Speedrun.colors
    Column {
        Text("How confident?", color = c.textSecondary, fontSize = 13.sp, modifier = Modifier.padding(bottom = Space.xs))
        Row(horizontalArrangement = Arrangement.spacedBy(Space.s)) {
            confidenceLevels.forEach { (label, value) ->
                val on = confidence == value
                Box(
                    Modifier.weight(1f)
                        .clip(RoundedCornerShape(Radius.button))
                        .background(if (on) c.accent.copy(alpha = 0.15f) else c.surface)
                        .border(1.dp, if (on) c.accent else c.separator, RoundedCornerShape(Radius.button))
                        .clickable { onSelect(value) }
                        .padding(vertical = 10.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(label, color = if (on) c.accent else c.textSecondary, fontSize = 14.sp)
                }
            }
        }
    }
}

@Composable
private fun SelfExplainRow(captured: Boolean, onClick: () -> Unit) {
    val c = Speedrun.colors
    Row(
        Modifier.fillMaxWidth()
            .clip(RoundedCornerShape(Radius.button))
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
            if (captured) "Reasoning captured \u2014 edit" else "Self-explain before answering (optional)",
            color = c.accent,
            fontSize = 15.sp,
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
        Text("No practice questions yet", color = c.textPrimary, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
        Text(
            "Add the open-licensed MMLU pack (2,231 college science + medicine MCQs) to start building your performance signal.",
            color = c.textSecondary,
            fontSize = 15.sp,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = Space.s, bottom = Space.xl),
        )
        PrimaryButton(
            text = if (importing) "Adding questions\u2026" else "Add MMLU pack",
            enabled = !importing,
            onClick = onAddMmlu,
        )
        Spacer(Modifier.height(Space.s))
        SecondaryButton("Not now", onClick = onDone)
    }
}

@Composable
private fun Summary(correct: Int, total: Int, onDone: () -> Unit) {
    val c = Speedrun.colors
    val pct = if (total == 0) 0 else (correct * 100 / total)
    Column(
        Modifier.fillMaxSize().padding(Space.xxl),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("$correct / $total", color = c.performance, fontSize = 44.sp, fontWeight = FontWeight.Bold)
        Text("Practice complete ($pct%)", color = c.textPrimary, fontSize = 20.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = Space.s))
        Text(
            "These answers now feed your performance signal and calibration on the Progress tab.",
            color = c.textSecondary,
            fontSize = 15.sp,
            modifier = Modifier.padding(top = Space.xs, bottom = Space.xl),
        )
        PrimaryButton("Done", onClick = onDone)
    }
}
