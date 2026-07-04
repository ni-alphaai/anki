// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Timer
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import net.speedrun.app.Diagnosis
import net.speedrun.app.ExamPlanUi
import net.speedrun.app.Readiness
import net.speedrun.app.ui.theme.Radius
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import net.speedrun.app.ui.theme.SpeedrunColors
import net.speedrun.app.ui.theme.body
import net.speedrun.app.ui.theme.caption
import net.speedrun.app.ui.theme.heading
import net.speedrun.app.ui.theme.label
import net.speedrun.app.ui.theme.readout
import net.speedrun.app.ui.theme.stat
import net.speedrun.app.ui.theme.subhead
import net.speedrun.app.ui.theme.title
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/** A rounded elevated surface with a soft, low shadow (the app's default container). */
@Composable
fun SpeedrunCard(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            // iOS grouped-cell look: a flat filled cell (no shadow) whose edge
            // reads from the fill contrast against the grouped background, with a
            // hairline separator for crisp definition.
            .clip(RoundedCornerShape(Radius.card))
            .background(Speedrun.colors.surfaceElevated)
            .border(0.5.dp, Speedrun.colors.separator, RoundedCornerShape(Radius.card))
            .padding(Space.l),
        content = content,
    )
}

/** Small uppercase section label - de-emphasized, supports the content. */
@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text.uppercase(),
        color = Speedrun.colors.textSecondary,
        style = MaterialTheme.typography.label,
        modifier = modifier.padding(start = Space.xs, bottom = Space.s),
    )
}

/**
 * The large-title header for a top-level tab (Today / Progress / Library): a
 * Fraunces `title` with an optional subtitle and an optional trailing action.
 * One component so tab headers never drift in size/weight.
 */
@Composable
fun ScreenHeader(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    trailing: (@Composable () -> Unit)? = null,
) {
    val c = Speedrun.colors
    Column(modifier.fillMaxWidth().padding(top = Space.s)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(title, color = c.textPrimary, style = MaterialTheme.typography.title, modifier = Modifier.weight(1f))
            trailing?.invoke()
        }
        if (subtitle != null) {
            Text(
                subtitle,
                color = c.textSecondary,
                style = MaterialTheme.typography.body,
                modifier = Modifier.padding(top = Space.xs),
            )
        }
    }
}

/**
 * The back + large-title header for a pushed detail screen (Settings, deck
 * overview): a back affordance above the same Fraunces `title`, plus an optional
 * trailing action.
 */
@Composable
fun DetailTopBar(
    title: String,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
    trailing: (@Composable () -> Unit)? = null,
) {
    val c = Speedrun.colors
    Column(modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back", tint = c.accent)
            }
            Spacer(Modifier.weight(1f))
            trailing?.invoke()
        }
        Text(
            title,
            color = c.textPrimary,
            style = MaterialTheme.typography.title,
            modifier = Modifier.padding(start = Space.xs),
        )
    }
}

/**
 * The close + counter bar for an immersive session (reviewer, practice): a Close
 * affordance on the left and an optional progress counter on the right.
 */
@Composable
fun SessionTopBar(
    onClose: () -> Unit,
    modifier: Modifier = Modifier,
    counter: String? = null,
) {
    val c = Speedrun.colors
    Row(
        modifier.fillMaxWidth().padding(horizontal = Space.s, vertical = Space.xs),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onClose) {
            Icon(Icons.Filled.Close, contentDescription = "Close", tint = c.textSecondary)
        }
        Spacer(Modifier.weight(1f))
        if (counter != null) {
            Text(
                counter,
                color = c.textSecondary,
                style = MaterialTheme.typography.caption,
                modifier = Modifier.padding(end = Space.m),
            )
        }
    }
}

/**
 * The signature readiness instrument. Two distinct 270-degree arcs (memory blue,
 * performance green - never one misleading gradient) around a thin outer coverage
 * track, with the projected low-high range drawn as a band + pointer on an inner
 * arc. When abstaining, all arcs fall back to neutral tracks and the center shows
 * the honest empty state.
 */
