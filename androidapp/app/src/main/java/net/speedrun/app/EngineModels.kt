// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import anki.scheduler.SchedulingStates

/** UI-facing domain models, decoupled from the generated protobuf types. */

/** A deck and its subdecks (topics), mirroring the collection's deck tree. */
data class DeckNode(
    val id: Long,
    val name: String,
    val level: Int,
    val newCount: Int,
    val learnCount: Int,
    val reviewCount: Int,
    val children: List<DeckNode>,
) {
    val dueTotal: Int get() = newCount + learnCount + reviewCount
    val hasChildren: Boolean get() = children.isNotEmpty()
}

/**
 * The MCAT scaled-score band (472-528). Kept in the domain so UI components
 * (the readiness gauge, the "MCAT (472-528)" label) never hardcode the scale.
 */
object McatScale {
    const val MIN = 472
    const val MAX = 528
    const val RANGE = MAX - MIN // 56

    /** Position of a scaled score on the 0..1 gauge sweep. */
    fun fraction(score: Int): Float = ((score - MIN).toFloat() / RANGE).coerceIn(0f, 1f)
}

/** The three separate signals + the honest give-up state. */
data class Readiness(
    val memory: Float,
    val performance: Float,
    val coverage: Float,
    val recallPerfGap: Float,
    val readinessScaled: Int,
    val low: Int,
    val high: Int,
    val sufficient: Boolean,
    val memorySufficient: Boolean,
    val performanceSufficient: Boolean,
    val blockingDimension: String,
    val reason: String,
) {
    /** Composite score as a 0..1 gauge position (and its low-high range band). */
    val scoreFraction: Float get() = McatScale.fraction(readinessScaled)
    val lowFraction: Float get() = McatScale.fraction(low)
    val highFraction: Float get() = McatScale.fraction(high)

    /** Human label for the weakest dimension shown in the abstain state. */
    val weakestLabel: String
        get() = when (blockingDimension.lowercase()) {
            "memory" -> "Memory"
            "performance" -> "Performance"
            "coverage" -> "Coverage"
            else -> blockingDimension.replaceFirstChar { it.uppercase() }
        }
}

data class ExamPlanUi(
    val hasProfile: Boolean,
    val daysLeft: Long,
    val currentReadiness: Int,
    val targetScore: Int,
    val onTrack: Boolean,
    val neededPoints: Int,
    val pointsPerWeek: Float,
    val studyMode: String,
    val recommendedTier: String,
    val readinessSufficient: Boolean,
    val note: String,
)

data class ExamProfileUi(val examDateMs: Long, val targetScore: Int) {
    val isSet: Boolean get() = examDateMs > 0L || targetScore > 0
}

data class PerformanceUi(
    val cardsEvaluated: Int,
    val examAttempts: Int,
    val recallRate: Float,
    val performanceRate: Float,
    val gap: Float,
    val sufficient: Boolean,
    val note: String,
)

data class TopicCoverageUi(val label: String, val weight: Float, val cards: Int, val covered: Boolean)

data class CoverageUi(
    val topicsTotal: Int,
    val topicsCovered: Int,
    val coverage: Float,
    val weightedCoverage: Float,
    val topics: List<TopicCoverageUi>,
)

/** A short status + colour kind for one topic on the dashboard. */
enum class TopicStatus(val label: String) {
    NOT_IN_DECKS("Not in your decks"),
    NOT_STARTED("Not started"),
    NEEDS_WORK("Needs work"),
    STRONG("Strong"),
    BUILDING("Building"),
}

/** One content category's derived signals, for the topic dashboard + drill-in. */
data class TopicUi(
    val id: String,
    val name: String,
    val sectionKey: String,
    val weight: Float,
    val cards: Int,
    val covered: Boolean,
    val review: Int,
    val mature: Int,
    val attempts: Int,
    val correct: Int,
) {
    val memory: Float? get() = if (review > 0) mature.toFloat() / review else null
    val performance: Float? get() = if (attempts > 0) correct.toFloat() / attempts else null
    val status: TopicStatus get() = topicStatus(this)
}

private const val TOPIC_MIN_ATTEMPTS = 3

