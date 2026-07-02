// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import android.webkit.WebView
import androidx.webkit.WebSettingsCompat
import androidx.webkit.WebViewFeature
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import net.speedrun.app.Diagnosis
import net.speedrun.app.EngineRepository
import net.speedrun.app.Rating
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.VoiceExplainSheet
import net.speedrun.app.ui.theme.Radius
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun

@Composable
fun ReviewScreen(onDone: () -> Unit) {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    val haptic = LocalHapticFeedback.current

    var card by remember { mutableStateOf<net.speedrun.app.ReviewCard?>(null) }
    var showAnswer by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var finished by remember { mutableStateOf(false) }
    var reviewed by remember { mutableIntStateOf(0) }
    var shownAt by remember { mutableStateOf(0L) }
    var pendingExplanation by remember { mutableStateOf("") }
    var showVoice by remember { mutableStateOf(false) }
    var diagnosis by remember { mutableStateOf<Diagnosis?>(null) }

    suspend fun loadNext() {
        loading = true
        showAnswer = false
        pendingExplanation = ""
        val next = runCatching { EngineRepository.nextCard() }.getOrNull()
        card = next
        finished = next == null
        loading = false
        shownAt = System.currentTimeMillis()
    }

    LaunchedEffect(Unit) { loadNext() }

    // Post-miss diagnosis cue auto-dismisses.
    LaunchedEffect(diagnosis) {
        if (diagnosis != null) {
            delay(5000)
            diagnosis = null
        }
    }

    fun onRate(rating: Rating) {
        val current = card ?: return
        haptic.performHapticFeedback(HapticFeedbackType.LongPress)
        val explanation = pendingExplanation
        val took = System.currentTimeMillis() - shownAt
        loading = true
        scope.launch {
            val diag = runCatching {
                EngineRepository.answer(current, rating, took, explanation)
            }.getOrNull()
            reviewed++
            diagnosis = diag?.takeIf { it.label != null }
            loadNext()
        }
    }

    val remaining = card?.let { it.newCount + it.learnCount + it.reviewCount } ?: 0
    val progress = if (reviewed + remaining == 0) 0f else reviewed.toFloat() / (reviewed + remaining)

    Column(Modifier.fillMaxSize().background(c.background)) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = Space.s, vertical = Space.xs),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onDone) {
                Icon(Icons.Filled.Close, contentDescription = "Close", tint = c.textSecondary)
            }
            Spacer(Modifier.weight(1f))
            card?.let {
                Text(
                    "${it.newCount} \u00b7 ${it.learnCount} \u00b7 ${it.reviewCount}",
                    color = c.textSecondary,
                    fontSize = 13.sp,
                    modifier = Modifier.padding(end = Space.m),
                )
            }
        }
        LinearProgressIndicator(
            progress = { progress },
            modifier = Modifier.fillMaxWidth().height(3.dp),
            color = c.accent,
            trackColor = c.separator,
        )

        diagnosis?.let { DiagnosisBanner(it) }

        Box(Modifier.weight(1f).fillMaxWidth().padding(Space.l)) {
            when {
                loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = c.accent)
                }
                finished -> CaughtUp(onDone)
                card != null -> CardSurface(
                    html = cardHtml(
                        css = card!!.css,
                        content = if (showAnswer) card!!.answerHtml else card!!.questionHtml,
                        textColor = if (c.isDark) "#EDEDED" else "#1C1C1E",
                    ),
                    baseUrl = EngineRepository.mediaBaseUrl.ifBlank { "file:///android_asset/" },
                )
            }
        }

        if (!loading && !finished && card != null) {
            Column(Modifier.padding(horizontal = Space.l).padding(bottom = Space.l)) {
                if (!showAnswer) {
                    SelfExplainButton(captured = pendingExplanation.isNotBlank()) { showVoice = true }
                    Spacer(Modifier.height(Space.s))
                    PrimaryButton("Show answer") {
                        haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                        showAnswer = true
                    }
                } else {
                    val hints = card!!.intervals
                    Row(horizontalArrangement = Arrangement.spacedBy(Space.s)) {
                        RatingButton("Again", hints.getOrElse(0) { "" }, c.again, Modifier.weight(1f)) { onRate(Rating.AGAIN) }
                        RatingButton("Hard", hints.getOrElse(1) { "" }, c.hard, Modifier.weight(1f)) { onRate(Rating.HARD) }
                        RatingButton("Good", hints.getOrElse(2) { "" }, c.good, Modifier.weight(1f)) { onRate(Rating.GOOD) }
                        RatingButton("Easy", hints.getOrElse(3) { "" }, c.easy, Modifier.weight(1f)) { onRate(Rating.EASY) }
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
private fun SelfExplainButton(captured: Boolean, onClick: () -> Unit) {
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
            if (captured) "Reasoning captured \u2014 edit" else "Self-explain (optional)",
            color = c.accent,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
        )
    }
}

@Composable
private fun DiagnosisBanner(d: Diagnosis) {
    val c = Speedrun.colors
    Column(
        Modifier.fillMaxWidth()
            .background(c.readinessWarn.copy(alpha = 0.14f))
            .padding(horizontal = Space.l, vertical = Space.m),
    ) {
        Text(
            d.label ?: "",
            color = c.textPrimary,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
        )
        if (d.action.isNotBlank()) {
            Text(d.action, color = c.textSecondary, fontSize = 13.sp)
        }
    }
}

@Composable
private fun RatingButton(
    label: String,
    interval: String,
    color: Color,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    Column(
        modifier
            .clip(RoundedCornerShape(Radius.button))
            .background(color)
            .clickable { onClick() }
            .padding(vertical = 10.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(label, color = Color.White, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        if (interval.isNotBlank()) {
            Text(interval, color = Color.White.copy(alpha = 0.9f), fontSize = 11.sp)
        }
    }
}

@Composable
private fun CardSurface(html: String, baseUrl: String) {
    Box(
        Modifier.fillMaxSize()
            .clip(RoundedCornerShape(Radius.card))
            .background(Speedrun.colors.surface)
            .padding(Space.l),
    ) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { ctx ->
                WebView(ctx).apply {
                    setBackgroundColor(android.graphics.Color.TRANSPARENT)
                    settings.javaScriptEnabled = true
                    settings.builtInZoomControls = false
                    // Card <img> tags reference bare filenames resolved against the
                    // media folder's file:// base URL; allow file + subresource access
                    // so images (e.g. MileDown's) render.
                    settings.allowFileAccess = true
                    @Suppress("DEPRECATION")
                    settings.allowFileAccessFromFileURLs = true
                    @Suppress("DEPRECATION")
                    settings.allowUniversalAccessFromFileURLs = true
                    if (WebViewFeature.isFeatureSupported(WebViewFeature.ALGORITHMIC_DARKENING)) {
                        WebSettingsCompat.setAlgorithmicDarkeningAllowed(settings, false)
                    }
                }
            },
            update = { web ->
                if (web.tag != html) {
                    web.tag = html
                    web.loadDataWithBaseURL(baseUrl, html, "text/html", "utf-8", null)
                }
            },
        )
    }
}

@Composable
private fun CaughtUp(onDone: () -> Unit) {
    val c = Speedrun.colors
    Column(
        Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("\u2713", color = c.readinessGood, fontSize = 56.sp, fontWeight = FontWeight.Bold)
        Text(
            "All caught up",
            color = c.textPrimary,
            fontSize = 22.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(top = Space.s),
        )
        Text(
            "No more cards due right now.",
            color = c.textSecondary,
            fontSize = 15.sp,
            modifier = Modifier.padding(top = Space.xs, bottom = Space.xl),
        )
        PrimaryButton("Done", modifier = Modifier.padding(horizontal = Space.xxxl), onClick = onDone)
    }
}

private fun cardHtml(css: String, content: String, textColor: String): String = """
    <!DOCTYPE html><html><head>
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
    <style>
      html,body{margin:0;padding:0;background:transparent;-webkit-text-size-adjust:100%;}
      #sr{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;font-size:20px;
          line-height:1.6;color:$textColor;padding:2px;}
      #sr img{max-width:100%;height:auto;}
      $css
      .card{background:transparent !important;color:$textColor !important;}
    </style></head>
    <body><div id="sr" class="card">$content</div></body></html>
""".trimIndent()