@Composable
fun RingGauge(
    memory: Float,
    performance: Float,
    coverage: Float,
    scoreFraction: Float,
    lowFraction: Float,
    highFraction: Float,
    active: Boolean,
    modifier: Modifier = Modifier,
    diameter: Dp = 240.dp,
    content: @Composable () -> Unit,
) {
    val c = Speedrun.colors
    val appear by animateFloatAsState(
        targetValue = if (active) 1f else 0f,
        animationSpec = tween(900),
        label = "gaugeAppear",
    )
    val start = 135f
    val total = 270f
    Box(modifier.size(diameter), contentAlignment = Alignment.Center) {
        // The accent haze belongs to the live instrument; when abstaining it just
        // adds clutter behind the empty state, so only show it once there's data.
        if (active) {
            Box(
                Modifier.size(diameter * 0.62f).background(
                    Brush.radialGradient(listOf(c.accent.copy(alpha = 0.12f), Color.Transparent)),
                    CircleShape,
                ),
            )
        }
        Canvas(Modifier.fillMaxSize()) {
            val cov = 4.dp.toPx()
            val mem = 12.dp.toPx()
            val perf = 12.dp.toPx()
            val range = 4.dp.toPx()
            val gap = 4.dp.toPx()
            val r = size.minDimension / 2f

            fun ring(rc: Float, sw: Float, color: Color, sweepFrac: Float, cap: StrokeCap = StrokeCap.Round) {
                if (sweepFrac <= 0f) return
                drawArc(
                    color = color,
                    startAngle = start,
                    sweepAngle = total * sweepFrac.coerceIn(0f, 1f),
                    useCenter = false,
                    topLeft = Offset(center.x - rc, center.y - rc),
                    size = Size(rc * 2, rc * 2),
                    style = Stroke(width = sw, cap = cap),
                )
            }

            val covR = r - cov / 2f
            val memR = covR - cov / 2f - gap - mem / 2f
            val perfR = memR - mem / 2f - gap - perf / 2f
            val rangeR = perfR - perf / 2f - gap - range / 2f

            if (active) {
                // The full three-arc instrument, over its own neutral tracks.
                ring(covR, cov, c.separator, 1f)
                ring(memR, mem, c.separator, 1f)
                ring(perfR, perf, c.separator, 1f)
                ring(covR, cov, c.coverageTrack, coverage * appear)
                ring(memR, mem, c.memory, memory * appear)
                ring(perfR, perf, c.performance, performance * appear)

                // Projected low-high range as a band + pointer on the inner arc.
                val lo = lowFraction.coerceIn(0f, 1f)
                val hi = highFraction.coerceIn(0f, 1f)
                if (hi > lo) {
                    drawArc(
                        color = c.accent.copy(alpha = 0.35f * appear),
                        startAngle = start + total * lo,
                        sweepAngle = total * (hi - lo),
                        useCenter = false,
                        topLeft = Offset(center.x - rangeR, center.y - rangeR),
                        size = Size(rangeR * 2, rangeR * 2),
                        style = Stroke(width = range),
                    )
                }
                val ang = (start + total * scoreFraction.coerceIn(0f, 1f)) * (PI / 180f).toFloat()
                val px = center.x + rangeR * cos(ang)
                val py = center.y + rangeR * sin(ang)
                drawCircle(color = c.accent, radius = (range * 1.4f) * appear, center = Offset(px, py))
            } else {
                // Abstaining: the full instrument at rest - three quiet, thin,
                // even tracks around a generous hairline readout "well". The well
                // sits well OUTSIDE the centered label so "READINESS / no score
                // yet" never crosses or crowds the arcs.
                val restMem = 8.dp.toPx()
                val restPerf = 8.dp.toPx()
                ring(covR, cov, c.separator, 1f)
                ring(memR, restMem, c.separator, 1f)
                ring(perfR, restPerf, c.separator.copy(alpha = 0.6f), 1f)
                drawCircle(
                    color = c.separator,
                    radius = rangeR,
                    center = center,
                    style = Stroke(width = 1.dp.toPx()),
                )
            }
        }
        // The gauge is open at the bottom, so its geometric centre sits high in
        // the arc band. Bias the readout down into that open space so the top
        // label never crosses the inner arcs (or the range pointer).
        Box(Modifier.offset(y = diameter * 0.07f), contentAlignment = Alignment.Center) {
            content()
        }
    }
}

