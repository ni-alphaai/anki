// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui

import androidx.compose.animation.AnimatedContentTransitionScope
import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.automirrored.outlined.MenuBook
import androidx.compose.material.icons.filled.Insights
import androidx.compose.material.icons.filled.School
import androidx.compose.material.icons.outlined.Insights
import androidx.compose.material.icons.outlined.School
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavBackStackEntry
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import kotlinx.coroutines.launch
import net.speedrun.app.EngineRepository
import net.speedrun.app.AppSettings
import net.speedrun.app.OpenState
import net.speedrun.app.ui.screens.DeckOverviewScreen
import net.speedrun.app.ui.screens.GetStartedScreen
import net.speedrun.app.ui.screens.HomeScreen
import net.speedrun.app.ui.screens.LibraryScreen
import net.speedrun.app.ui.screens.OnboardingScreen
import net.speedrun.app.ui.screens.PracticeScreen
import net.speedrun.app.ui.screens.ReviewScreen
import net.speedrun.app.ui.screens.SettingsScreen
import net.speedrun.app.ui.screens.StatsScreen
import net.speedrun.app.ui.theme.Radius
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun

/** Which deck the user tapped into; readiness/plan are collection-wide. */
object Selection {
    var deckId: Long = 0L
    var deckName: String = ""
}

private data class Tab(
    val route: String,
    val label: String,
    val selected: ImageVector,
    val unselected: ImageVector,
)

private val tabs = listOf(
    Tab("today", "Today", Icons.Filled.School, Icons.Outlined.School),
    Tab("progress", "Progress", Icons.Filled.Insights, Icons.Outlined.Insights),
    Tab("library", "Library", Icons.AutoMirrored.Filled.MenuBook, Icons.AutoMirrored.Outlined.MenuBook),
)

// iOS-style push: a detail screen slides in from the trailing edge and slides
// back out on pop.
private val pushEnter: AnimatedContentTransitionScope<NavBackStackEntry>.() -> EnterTransition = {
    slideIntoContainer(AnimatedContentTransitionScope.SlideDirection.Start, tween(320))
}
private val pushPopExit: AnimatedContentTransitionScope<NavBackStackEntry>.() -> ExitTransition = {
    slideOutOfContainer(AnimatedContentTransitionScope.SlideDirection.End, tween(320))
}

@Composable
fun SpeedrunApp() {
    val context = LocalContext.current
    var open by remember { mutableStateOf<OpenState?>(null) }

    LaunchedEffect(Unit) {
        open = EngineRepository.open(context)
    }

    when (val s = open) {
        null -> CenteredMessage(loading = true)
        is OpenState.Error -> CenteredMessage(
            title = "Couldn't open the collection",
            body = s.message,
        )
        else -> MainScaffold() // Ready: the collection is open (created empty if needed)
    }
}

