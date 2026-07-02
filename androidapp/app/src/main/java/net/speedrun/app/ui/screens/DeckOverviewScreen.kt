// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import net.speedrun.app.DeckNode
import net.speedrun.app.EngineRepository
import net.speedrun.app.ui.CountPill
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.Selection
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.TertiaryButton
import net.speedrun.app.ui.theme.Display
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun

/** Deck detail: strictly deck-scoped (this deck's due counts + Study + Practice). */
@Composable
fun DeckOverviewScreen(onBack: () -> Unit, onStudy: () -> Unit, onPractice: () -> Unit) {
    val c = Speedrun.colors
    var node by remember { mutableStateOf<DeckNode?>(null) }

    val scope = rememberCoroutineScope()
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        scope.launch {
            runCatching { EngineRepository.setCurrentDeck(Selection.deckId) }
            val tree = runCatching { EngineRepository.deckTree() }.getOrDefault(emptyList())
            node = findNode(tree, Selection.deckId)
        }
    }

    Column(
        Modifier.fillMaxSize().background(c.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back", tint = c.accent)
            }
        }
        Text(
            Selection.deckName.ifBlank { "Deck" },
            color = c.textPrimary,
            fontFamily = Display,
            fontSize = 30.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(start = Space.xs),
        )
        Spacer(Modifier.height(Space.l))

        SpeedrunCard {
            SectionLabel("Due today")
            val n = node
            if (n == null || n.dueTotal == 0) {
                Text(
                    if (n == null) "\u2026" else "All caught up in this deck",
                    color = c.textSecondary,
                    fontSize = 15.sp,
                )
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(Space.l)) {
                    if (n.newCount > 0) CountPill(n.newCount, "new", c.easy)
                    if (n.learnCount > 0) CountPill(n.learnCount, "learn", c.hard)
                    if (n.reviewCount > 0) CountPill(n.reviewCount, "review", c.good)
                }
            }
        }
        Spacer(Modifier.height(Space.xl))

        PrimaryButton("Study now", onClick = onStudy)
        Spacer(Modifier.height(Space.xs))
        TertiaryButton("Practice questions", onClick = onPractice)
        Spacer(Modifier.height(Space.l))

        Text(
            "Practice draws from your full question bank. Your projected score and exam plan live on the Today and Progress tabs.",
            color = c.textTertiary,
            fontSize = 13.sp,
            modifier = Modifier.padding(horizontal = Space.xs),
        )
        Spacer(Modifier.height(Space.xxl))
    }
}

private fun findNode(nodes: List<DeckNode>, id: Long): DeckNode? {
    for (n in nodes) {
        if (n.id == id) return n
        findNode(n.children, id)?.let { return it }
    }
    return null
}
