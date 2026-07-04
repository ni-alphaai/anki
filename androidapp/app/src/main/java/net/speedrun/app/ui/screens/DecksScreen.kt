// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.animateFloatAsState
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Style
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import kotlinx.coroutines.launch
import net.speedrun.app.DeckNode
import net.speedrun.app.EngineRepository
import net.speedrun.app.ui.DueCounts
import net.speedrun.app.ui.GroupFootnote
import net.speedrun.app.ui.IconTile
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.RowDivider
import net.speedrun.app.ui.ScreenHeader
import net.speedrun.app.ui.SecondaryButton
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SettingsGroup
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import net.speedrun.app.ui.theme.body
import net.speedrun.app.ui.theme.caption
import net.speedrun.app.ui.theme.readout
import net.speedrun.app.ui.theme.subhead

/**
 * The home / default destination: a "today" hero summarizing what's due with the
 * primary study action, then the deck list as an inset-grouped list. The
 * readiness verdict lives on its own [DashboardScreen].
 */
@Composable
fun DecksScreen(
    onOpenDeck: (Long, String) -> Unit,
    onReview: (Long, String) -> Unit,
    onPractice: () -> Unit,
    onOpenSettings: () -> Unit,
    onOpenSection: (String) -> Unit,
) {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    var tree by remember { mutableStateOf<List<DeckNode>?>(null) }
    var dash by remember { mutableStateOf<net.speedrun.app.TopicDashboardUi?>(null) }
    val expanded = remember { mutableStateMapOf<Long, Boolean>() }

    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        scope.launch {
            tree = runCatching { EngineRepository.deckTree() }.getOrElse { emptyList() }
            dash = runCatching { EngineRepository.topicDashboard() }.getOrNull()
        }
    }

    val topDecks = tree ?: emptyList()
    val bestDeck = topDecks.maxByOrNull { it.dueTotal }
    val totalDue = topDecks.sumOf { it.dueTotal }
    val deckCount = topDecks.size

    val visible = ArrayList<Pair<DeckNode, Int>>()
    fun collect(nodes: List<DeckNode>, depth: Int) {
        nodes.forEach { node ->
            visible.add(node to depth)
            if (expanded[node.id] == true) collect(node.children, depth + 1)
        }
    }
    collect(topDecks, 0)

    Column(
        Modifier.fillMaxSize().background(c.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        ScreenHeader(
            title = "Decks",
            trailing = {
                IconButton(onClick = onOpenSettings) {
                    Icon(Icons.Outlined.Settings, contentDescription = "Settings", tint = c.textSecondary)
                }
            },
        )
        Spacer(Modifier.height(Space.m))

        TodayHero(
            totalDue = totalDue,
            deckCount = deckCount,
            onReview = { bestDeck?.let { onReview(it.id, it.name) } },
            onPractice = onPractice,
        )
        if (totalDue > 0) {
            Spacer(Modifier.height(Space.s))
            SecondaryButton("Practice questions", onClick = onPractice)
        }

        dash?.takeIf { it.hasTopics }?.let { d ->
            Spacer(Modifier.height(Space.xxl))
            SectionLabel("By MCAT topic")
            TopicSections(d, onOpenSection)
        }

        Spacer(Modifier.height(Space.xxl))
        SectionLabel("Your decks")
        when (val t = tree) {
            null -> SpeedrunCard { Text("Loading decks…", color = c.textSecondary, style = MaterialTheme.typography.body) }
            else -> if (t.isEmpty()) {
                SpeedrunCard {
                    Text("No decks yet", color = c.textPrimary, style = MaterialTheme.typography.subhead)
                    Text(
                        "Add a deck or the MCAT content library from the Library tab to start reviewing.",
                        color = c.textSecondary,
                        style = MaterialTheme.typography.body,
                        modifier = Modifier.padding(top = Space.xs),
                    )
                }
            } else {
                SettingsGroup(Modifier.animateContentSize()) {
                    visible.forEachIndexed { i, (node, depth) ->
                        DeckTreeRow(
                            node = node,
                            depth = depth,
                            expanded = expanded[node.id] == true,
                            onToggle = { expanded[node.id] = !(expanded[node.id] ?: false) },
                            onOpen = { onOpenDeck(node.id, node.name) },
                        )
                        if (i < visible.lastIndex) RowDivider(inset = Space.l)
                    }
                }
                GroupFootnote("Add more decks from the Library tab.")
            }
        }
        Spacer(Modifier.height(Space.xxl))
    }
}

