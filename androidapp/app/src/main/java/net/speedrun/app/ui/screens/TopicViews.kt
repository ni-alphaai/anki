// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt
import net.speedrun.app.EngineRepository
import net.speedrun.app.Mcat
import net.speedrun.app.TopicDashboardUi
import net.speedrun.app.TopicSectionUi
import net.speedrun.app.TopicStatus
import net.speedrun.app.TopicUi
import net.speedrun.app.ui.Chip
import net.speedrun.app.ui.DetailTopBar
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.SecondaryButton
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import net.speedrun.app.ui.theme.SpeedrunColors
import net.speedrun.app.ui.theme.body
import net.speedrun.app.ui.theme.caption
import net.speedrun.app.ui.theme.readout
import net.speedrun.app.ui.theme.subhead

private fun pctOrDash(v: Float?): String = if (v != null) "${(v * 100).roundToInt()}%" else "–"

private fun statusColor(status: TopicStatus, c: SpeedrunColors): Color = when (status) {
    TopicStatus.STRONG -> c.performance
    TopicStatus.BUILDING -> c.memory
    TopicStatus.NEEDS_WORK -> c.readinessBad
    TopicStatus.NOT_STARTED, TopicStatus.NOT_IN_DECKS -> c.textTertiary
}

@Composable
private fun MetricDot(color: Color, text: String) {
    val c = Speedrun.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).clip(RoundedCornerShape(2.dp)).background(color))
        Spacer(Modifier.width(5.dp))
        Text(text, color = c.textSecondary, style = MaterialTheme.typography.caption)
    }
}

/** Collapsed MCAT-section cards shared by Home (dashboard) and Decks; tapping a
 *  section drills into its subtopics, so neither screen shows a 30-row wall. */
@Composable
fun TopicSections(dash: TopicDashboardUi, onOpenSection: (String) -> Unit) {
    val sections = dash.sections.filter { it.topics.isNotEmpty() || it.disabled }
    Column(verticalArrangement = Arrangement.spacedBy(Space.l)) {
        sections.forEach { sec -> SectionCard(sec, onOpenSection) }
    }
}

@Composable
private fun SectionCard(sec: TopicSectionUi, onOpenSection: (String) -> Unit) {
    val c = Speedrun.colors
    val cardMod = if (sec.disabled) Modifier else Modifier.clickable { onOpenSection(sec.key) }
    SpeedrunCard(cardMod) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(sec.short, color = c.textPrimary, style = MaterialTheme.typography.subhead)
                Text(
                    sec.full,
                    color = c.textSecondary,
                    style = MaterialTheme.typography.caption,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
            if (!sec.disabled) {
                Icon(
                    Icons.AutoMirrored.Filled.KeyboardArrowRight,
                    contentDescription = "Open section",
                    tint = c.textTertiary,
                    modifier = Modifier.size(20.dp),
                )
            }
        }
        if (sec.disabled) {
            Spacer(Modifier.height(Space.s))
            Text(
                "Passage-based reading practice — no content-category cards.",
                color = c.textTertiary,
                style = MaterialTheme.typography.caption,
            )
            return@SpeedrunCard
        }
        Spacer(Modifier.height(Space.s))
        Row(horizontalArrangement = Arrangement.spacedBy(Space.m)) {
            MetricDot(c.coverageTrack, "Cov ${(sec.coverage * 100).roundToInt()}%")
            MetricDot(c.memory, "Mem ${pctOrDash(sec.memory)}")
            MetricDot(c.performance, "Perf ${pctOrDash(sec.performance)}")
        }
        Spacer(Modifier.height(Space.s))
        Text(
            "${sec.topics.size} topic${if (sec.topics.size != 1) "s" else ""}",
            color = c.textSecondary,
            style = MaterialTheme.typography.caption,
        )
    }
}

/** One section's page: its aggregate signals + the list of subtopics (each into
 *  the per-topic drill-in). Reached by tapping a section card. */