/**
 * Turn the engine's run-on "not enough evidence: need a/b, c/d, ..." reason into
 * the tidy checklist of items that unlock the score (empty when there is no
 * "need" clause). Pure + top-level so it is unit-testable without Compose.
 */
fun parseNeedItems(reason: String): List<String> =
    reason.substringAfter(": need ", "")
        .split(", ")
        .map { it.trim() }
        .filter { it.isNotEmpty() }

/**
 * The readiness verdict, rendered as a scorecard: the two-arc instrument with the
 * projected MCAT score + low-high range, or an honest abstention that names the
 * weakest dimension - always with the memory / performance / coverage breakdown
 * beneath. The exam plan lives in its own [ExamPlanCard].
 */
@Composable
fun ReadinessVerdict(
    readiness: Readiness,
    modifier: Modifier = Modifier,
) {
    val c = Speedrun.colors
    SpeedrunCard(modifier) {
        Box(Modifier.fillMaxWidth().padding(top = Space.s), contentAlignment = Alignment.Center) {
            RingGauge(
                memory = readiness.memory,
                performance = readiness.performance,
                coverage = readiness.coverage,
                scoreFraction = readiness.scoreFraction,
                lowFraction = readiness.lowFraction,
                highFraction = readiness.highFraction,
                active = readiness.sufficient,
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("READINESS", color = c.textSecondary, style = MaterialTheme.typography.label)
                    Spacer(Modifier.height(Space.xs))
                    if (readiness.sufficient) {
                        Text(
                            readiness.readinessScaled.toString(),
                            color = c.textPrimary,
                            style = MaterialTheme.typography.readout,
                        )
                        Text("projected MCAT", color = c.textSecondary, style = MaterialTheme.typography.caption)
                    } else {
                        // An "instrument at rest" tick in the UI font's absence -
                        // a calm rule instead of a lone serif dash floating in the
                        // well - then the honest status beneath it.
                        Box(
                            Modifier
                                .padding(vertical = Space.s)
                                .size(width = 28.dp, height = 3.dp)
                                .clip(RoundedCornerShape(Radius.pill))
                                .background(c.textTertiary),
                        )
                        Text("no score yet", color = c.textSecondary, style = MaterialTheme.typography.caption)
                    }
                }
            }
        }
        Spacer(Modifier.height(Space.l))
        if (readiness.sufficient) {
            Text(
                "Likely ${readiness.low}\u2013${readiness.high}",
                color = c.textSecondary,
                style = MaterialTheme.typography.body,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth(),
            )
        } else {
            // Turn the engine's run-on "not enough evidence: need a/b, c/d, ..."
            // sentence into a tidy, scannable checklist of what unlocks the score,
            // instead of a centered blob of wrapping text.
            val needItems = remember(readiness.reason) { parseNeedItems(readiness.reason) }
            Column(Modifier.fillMaxWidth()) {
                if (needItems.isEmpty()) {
                    Text(
                        readiness.reason.ifBlank {
                            "Keep reviewing to unlock your score once memory and performance have enough data."
                        },
                        color = c.textSecondary,
                        style = MaterialTheme.typography.body,
                    )
                } else {
                    Text(
                        "To unlock your projected score",
                        color = c.textTertiary,
                        style = MaterialTheme.typography.label,
                    )
                    Spacer(Modifier.height(Space.s))
                    needItems.forEach { item ->
                        Row(
                            Modifier.fillMaxWidth().padding(vertical = 3.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Box(Modifier.size(5.dp).clip(CircleShape).background(c.textTertiary))
                            Spacer(Modifier.width(Space.s))
                            Text(item, color = c.textSecondary, style = MaterialTheme.typography.body)
                        }
                    }
                }
                Spacer(Modifier.height(Space.s))
                Text(
                    "Weakest dimension: ${readiness.weakestLabel}",
                    color = c.textTertiary,
                    style = MaterialTheme.typography.caption,
                )
            }
        }
        Spacer(Modifier.height(Space.l))
        Box(
            Modifier.fillMaxWidth().padding(horizontal = Space.xs).height(1.dp)
                .background(c.separator),
        )
        Spacer(Modifier.height(Space.l))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            StatCell("Memory", pct(readiness.memory), c.memory, !readiness.memorySufficient)
            StatCell(
                "Performance",
                if (readiness.performanceSufficient) pct(readiness.performance) else "thin",
                c.performance,
                !readiness.performanceSufficient,
            )
            StatCell("Coverage", pct(readiness.coverage), c.coverageTrack, dimmed = false)
        }
    }
}

