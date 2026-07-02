// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import net.speedrun.app.ExamPlanUi
import net.speedrun.app.Readiness
import net.speedrun.app.ui.theme.Display
import net.speedrun.app.ui.theme.Radius
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun

/** A rounded white surface with a soft, low shadow (the app's default container). */
@Composable
fun SpeedrunCard(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .shadow(6.dp, RoundedCornerShape(Radius.card), clip = false, spotColor = Color.Black.copy(alpha = 0.08f))
            .clip(RoundedCornerShape(Radius.card))
            .background(Speedrun.colors.surface)
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
        fontSize = 13.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 0.8.sp,
        modifier = modifier.padding(start = Space.xs, bottom = Space.s),
    )
}

/**
 * The signature element: a circular readiness gauge. The 270-degree track fills
 * with a blue->green gradient (memory -> performance, the thesis) behind a soft
 * glow, or stays an empty track when the app is honestly abstaining.
 */
@Composable
fun RingGauge(
    progress: Float,
    active: Boolean,
    modifier: Modifier = Modifier,
    diameter: Dp = 216.dp,
    stroke: Dp = 18.dp,
    content: @Composable () -> Unit,
) {
    val c = Speedrun.colors
    val sweep by animateFloatAsState(
        targetValue = if (active) progress.coerceIn(0f, 1f) else 0f,
        animationSpec = tween(900),
        label = "ring",
    )
    Box(modifier.size(diameter), contentAlignment = Alignment.Center) {
        Box(
            Modifier.size(diameter * 0.72f).background(
                Brush.radialGradient(listOf(c.accent.copy(alpha = 0.16f), Color.Transparent)),
                CircleShape,
            ),
        )
        Canvas(Modifier.fillMaxSize().padding(stroke / 2)) {
            val sw = stroke.toPx()
            drawArc(
                color = c.separator,
                startAngle = 135f,
                sweepAngle = 270f,
                useCenter = false,
                style = Stroke(width = sw, cap = StrokeCap.Round),
            )
            if (active && sweep > 0f) {
                drawArc(
                    brush = Brush.linearGradient(listOf(c.memory, c.performance)),
                    startAngle = 135f,
                    sweepAngle = 270f * sweep,
                    useCenter = false,
                    style = Stroke(width = sw, cap = StrokeCap.Round),
                )
            }
        }
        content()
    }
}

/**
 * The readiness verdict, rendered as a scorecard: a projected MCAT score inside
 * the gauge with its range and an on-track chip, or an honest abstention that
 * names the weakest dimension - always with the memory / performance / coverage
 * breakdown beneath.
 */
@Composable
fun ReadinessVerdict(
    readiness: Readiness,
    plan: ExamPlanUi? = null,
    modifier: Modifier = Modifier,
) {
    val c = Speedrun.colors
    val fraction = ((readiness.readinessScaled - 472) / 56f).coerceIn(0f, 1f)
    SpeedrunCard(modifier) {
        Box(Modifier.fillMaxWidth().padding(top = Space.s), contentAlignment = Alignment.Center) {
            RingGauge(progress = fraction, active = readiness.sufficient) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        "READINESS",
                        color = c.textTertiary,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        letterSpacing = 1.5.sp,
                    )
                    Spacer(Modifier.height(Space.xs))
                    if (readiness.sufficient) {
                        Text(
                            readiness.readinessScaled.toString(),
                            color = c.textPrimary,
                            fontFamily = Display,
                            fontSize = 60.sp,
                            fontWeight = FontWeight.Bold,
                            lineHeight = 60.sp,
                        )
                        Text("projected MCAT", color = c.textSecondary, fontSize = 13.sp)
                    } else {
                        Text(
                            "\u2014",
                            color = c.textTertiary,
                            fontFamily = Display,
                            fontSize = 52.sp,
                            fontWeight = FontWeight.Bold,
                            lineHeight = 52.sp,
                        )
                        Text("not enough data", color = c.textSecondary, fontSize = 13.sp)
                    }
                }
            }
        }
        Spacer(Modifier.height(Space.l))
        if (readiness.sufficient) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Likely ${readiness.low}\u2013${readiness.high}", color = c.textSecondary, fontSize = 15.sp)
                plan?.takeIf { it.hasProfile }?.let {
                    Spacer(Modifier.width(Space.s))
                    Chip(
                        if (it.onTrack) "On track" else "Behind pace",
                        if (it.onTrack) c.readinessGood else c.readinessWarn,
                    )
                }
            }
        } else {
            Text(
                "Keep reviewing \u2014 your score unlocks once memory and performance have enough data.",
                color = c.textSecondary,
                fontSize = 14.sp,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth().padding(horizontal = Space.l),
            )
        }
        Spacer(Modifier.height(Space.l))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            StatCell("Memory", pct(readiness.memory), c.memory, !readiness.memorySufficient)
            StatCell(
                "Performance",
                if (readiness.performanceSufficient) pct(readiness.performance) else "thin",
                c.performance,
                !readiness.performanceSufficient,
            )
            StatCell("Coverage", pct(readiness.coverage), c.textSecondary, dimmed = false)
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
                fontSize = 17.sp,
                fontWeight = FontWeight.SemiBold,
            )
        }
        Spacer(Modifier.height(2.dp))
        Text(label, color = c.textTertiary, fontSize = 12.sp)
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
            Text(label, color = c.textSecondary, fontSize = 15.sp)
            Text(
                valueText,
                color = if (dimmed) c.textTertiary else c.textPrimary,
                fontSize = 15.sp,
                fontWeight = FontWeight.SemiBold,
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

/** A compact count chip with a colored dot, e.g. "12 new". */
@Composable
fun CountPill(count: Int, label: String, color: Color) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(Space.xs))
        Text(
            "$count $label",
            color = Speedrun.colors.textSecondary,
            fontSize = 13.sp,
            fontWeight = FontWeight.Medium,
        )
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
        Text(text, color = color, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
    }
}

/** Full-width filled primary action: an ink pill that inverts in dark mode. */
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
            .clip(RoundedCornerShape(Radius.pill))
            .background(if (enabled) c.textPrimary else c.separator)
            .clickable(interactionSource = interaction, indication = null, enabled = enabled) {
                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                onClick()
            }
            .padding(vertical = 16.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text,
            color = if (enabled) c.background else c.textTertiary,
            fontSize = 16.sp,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/** Tonal secondary action - same footprint as PrimaryButton, quieter weight. */
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
            .clip(RoundedCornerShape(Radius.pill))
            .background(c.textPrimary.copy(alpha = if (enabled) 0.06f else 0.03f))
            .clickable(interactionSource = interaction, indication = null, enabled = enabled) {
                haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                onClick()
            }
            .padding(vertical = 16.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text,
            color = if (enabled) c.textPrimary else c.textTertiary,
            fontSize = 16.sp,
            fontWeight = FontWeight.SemiBold,
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
        Text(text, color = c.accent, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
    }
}

/** A label/value row for grouped detail cards. */
@Composable
fun KeyValueRow(label: String, value: String, valueColor: Color = Speedrun.colors.textPrimary) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = Space.s),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, color = Speedrun.colors.textSecondary, fontSize = 15.sp)
        Text(value, color = valueColor, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
    }
}

fun pct(v: Float): String = "${(v.coerceIn(0f, 1f) * 100).toInt()}%"