@Composable
fun TopicSectionDetailScreen(
    sectionKey: String,
    onBack: () -> Unit,
    onOpenTopic: (String) -> Unit,
) {
    val c = Speedrun.colors
    var sec by remember { mutableStateOf<TopicSectionUi?>(null) }
    LaunchedEffect(sectionKey) {
        val dash = runCatching { EngineRepository.topicDashboard() }.getOrNull()
        sec = dash?.sections?.firstOrNull { it.key == sectionKey }
    }
    Column(
        Modifier.fillMaxSize().background(c.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        val s = sec
        DetailTopBar(title = s?.short ?: "Section", onBack = onBack)
        Spacer(Modifier.height(Space.m))
        if (s == null) return@Column
        Text(s.full, color = c.textSecondary, style = MaterialTheme.typography.body)
        Spacer(Modifier.height(Space.m))
        Row(horizontalArrangement = Arrangement.spacedBy(Space.m)) {
            MetricDot(c.coverageTrack, "Cov ${(s.coverage * 100).roundToInt()}%")
            MetricDot(c.memory, "Mem ${pctOrDash(s.memory)}")
            MetricDot(c.performance, "Perf ${pctOrDash(s.performance)}")
        }
        Spacer(Modifier.height(Space.m))
        SpeedrunCard {
            s.topics.forEachIndexed { i, t ->
                TopicRow(t, onOpenTopic)
                if (i < s.topics.lastIndex) {
                    Box(
                        Modifier.fillMaxWidth().height(0.5.dp).background(c.separator),
                    )
                }
            }
        }
        Spacer(Modifier.height(Space.xxl))
    }
}

@Composable
private fun TopicRow(t: TopicUi, onOpenTopic: (String) -> Unit) {
    val c = Speedrun.colors
    Row(
        Modifier.fillMaxWidth().clickable { onOpenTopic(t.id) }.padding(vertical = Space.s),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                t.name,
                color = c.textPrimary,
                style = MaterialTheme.typography.body,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(4.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(Space.m)) {
                MetricDot(c.coverageTrack, "${t.cards}")
                MetricDot(c.memory, pctOrDash(t.memory))
                MetricDot(c.performance, pctOrDash(t.performance))
            }
        }
        Spacer(Modifier.width(Space.s))
        Chip(t.status.label, statusColor(t.status, c))
        Icon(
            Icons.AutoMirrored.Filled.KeyboardArrowRight,
            contentDescription = "Open topic",
            tint = c.textTertiary,
            modifier = Modifier.size(20.dp).padding(start = Space.xs),
        )
    }
}

/** One topic's focused view: only its three signals + actions (not the whole dashboard). */
@Composable
fun TopicDetailScreen(
    topicId: String,
    onBack: () -> Unit,
    onReview: () -> Unit,
    onPractice: (List<String>) -> Unit,
) {
    val c = Speedrun.colors
    var topic by remember { mutableStateOf<TopicUi?>(null) }
    var loaded by remember { mutableStateOf(false) }

    LaunchedEffect(topicId) {
        val dash = runCatching { EngineRepository.topicDashboard() }.getOrNull()
        topic = dash?.sections?.flatMap { it.topics }?.firstOrNull { it.id == topicId }
        loaded = true
    }

    Column(
        Modifier.fillMaxSize().background(c.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        val name = topic?.name ?: "Topic"
        DetailTopBar(title = name, onBack = onBack)
        Spacer(Modifier.height(Space.l))
        val t = topic
        if (t == null) {
            if (loaded) {
                SpeedrunCard {
                    Text("Topic not found.", color = c.textSecondary, style = MaterialTheme.typography.body)
                }
            }
            return@Column
        }
        SectionLabel(Mcat.sectionForTopic(t.id)?.short ?: "MCAT topic")
        StatCard("Cards", t.cards.toString(), if (t.covered) "in your library" else "not in your decks yet", c.coverageTrack)
        Spacer(Modifier.height(Space.m))
        val memSub = if (t.review > 0) {
            "${t.mature} mature of ${t.review} review cards"
        } else {
            "No review cards yet — study to build recall."
        }
        StatCard("Memory", pctOrDash(t.memory), memSub, c.memory)
        Spacer(Modifier.height(Space.m))
        val perfSub = if (t.attempts > 0) {
            "${t.correct} of ${t.attempts} questions correct"
        } else {
            "No questions answered yet — practice to measure it."
        }
        StatCard("Performance", pctOrDash(t.performance), perfSub, c.performance)
        Spacer(Modifier.height(Space.xl))
        // Two paths from a topic: review its actual flashcards (memory) and
        // practice its exam-style questions. Filter practice to the section's
        // subjects (the per-category subject isn't in the on-device signals).
        val subjects = Mcat.sectionForTopic(t.id)?.subjects.orEmpty()
        if (t.cards > 0) {
            PrimaryButton("Review memory cards", onClick = onReview)
            Spacer(Modifier.height(Space.s))
            SecondaryButton("Practice questions", onClick = { onPractice(subjects) })
        } else {
            PrimaryButton("Practice questions", onClick = { onPractice(subjects) })
        }
        Spacer(Modifier.height(Space.xxl))
    }
}

@Composable
private fun StatCard(label: String, value: String, sub: String, accent: Color) {
    val c = Speedrun.colors
    SpeedrunCard {
        Text(value, color = accent, style = MaterialTheme.typography.readout)
        Text(
            label,
            color = c.textPrimary,
            style = MaterialTheme.typography.subhead,
            modifier = Modifier.padding(top = Space.xs),
        )
        Text(
            sub,
            color = c.textSecondary,
            style = MaterialTheme.typography.caption,
            modifier = Modifier.padding(top = 2.dp),
        )
    }
}