/**
 * The exam plan, upgraded from a lone on-track chip into a card: days left, the
 * target, the projected score, the points still needed and the pace, plus the
 * recommended study tier and the engine's note. Renders nothing without a profile.
 */
@Composable
fun ExamPlanCard(plan: ExamPlanUi, modifier: Modifier = Modifier) {
    if (!plan.hasProfile) return
    val c = Speedrun.colors
    SpeedrunCard(modifier) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Exam plan", color = c.textPrimary, style = MaterialTheme.typography.subhead)
            Chip(
                if (plan.onTrack) "On track" else "Behind pace",
                if (plan.onTrack) c.readinessGood else c.readinessWarn,
            )
        }
        Spacer(Modifier.height(Space.s))
        KeyValueRow("Days left", plan.daysLeft.toString())
        KeyValueRow("Target", plan.targetScore.toString())
        if (plan.readinessSufficient) {
            KeyValueRow("Projected now", plan.currentReadiness.toString())
        }
        if (plan.neededPoints > 0) {
            KeyValueRow("Points to target", "+${plan.neededPoints}")
        }
        if (plan.pointsPerWeek > 0f) {
            KeyValueRow("Pace needed", "%.1f pts/wk".format(plan.pointsPerWeek))
        }
        if (plan.recommendedTier.isNotBlank()) {
            KeyValueRow("Recommended", plan.recommendedTier)
        }
        if (plan.note.isNotBlank()) {
            Spacer(Modifier.height(Space.s))
            Text(plan.note, color = c.textSecondary, style = MaterialTheme.typography.caption)
        }
    }
}

@Composable
private fun StatCell(label: String, value: String, color: Color, dimmed: Boolean) {
    val c = Speedrun.colors
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(if (dimmed) c.textTertiary else color))
            Spacer(Modifier.width(Space.xs))
            Text(
                value,
                color = if (dimmed) c.textTertiary else c.textPrimary,
                style = MaterialTheme.typography.stat,
            )
        }
        Spacer(Modifier.height(2.dp))
        Text(label, color = c.textTertiary, style = MaterialTheme.typography.caption)
    }
}

/** A 0..1 signal shown as a labeled, colored track (memory / performance / coverage). */
@Composable
fun SignalBar(
    label: String,
    value: Float,
    valueText: String,
    color: Color,
    dimmed: Boolean = false,
) {
    val c = Speedrun.colors
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().padding(bottom = Space.xs),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(label, color = c.textSecondary, style = MaterialTheme.typography.body)
            Text(
                valueText,
                color = if (dimmed) c.textTertiary else c.textPrimary,
                style = MaterialTheme.typography.stat,
            )
        }
        val fill by animateFloatAsState(value.coerceIn(0f, 1f), tween(700), label = "signal")
        Box(
            Modifier.fillMaxWidth().height(8.dp)
                .clip(RoundedCornerShape(Radius.pill))
                .background(c.separator),
        ) {
            Box(
                Modifier.fillMaxWidth(fill).height(8.dp)
                    .clip(RoundedCornerShape(Radius.pill))
                    .background(if (dimmed) c.textTertiary else color),
            )
        }
    }
}

