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

/** Result of trying to open the shared collection on the device. */
sealed interface OpenState {
    data object Ready : OpenState
    data object NeedsDeck : OpenState
    data class Error(val message: String) : OpenState
}