fun topicStatus(t: TopicUi): TopicStatus = when {
    t.cards == 0 -> TopicStatus.NOT_IN_DECKS
    t.review == 0 && t.attempts == 0 -> TopicStatus.NOT_STARTED
    t.attempts >= TOPIC_MIN_ATTEMPTS && (t.performance ?: 1f) < 0.55f -> TopicStatus.NEEDS_WORK
    (t.memory ?: 0f) >= 0.7f && (t.performance ?: 1f) >= 0.65f -> TopicStatus.STRONG
    else -> TopicStatus.BUILDING
}

/**
 * A section (Bio/Biochem, Chem/Phys, Psych/Soc, CARS) + its topics and aggregate
 * signals. For CARS ([reasoning] = true) coverage and memory are N/A (null),
 * never a misleading 0%; only performance is measured.
 */
data class TopicSectionUi(
    val key: String,
    val short: String,
    val full: String,
    val reasoning: Boolean,
    val total: Int,
    val covered: Int,
    val coverage: Float?,
    val memory: Float?,
    val performance: Float?,
    val attempts: Int,
    val topics: List<TopicUi>,
)

data class TopicDashboardUi(val sections: List<TopicSectionUi>, val hasTopics: Boolean)

/**
 * Group per-topic signals under the four MCAT sections (mirrors desktop).
 * [sectionExtra] carries unlinked (cardId=0) practice attempts per section key
 * as (attempts, correct), folded into each section's performance so a section
 * backed only by the MMLU bank / CARS still shows a real Perf number.
 */
fun buildTopicDashboard(
    topics: List<TopicUi>,
    sectionExtra: Map<String, Pair<Int, Int>> = emptyMap(),
): TopicDashboardUi {
    val sections = Mcat.SECTIONS.map { sec ->
        val rows = topics.asSequence()
            .filter { it.sectionKey == sec.key }
            .sortedWith(compareByDescending<TopicUi> { it.weight }.thenBy { it.name })
            .toList()
        val extra = sectionExtra[sec.key] ?: (0 to 0)
        val sumAtt = rows.sumOf { it.attempts } + extra.first
        val sumCor = rows.sumOf { it.correct } + extra.second
        TopicSectionUi(
            key = sec.key,
            short = sec.short,
            full = sec.full,
            reasoning = sec.reasoning,
            total = rows.size,
            covered = rows.count { it.covered },
            coverage = when {
                sec.reasoning -> null
                rows.isNotEmpty() -> rows.count { it.covered }.toFloat() / rows.size
                else -> 0f
            },
            memory = if (sec.reasoning) {
                null
            } else {
                rows.sumOf { it.review }.let { r ->
                    if (r > 0) rows.sumOf { it.mature }.toFloat() / r else null
                }
            },
            performance = if (sumAtt > 0) sumCor.toFloat() / sumAtt else null,
            attempts = sumAtt,
            topics = rows,
        )
    }
    val hasEvidence = sections.any { it.topics.isNotEmpty() || it.attempts > 0 }
    return TopicDashboardUi(sections, hasEvidence)
}

data class CalibrationBinUi(
    val lo: Float,
    val hi: Float,
    val count: Int,
    val meanPredicted: Float,
    val meanOutcome: Float,
)

data class CalibrationUi(
    val n: Int,
    val brier: Float,
    val logLoss: Float,
    val sufficient: Boolean,
    val note: String,
    val bins: List<CalibrationBinUi>,
)

/** A held-out exam-style question for the performance/reasoning loop. */
data class QuestionItemUi(
    val id: Long,
    val cardId: Long,
    val topic: String,
    val stem: String,
    val options: List<String>,
    val correctIndex: Int,
    val explanation: String,
    val passage: String = "",
    val passageId: String = "",
    val passageTitle: String = "",
) {
    /** CARS/passage item: a miss is a reasoning/passage gap, never forgotten memory. */
    val isPassage: Boolean get() = passage.isNotBlank() || passageId.isNotBlank() || topic == "cars"
}

/** Per-subject question counts of the whole held-out bank (for the Practice landing). */
data class PracticeBankSummaryUi(val total: Int, val byTopic: Map<String, Int>) {
    /** Total questions across a section's subject tags. */
    fun sectionCount(section: Mcat.Section): Int =
        section.subjects.sumOf { byTopic[it] ?: 0 }
}