/**
 * The day's study state: the due count (or an "all caught up" state) with a
 * leading glyph and the primary action, so the top of the tab reads at a glance.
 */
@Composable
private fun TodayHero(
    totalDue: Int,
    deckCount: Int,
    onReview: () -> Unit,
    onPractice: () -> Unit,
) {
    val c = Speedrun.colors
    SpeedrunCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (totalDue > 0) {
                IconTile(Icons.Filled.Bolt, tint = c.accent, size = 48.dp)
                Spacer(Modifier.width(Space.m))
                Column(Modifier.weight(1f)) {
                    Text(totalDue.toString(), color = c.textPrimary, style = MaterialTheme.typography.readout)
                    Text(
                        "cards due today" + if (deckCount > 1) " · $deckCount decks" else "",
                        color = c.textSecondary,
                        style = MaterialTheme.typography.body,
                    )
                }
            } else {
                IconTile(Icons.Filled.CheckCircle, tint = c.readinessGood, size = 48.dp)
                Spacer(Modifier.width(Space.m))
                Column(Modifier.weight(1f)) {
                    Text("All caught up", color = c.textPrimary, style = MaterialTheme.typography.subhead)
                    Text(
                        "Nothing due right now — keep momentum with a practice set.",
                        color = c.textSecondary,
                        style = MaterialTheme.typography.body,
                        modifier = Modifier.padding(top = 2.dp),
                    )
                }
            }
        }
        Spacer(Modifier.height(Space.l))
        if (totalDue > 0) {
            PrimaryButton("Review $totalDue due", onClick = onReview)
        } else {
            PrimaryButton("Practice questions", onClick = onPractice)
        }
    }
}

@Composable
private fun DeckTreeRow(
    node: DeckNode,
    depth: Int,
    expanded: Boolean,
    onToggle: () -> Unit,
    onOpen: () -> Unit,
) {
    val c = Speedrun.colors
    val rotation by animateFloatAsState(if (expanded) 90f else 0f, label = "chevron")
    Row(
        Modifier.fillMaxWidth().clickable { onOpen() }
            .padding(horizontal = Space.l, vertical = Space.m),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (depth == 0) {
            IconTile(Icons.Filled.Style, tint = c.accent, size = 40.dp)
            Spacer(Modifier.width(Space.m))
        } else {
            Spacer(Modifier.width((depth * 16).dp))
        }
        // Disclosure control (only for decks with subtopics); rotates when open.
        if (node.hasChildren) {
            Box(
                Modifier.size(28.dp).clickable { onToggle() },
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    Icons.AutoMirrored.Filled.KeyboardArrowRight,
                    contentDescription = if (expanded) "Collapse" else "Expand",
                    tint = c.textTertiary,
                    modifier = Modifier.graphicsLayer { rotationZ = rotation },
                )
            }
            Spacer(Modifier.width(Space.xs))
        }
        Column(Modifier.weight(1f)) {
            Text(node.name, color = c.textPrimary, style = MaterialTheme.typography.subhead)
            Spacer(Modifier.height(Space.xs))
            if (node.dueTotal == 0) {
                Text("Done for now", color = c.textTertiary, style = MaterialTheme.typography.caption)
            } else {
                DueCounts(node.newCount, node.learnCount, node.reviewCount)
            }
        }
        Icon(
            Icons.AutoMirrored.Filled.KeyboardArrowRight,
            contentDescription = "Open deck",
            tint = c.textTertiary,
            modifier = Modifier.size(20.dp),
        )
    }
}