/**
 * The due-card counts for a deck, unified across Today and the deck overview:
 * new / learning / review as subtle hue-filled pills (spec: pill + label type).
 */
@Composable
fun DueCounts(
    newCount: Int,
    learnCount: Int,
    reviewCount: Int,
    modifier: Modifier = Modifier,
) {
    val c = Speedrun.colors
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(Space.s)) {
        if (newCount > 0) Chip("$newCount new", c.easy)
        if (learnCount > 0) Chip("$learnCount learn", c.hard)
        if (reviewCount > 0) Chip("$reviewCount review", c.good)
    }
}

/** A small pill chip with a colored label - status cues like "On track". */
@Composable
fun Chip(text: String, color: Color) {
    Box(
        Modifier
            .clip(RoundedCornerShape(Radius.pill))
            .background(color.copy(alpha = 0.15f))
            .padding(horizontal = Space.m, vertical = 4.dp),
    ) {
        Text(text, color = color, style = MaterialTheme.typography.label)
    }
}

/** Full-width filled primary action: the accent pill (high emphasis, one per screen). */
@Composable
fun PrimaryButton(
    text: String,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    val c = Speedrun.colors
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    val scale by animateFloatAsState(if (pressed && enabled) 0.97f else 1f, label = "press")
    val haptic = LocalHapticFeedback.current
    Box(
        modifier
            .fillMaxWidth()
            .graphicsLayer { scaleX = scale; scaleY = scale }
            // iOS filled button: flat accent fill (no shadow), capsule shape.
            .clip(RoundedCornerShape(Radius.pill))
            .background(if (enabled) c.primary else c.separator)
            .clickable(interactionSource = interaction, indication = null, enabled = enabled) {
                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                onClick()
            }
            .padding(vertical = 16.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text,
            color = if (enabled) c.onPrimary else c.textTertiary,
            // Bold so the label on the accent fill clears large-text contrast.
            style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.Bold),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/**
 * Outlined secondary action - same footprint as PrimaryButton, a `surface` fill
 * with a hairline `border` and ink label, plus a low shadow, so it unmistakably
 * reads as a tappable button (medium emphasis) rather than a bare text link,
 * sitting below the one filled primary.
 */
@Composable
fun SecondaryButton(
    text: String,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    val c = Speedrun.colors
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    val scale by animateFloatAsState(if (pressed && enabled) 0.97f else 1f, label = "press")
    val haptic = LocalHapticFeedback.current
    Box(
        modifier
            .fillMaxWidth()
            .graphicsLayer { scaleX = scale; scaleY = scale }
            // iOS tinted secondary: a soft accent-tinted fill, flat (no shadow).
            .clip(RoundedCornerShape(Radius.pill))
            .background(if (enabled) c.accent.copy(alpha = 0.12f) else c.background)
            .clickable(interactionSource = interaction, indication = null, enabled = enabled) {
                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                onClick()
            }
            .padding(vertical = 16.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text,
            color = if (enabled) c.accent else c.textTertiary,
            style = MaterialTheme.typography.labelLarge,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/** Tertiary text action - the lightest button weight, for a subordinate choice. */
@Composable
fun TertiaryButton(
    text: String,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    val c = Speedrun.colors
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    val scale by animateFloatAsState(if (pressed && enabled) 0.96f else 1f, label = "press")
    val haptic = LocalHapticFeedback.current
    Box(
        modifier
            .fillMaxWidth()
            .graphicsLayer { scaleX = scale; scaleY = scale }
            .clip(RoundedCornerShape(Radius.pill))
            .clickable(interactionSource = interaction, indication = null, enabled = enabled) {
                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                onClick()
            }
            .padding(vertical = 12.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = c.accent, style = MaterialTheme.typography.labelLarge)
    }
}

/** A label/value row for grouped detail cards. */
@Composable
fun KeyValueRow(label: String, value: String, valueColor: Color = Speedrun.colors.textPrimary) {
    // Weighted halves + a gap so a long value (e.g. "495-504 (middle third)")
    // wraps right-aligned in its own column instead of colliding with the label.
    Row(
        Modifier.fillMaxWidth().padding(vertical = Space.s),
        verticalAlignment = Alignment.Top,
    ) {
        Text(
            label,
            color = Speedrun.colors.textSecondary,
            style = MaterialTheme.typography.body,
            modifier = Modifier.weight(1f),
        )
        Text(
            value,
            color = valueColor,
            style = MaterialTheme.typography.stat,
            textAlign = TextAlign.End,
            modifier = Modifier.weight(1f).padding(start = Space.m),
        )
    }
}

fun pct(v: Float): String = "${(v.coerceIn(0f, 1f) * 100).toInt()}%"

/**
 * One segmented-control primitive (unifies Settings' theme picker and Practice's
 * confidence row): a `surface` thumb sliding on an inset track. Pass
 * [selectedIndex] = -1 for "nothing selected yet".
 */
@Composable
fun SegmentedControl(
    options: List<String>,
    selectedIndex: Int,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val c = Speedrun.colors
    Row(
        modifier.fillMaxWidth()
            .clip(RoundedCornerShape(Radius.control))
            .background(c.separator)
            .padding(3.dp),
        horizontalArrangement = Arrangement.spacedBy(3.dp),
    ) {
        options.forEachIndexed { i, label ->
            val selected = i == selectedIndex
            Box(
                Modifier.weight(1f)
                    .clip(RoundedCornerShape(Radius.control - 3.dp))
                    .background(if (selected) c.surfaceElevated else Color.Transparent)
                    .clickable { onSelect(i) }
                    .padding(vertical = Space.s),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    label,
                    color = if (selected) c.textPrimary else c.textSecondary,
                    style = MaterialTheme.typography.body,
                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                )
            }
        }
    }
}

/** Accent color + icon for a miss's failure mode (spec: kind-aware diagnosis). */
private fun diagnosisStyle(kind: Int, c: SpeedrunColors): Pair<Color, ImageVector> = when (kind) {
    1 -> c.memory to Icons.Filled.Psychology       // memory -> brain
    2 -> c.reasoning to Icons.Filled.Lightbulb     // reasoning -> bulb
    3 -> c.passage to Icons.Filled.Description      // passage -> doc
    4 -> c.readinessWarn to Icons.Filled.Timer      // test-taking -> timer
    else -> c.textSecondary to Icons.Filled.Lightbulb
}

/**
 * The single, kind-aware diagnosis cue used by BOTH the reviewer and practice:
 * a themed, non-shifting inline card that names the failure mode (color + icon),
 * states the routed action, and offers an optional inline "Practice this". Never
 * an auto-dismissing bare tooltip.
 */
@Composable
fun DiagnosisView(
    diagnosis: Diagnosis,
    modifier: Modifier = Modifier,
    onPractice: (() -> Unit)? = null,
    onDismiss: (() -> Unit)? = null,
) {
    val c = Speedrun.colors
    val (accent, icon) = diagnosisStyle(diagnosis.kind, c)
    Row(
        modifier.fillMaxWidth()
            .clip(RoundedCornerShape(Radius.card))
            .background(accent.copy(alpha = 0.12f))
            .border(1.dp, accent.copy(alpha = 0.35f), RoundedCornerShape(Radius.card))
            .padding(Space.m),
        verticalAlignment = Alignment.Top,
    ) {
        Box(
            Modifier.size(36.dp).clip(CircleShape).background(accent.copy(alpha = 0.18f)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, contentDescription = null, tint = accent, modifier = Modifier.size(20.dp))
        }
        Spacer(Modifier.width(Space.m))
        Column(Modifier.weight(1f)) {
            Text(diagnosis.label ?: "", color = c.textPrimary, style = MaterialTheme.typography.subhead)
            if (diagnosis.action.isNotBlank()) {
                Text(
                    diagnosis.action,
                    color = c.textSecondary,
                    style = MaterialTheme.typography.caption,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
            if (onPractice != null) {
                Spacer(Modifier.height(Space.s))
                Text(
                    "Practice this \u2192",
                    color = accent,
                    style = MaterialTheme.typography.body,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clip(RoundedCornerShape(Radius.pill)).clickable { onPractice() },
                )
            }
        }
        if (onDismiss != null) {
            Icon(
                Icons.Filled.Close,
                contentDescription = "Dismiss",
                tint = c.textTertiary,
                modifier = Modifier.size(20.dp).clip(CircleShape).clickable { onDismiss() },
            )
        }
    }
}

/**
 * A shared success / summary state (reviewer "caught up" + practice summary):
 * an optional hero [headline] (readout) or [icon], a title, a message, and up to
 * two actions.
 */
@Composable
fun CompletionState(
    title: String,
    message: String,
    primaryLabel: String,
    onPrimary: () -> Unit,
    modifier: Modifier = Modifier,
    headline: String? = null,
    headlineColor: Color = Speedrun.colors.performance,
    icon: ImageVector? = null,
    iconTint: Color = Speedrun.colors.readinessGood,
    secondaryLabel: String? = null,
    onSecondary: (() -> Unit)? = null,
) {
    val c = Speedrun.colors
    Column(
        modifier.fillMaxSize().padding(Space.xxl),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        when {
            headline != null -> Text(headline, color = headlineColor, style = MaterialTheme.typography.readout)
            icon != null -> Icon(icon, contentDescription = null, tint = iconTint, modifier = Modifier.size(56.dp))
        }
        Text(
            title,
            color = c.textPrimary,
            style = MaterialTheme.typography.heading,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = Space.s),
        )
        Text(
            message,
            color = c.textSecondary,
            style = MaterialTheme.typography.body,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = Space.xs, bottom = Space.xl),
        )
        PrimaryButton(primaryLabel, onClick = onPrimary)
        if (secondaryLabel != null && onSecondary != null) {
            Spacer(Modifier.height(Space.s))
            SecondaryButton(secondaryLabel, onClick = onSecondary)
        }
    }
}

/**
 * The app's text input: an `OutlinedTextField` tokenized to the design language
 * (control radius, hairline border, accent focus ring). Used everywhere instead
 * of a raw M3 field.
 */
@Composable
fun AppTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    placeholder: String? = null,
    singleLine: Boolean = true,
    minLines: Int = 1,
    visualTransformation: VisualTransformation = VisualTransformation.None,
) {
    val c = Speedrun.colors
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = modifier.fillMaxWidth(),
        label = { Text(label) },
        placeholder = placeholder?.let { p -> { Text(p) } },
        singleLine = singleLine,
        minLines = minLines,
        visualTransformation = visualTransformation,
        shape = RoundedCornerShape(Radius.control),
        textStyle = MaterialTheme.typography.body,
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = c.accent,
            unfocusedBorderColor = c.separator,
            focusedLabelColor = c.accent,
            unfocusedLabelColor = c.textSecondary,
            cursorColor = c.accent,
            focusedTextColor = c.textPrimary,
            unfocusedTextColor = c.textPrimary,
            focusedContainerColor = c.surface,
            unfocusedContainerColor = c.surface,
            focusedPlaceholderColor = c.textTertiary,
            unfocusedPlaceholderColor = c.textTertiary,
        ),
    )
}

/**
 * An iOS inset-grouped list container: a flat rounded card whose rows sit flush
 * (each row owns its own padding) and are separated by hairlines. Pairs with
 * [SettingsRow] / [RowDivider] and an optional [GroupFootnote] beneath.
 */
@Composable
fun SettingsGroup(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier.fillMaxWidth()
            .clip(RoundedCornerShape(Radius.card))
            .background(Speedrun.colors.surfaceElevated)
            .border(0.5.dp, Speedrun.colors.separator, RoundedCornerShape(Radius.card)),
        content = content,
    )
}

