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
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.ScreenHeader
import net.speedrun.app.ui.SecondaryButton
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import net.speedrun.app.ui.theme.body
import net.speedrun.app.ui.theme.caption
import net.speedrun.app.ui.theme.subhead

/**
 * The home / default destination: the day's study actions (review the most-due
 * deck, or practice) above the deck tree. The readiness verdict lives on its own
 * [DashboardScreen] now, so the deck list is reachable without scrolling past it.
 */
@Composable
fun DecksScreen(
    onOpenDeck: (Long, String) -> Unit,
    onReview: (Long, String) -> Unit,
    onPractice: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    var tree by remember { mutableStateOf<List<DeckNode>?>(null) }
    val expanded = remember { mutableStateMapOf<Long, Boolean>() }

    // Reload on resume so due counts reflect reviews done elsewhere.
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        scope.launch {
            tree = runCatching { EngineRepository.deckTree() }.getOrElse { emptyList() }
        }
    }

    val topDecks = tree ?: emptyList()
    val bestDeck = topDecks.maxByOrNull { it.dueTotal }
    val totalDue = topDecks.sumOf { it.dueTotal }

    // Flatten the tree to the currently visible rows (respecting expand state).
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

        PrimaryButton(
            text = if (totalDue > 0) "Review $totalDue due" else "Review",
            enabled = bestDeck != null,
        ) {
            bestDeck?.let { onReview(it.id, it.name) }
        }
        Spacer(Modifier.height(Space.s))
        SecondaryButton("Practice questions", onClick = onPractice)

        Spacer(Modifier.height(Space.xxl))
        SectionLabel("Your decks")
        when (val t = tree) {
            null -> SpeedrunCard { Text("Loading decks\u2026", color = c.textSecondary, style = MaterialTheme.typography.body) }
            else -> if (t.isEmpty()) {
                SpeedrunCard {
                    Text("No decks yet", color = c.textPrimary, style = MaterialTheme.typography.subhead)
                    Text(
                        "Add the MileDown deck from the Library tab to start reviewing.",
                        color = c.textSecondary,
                        style = MaterialTheme.typography.body,
                        modifier = Modifier.padding(top = Space.xs),
                    )
                }
            } else {
                SpeedrunCard(Modifier.animateContentSize()) {
                    visible.forEachIndexed { i, (node, depth) ->
                        DeckTreeRow(
                            node = node,
                            depth = depth,
                            expanded = expanded[node.id] == true,
                            onToggle = { expanded[node.id] = !(expanded[node.id] ?: false) },
                            onOpen = { onOpenDeck(node.id, node.name) },
                        )
                        if (i < visible.lastIndex) {
                            Box(
                                Modifier.fillMaxWidth().height(0.6.dp)
                                    .padding(start = Space.xs)
                                    .background(c.separator),
                            )
                        }
                    }
                }
            }
        }
        Spacer(Modifier.height(Space.xxl))
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
        Modifier.fillMaxWidth().clickable { onOpen() }.padding(vertical = Space.m),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Spacer(Modifier.width((depth * 14).dp))
        // Disclosure control (only for decks with subtopics); rotates when open.
        Box(
            Modifier.size(28.dp).clickable(enabled = node.hasChildren) { onToggle() },
            contentAlignment = Alignment.Center,
        ) {
            if (node.hasChildren) {
                Icon(
                    Icons.AutoMirrored.Filled.KeyboardArrowRight,
                    contentDescription = if (expanded) "Collapse" else "Expand",
                    tint = c.textTertiary,
                    modifier = Modifier.graphicsLayer { rotationZ = rotation },
                )
            }
        }
        Column(Modifier.weight(1f).padding(start = Space.xs)) {
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