@Composable
private fun MainScaffold() {
    val context = LocalContext.current
    // Route the first launch: import first if empty, then set the exam, then Today.
    var start by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        var hasContent = runCatching { EngineRepository.hasContent() }.getOrDefault(false)
        // Demo convenience: on a fresh install, seed the bundled biology example
        // deck so there is something to review immediately (skipped if the user
        // already has content, and only ever once).
        if (!hasContent && !AppSettings.exampleLoaded) {
            runCatching { EngineRepository.importE2eBiology(context) }
            AppSettings.setExampleLoaded(context, true)
            hasContent = runCatching { EngineRepository.hasContent() }.getOrDefault(false)
        }
        val examSet = runCatching { EngineRepository.examProfile().isSet }.getOrDefault(false)
        start = when {
            !hasContent -> "getstarted"
            !examSet -> "onboarding"
            else -> "today"
        }
    }
    val startRoute = start ?: run {
        CenteredMessage(loading = true)
        return
    }

    val nav = rememberNavController()
    val scope = rememberCoroutineScope()
    val backStack by nav.currentBackStackEntryAsState()
    val route = backStack?.destination?.route
    val showBar = route in setOf("today", "progress", "library")

    Scaffold(
        containerColor = Speedrun.colors.background,
        bottomBar = { if (showBar) BottomBar(route) { dest -> navigateTab(nav, dest) } },
    ) { padding ->
        NavHost(
            navController = nav,
            startDestination = startRoute,
            modifier = Modifier.padding(padding),
            // Baseline crossfade (tab switches, first-run); detail routes slide.
            enterTransition = { fadeIn(tween(180)) },
            exitTransition = { fadeOut(tween(180)) },
            popEnterTransition = { fadeIn(tween(180)) },
            popExitTransition = { fadeOut(tween(180)) },
        ) {
            composable("getstarted") {
                GetStartedScreen(onDone = {
                    nav.navigate("onboarding") { popUpTo("getstarted") { inclusive = true } }
                })
            }
            composable("onboarding") {
                OnboardingScreen(onDone = {
                    nav.navigate("today") { popUpTo("onboarding") { inclusive = true } }
                })
            }
            composable("today") {
                HomeScreen(
                    onOpenDeck = { id, name ->
                        Selection.deckId = id
                        Selection.deckName = name
                        nav.navigate("overview")
                    },
                    onReview = { id, name ->
                        Selection.deckId = id
                        Selection.deckName = name
                        scope.launch {
                            runCatching { EngineRepository.setCurrentDeck(id) }
                            nav.navigate("review")
                        }
                    },
                    onPractice = { nav.navigate("practice") },
                    onOpenSettings = { nav.navigate("settings") },
                )
            }
            composable("progress") { StatsScreen() }
            composable("library") { LibraryScreen() }
            composable(
                "settings",
                enterTransition = pushEnter,
                popExitTransition = pushPopExit,
            ) {
                SettingsScreen(
                    onBack = { nav.popBackStack() },
                    onEditExam = { nav.navigate("onboarding") },
                )
            }
            composable(
                "overview",
                enterTransition = pushEnter,
                popExitTransition = pushPopExit,
            ) {
                DeckOverviewScreen(
                    onBack = { nav.popBackStack() },
                    onStudy = { nav.navigate("review") },
                    onPractice = { nav.navigate("practice") },
                )
            }
            composable(
                "review",
                enterTransition = pushEnter,
                popExitTransition = pushPopExit,
            ) {
                ReviewScreen(onDone = { nav.popBackStack() })
            }
            composable(
                "practice",
                enterTransition = pushEnter,
                popExitTransition = pushPopExit,
            ) {
                PracticeScreen(onDone = { nav.popBackStack() })
            }
        }
    }
}

private fun navigateTab(nav: androidx.navigation.NavController, route: String) {
    nav.navigate(route) {
        // Today is the home base for the tab bar; switch tabs while saving state.
        popUpTo("today") { saveState = true }
        launchSingleTop = true
        restoreState = true
    }
}

@Composable
private fun BottomBar(current: String?, onSelect: (String) -> Unit) {
    val c = Speedrun.colors
    // A floating ink pill; the active tab's icon sits in a light circle.
    Box(Modifier.fillMaxWidth().padding(horizontal = Space.xxl, vertical = Space.m)) {
        Row(
            Modifier.fillMaxWidth()
                .shadow(16.dp, RoundedCornerShape(Radius.pill), clip = false, spotColor = Color.Black.copy(alpha = 0.25f))
                .clip(RoundedCornerShape(Radius.pill))
                .background(c.textPrimary)
                .padding(Space.s),
            horizontalArrangement = Arrangement.SpaceEvenly,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            tabs.forEach { tab ->
                val selected = current == tab.route
                Box(
                    Modifier.size(48.dp).clip(CircleShape)
                        .background(if (selected) c.background else Color.Transparent)
                        .clickable(
                            interactionSource = remember { MutableInteractionSource() },
                            indication = null,
                        ) { onSelect(tab.route) },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        if (selected) tab.selected else tab.unselected,
                        contentDescription = tab.label,
                        tint = if (selected) c.textPrimary else c.background.copy(alpha = 0.6f),
                        modifier = Modifier.size(24.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun CenteredMessage(
    title: String = "",
    body: String = "",
    loading: Boolean = false,
) {
    Box(
        Modifier.fillMaxSize().background(Speedrun.colors.background).padding(Space.xxl),
        contentAlignment = Alignment.Center,
    ) {
        if (loading) {
            CircularProgressIndicator(color = Speedrun.colors.accent)
        } else {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    title,
                    color = Speedrun.colors.textPrimary,
                    fontSize = 22.sp,
                    fontWeight = FontWeight.SemiBold,
                    textAlign = TextAlign.Center,
                )
                Text(
                    body,
                    color = Speedrun.colors.textSecondary,
                    fontSize = 15.sp,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(top = Space.s),
                )
            }
        }
    }
}