/**
 * One row of an inset-grouped list (>= 52dp): a title with an optional subtitle,
 * an optional right-aligned [value], an optional [trailing] control (a toggle),
 * and an optional disclosure chevron. Tapping runs [onClick] when set.
 */
@Composable
fun SettingsRow(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    value: String? = null,
    showChevron: Boolean = false,
    titleColor: Color = Speedrun.colors.textPrimary,
    trailing: (@Composable () -> Unit)? = null,
    onClick: (() -> Unit)? = null,
) {
    val c = Speedrun.colors
    val row = modifier.fillMaxWidth().heightIn(min = 52.dp)
    Row(
        (if (onClick != null) row.clickable { onClick() } else row)
            .padding(horizontal = Space.l, vertical = Space.m),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, color = titleColor, style = MaterialTheme.typography.body)
            if (subtitle != null) {
                Text(
                    subtitle,
                    color = c.textSecondary,
                    style = MaterialTheme.typography.caption,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
        }
        if (value != null) {
            // Short values hug the right edge (no weight). Long secondary text
            // belongs in `subtitle` (stacked under the title) so it never starves
            // the title into a character-wrap.
            Text(
                value,
                color = c.textSecondary,
                style = MaterialTheme.typography.body,
                textAlign = TextAlign.End,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(start = Space.m),
            )
        }
        trailing?.let { Spacer(Modifier.width(Space.s)); it() }
        if (showChevron) {
            Spacer(Modifier.width(Space.xs))
            Icon(
                Icons.AutoMirrored.Filled.KeyboardArrowRight,
                contentDescription = null,
                tint = c.textTertiary,
                modifier = Modifier.size(20.dp),
            )
        }
    }
}

