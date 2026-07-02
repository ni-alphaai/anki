// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.sp
import net.speedrun.app.ui.SecondaryButton
import net.speedrun.app.ui.theme.Display
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun

/**
 * First-run welcome shown when the collection is empty. Reuses the Library's
 * import actions so a brand-new phone can never dead-end - the user imports their
 * deck and/or the MMLU pack, then continues.
 */
@Composable
fun GetStartedScreen(onDone: () -> Unit) {
    val c = Speedrun.colors
    Column(
        Modifier.fillMaxSize().background(c.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        Spacer(Modifier.height(Space.xxxl))
        Text("Welcome to Speedrun", color = c.textPrimary, fontFamily = Display, fontSize = 34.sp, fontWeight = FontWeight.Bold)
        Text(
            "An honest MCAT scorecard that keeps memory, performance, and readiness separate. " +
                "Import your deck and practice questions to begin.",
            color = c.textSecondary, fontSize = 15.sp, modifier = Modifier.padding(top = Space.s),
        )
        Spacer(Modifier.height(Space.xl))

        ImportPanel(onImported = {})
        Spacer(Modifier.height(Space.l))

        SecondaryButton("Continue", onClick = onDone)
        Spacer(Modifier.height(Space.m))
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Text(
                "I'll add content later",
                color = c.textTertiary,
                fontSize = 15.sp,
                textAlign = TextAlign.Center,
                modifier = Modifier.clickable { onDone() }.padding(Space.s),
            )
        }
        Spacer(Modifier.height(Space.xxl))
    }
}