/** What the CTA on the next-best-action card should do. */
enum class NextActionKind { NONE, PRACTICE, EDIT_EXAM }

/** The single recommended step shown on the dashboard (mirrors desktop `_next_action`). */
data class NextActionUi(
    val title: String,
    val detail: String,
    val ctaLabel: String?,
    val kind: NextActionKind,
)

/**
 * The single recommended next step from the readiness snapshot + exam plan.
 * Mirrors the desktop `_next_action` routing so both apps advise the same thing.
 */
fun nextAction(r: Readiness, plan: ExamPlanUi?): NextActionUi {
    if (!r.sufficient) {
        return when (r.blockingDimension.lowercase()) {
            "memory" -> NextActionUi(
                "Study more cards",
                "Readiness needs more graded reviews before it can estimate your memory signal.",
                null, NextActionKind.NONE,
            )
            "performance" -> NextActionUi(
                "Answer held-out questions",
                "Register and answer exam-style questions so performance is measured separately from recall.",
                "Practice now", NextActionKind.PRACTICE,
            )
            "coverage" -> NextActionUi(
                "Cover more of the outline",
                "Tag cards by MCAT topic; readiness abstains below 50% coverage.",
                null, NextActionKind.NONE,
            )
            else -> NextActionUi(
                "Build more evidence",
                r.reason,
                null, NextActionKind.NONE,
            )
        }
    }
    if (r.recallPerfGap >= 0.15f) {
        return NextActionUi(
            "Bridge recall to application",
            "Your recall outruns your exam-style performance. Practice concept-linked questions to close the gap.",
            "Practice now", NextActionKind.PRACTICE,
        )
    }
    if (plan != null && plan.hasProfile && plan.readinessSufficient && !plan.onTrack) {
        return NextActionUi(
            "Pick up the pace",
            "You need about +${plan.neededPoints} points (~${"%.1f".format(plan.pointsPerWeek)}/week) " +
                "to reach your target by exam day.",
            "Adjust plan", NextActionKind.EDIT_EXAM,
        )
    }
    return NextActionUi(
        "On track — keep going",
        "Memory, performance, and coverage all look healthy. Keep your spaced reviews steady.",
        "Practice", NextActionKind.PRACTICE,
    )
}

/** End-of-session feedback report (Design 2 / D2): miss counts by cause + weak topics. */
data class FeedbackReportUi(
    val total: Int,
    val correct: Int,
    val memoryMisses: Int,
    val reasoningMisses: Int,
    val passageMisses: Int,
    val testTakingMisses: Int,
    val weakTopics: List<String>,
)

enum class Rating { AGAIN, HARD, GOOD, EASY }

/** A single card ready to review; [states] carries the scheduler transitions. */
data class ReviewCard(
    val cardId: Long,
    val noteId: Long,
    val questionHtml: String,
    val answerHtml: String,
    val css: String,
    val newCount: Int,
    val learnCount: Int,
    val reviewCount: Int,
    val states: SchedulingStates,
    // Interval labels for [again, hard, good, easy], from the engine.
    val intervals: List<String>,
)

/**
 * Root-cause classification of a miss, from the deterministic engine.
 * kind: 1=memory, 2=reasoning, 3=passage, 4=test-taking (0/5 = none/correct).
 */
data class Diagnosis(val kind: Int, val routedAction: Int) {
    val label: String?
        get() = when (kind) {
            1 -> "Memory gap"
            2 -> "Reasoning gap"
            3 -> "Passage-comprehension gap"
            4 -> "Test-taking gap"
            else -> null
        }
    val action: String
        get() = when (routedAction) {
            1 -> "It'll resurface sooner via spaced repetition."
            2 -> "Next: concept-linked passage practice."
            3 -> "Next: review your test-taking strategy."
            else -> ""
        }
}

/** A recorded attempt: its id (so a later AI diagnosis can overwrite it) and the
 * engine's deterministic diagnosis. */
data class RecordedAttempt(val attemptId: Long, val diagnosis: Diagnosis)

/** Result of trying to open the shared collection on the device. */
sealed interface OpenState {
    data object Ready : OpenState
    data object NeedsDeck : OpenState
    data class Error(val message: String) : OpenState
}