/** A hairline row separator, inset from the leading edge like iOS grouped lists. */
@Composable
fun RowDivider(inset: Dp = Space.l) {
    Box(
        Modifier.fillMaxWidth().padding(start = inset).height(0.5.dp)
            .background(Speedrun.colors.separator),
    )
}

/** A small gray footnote beneath an inset group (iOS explanatory caption). */
@Composable
fun GroupFootnote(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        color = Speedrun.colors.textTertiary,
        style = MaterialTheme.typography.caption,
        modifier = modifier.fillMaxWidth().padding(start = Space.l, end = Space.l, top = Space.s),
    )
}

/** A rounded, tinted square holding a symbol - leading accessory for rows/heroes. */
@Composable
fun IconTile(
    icon: ImageVector,
    modifier: Modifier = Modifier,
    tint: Color = Speedrun.colors.accent,
    size: Dp = 40.dp,
) {
    Box(
        modifier.size(size)
            .clip(RoundedCornerShape(Radius.control))
            .background(tint.copy(alpha = 0.14f)),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(size * 0.52f))
    }
}

/** A token-styled Switch (accent track when on) so toggles match the language. */
@Composable
fun AppSwitch(checked: Boolean, onCheckedChange: (Boolean) -> Unit, modifier: Modifier = Modifier) {
    val c = Speedrun.colors
    Switch(
        checked = checked,
        onCheckedChange = onCheckedChange,
        modifier = modifier,
        colors = SwitchDefaults.colors(
            checkedThumbColor = Color.White,
            checkedTrackColor = c.accent,
            checkedBorderColor = c.accent,
            uncheckedThumbColor = c.textTertiary,
            uncheckedTrackColor = c.separator,
            uncheckedBorderColor = c.separator,
        ),
    )
}
