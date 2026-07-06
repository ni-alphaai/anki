// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The MCAT taxonomy + next-best-action routing are pure and mirror the desktop,
 *  so both apps group Practice and advise the same next step. */
class McatTest {
    @Test
    fun fourScoredSections() {
        assertEquals(
            listOf("chem_phys", "cars", "bio_biochem", "psych_soc"),
            Mcat.SECTIONS.map { it.key },
        )
    }

    @Test
    fun sectionByKey() {
        assertEquals("Bio/Biochem", Mcat.sectionByKey("bio_biochem")?.short)
        assertNull(Mcat.sectionByKey("nope"))
    }

    @Test
    fun carsIsAReasoningSectionWithItsOwnBank() {
        val cars = Mcat.sectionByKey("cars")
        assertTrue(cars?.reasoning == true)
        assertEquals(listOf("cars"), cars?.subjects)
        assertEquals("cars", Mcat.sectionForSubject("cars")?.key)
    }

    @Test
    fun subjectLabels() {
        assertEquals("General Chemistry", Mcat.subjectLabel("general_chemistry"))
        assertEquals("Psychology / Sociology", Mcat.subjectLabel("psychology_sociology"))
        assertEquals("Organic Chemistry", Mcat.subjectLabel("organic_chemistry")) // fallback
    }

    @Test
    fun canonicalSubjectNormalization() {
        assertEquals("general_chemistry", Mcat.canonicalSubject("General Chemistry"))
        assertEquals("psychology_sociology", Mcat.canonicalSubject("Psychology/Sociology"))
        assertEquals("biology", Mcat.canonicalSubject("biology"))
    }
}

/** Next-best-action routing (pure) mirrors the desktop `_next_action`. */
class NextActionTest {
    private fun readiness(sufficient: Boolean, blocking: String = "none", gap: Float = 0f) =
        Readiness(
            memory = 0f, performance = 0f, coverage = 0f, recallPerfGap = gap,
            readinessScaled = 0, low = 0, high = 0, sufficient = sufficient,
            memorySufficient = false, performanceSufficient = false,
            blockingDimension = blocking, reason = "not enough evidence yet",
        )

    private fun plan(onTrack: Boolean) = ExamPlanUi(
        hasProfile = true, daysLeft = 30, currentReadiness = 500, targetScore = 510,
        onTrack = onTrack, neededPoints = 8, pointsPerWeek = 1.5f, studyMode = "balanced",
        recommendedTier = "Strong", readinessSufficient = true, note = "",
    )

    @Test
    fun performanceBlockRoutesToPractice() {
        assertEquals(NextActionKind.PRACTICE, nextAction(readiness(false, "performance"), null).kind)
    }

    @Test
    fun memoryBlockHasNoCta() {
        assertEquals(NextActionKind.NONE, nextAction(readiness(false, "memory"), null).kind)
    }

    @Test
    fun largeGapBridgesToApplication() {
        val na = nextAction(readiness(true, gap = 0.2f), null)
        assertEquals("Bridge recall to application", na.title)
        assertEquals(NextActionKind.PRACTICE, na.kind)
    }

    @Test
    fun offTrackAdjustsPlan() {
        assertEquals(NextActionKind.EDIT_EXAM, nextAction(readiness(true), plan(onTrack = false)).kind)
    }

    @Test
    fun onTrackKeepsGoing() {
        assertEquals("On track — keep going", nextAction(readiness(true), plan(onTrack = true)).title)
    }
}

/** The FC-number -> section derivation used to group the topic dashboard. */
class TopicSectionTest {
    @Test
    fun derivesSectionFromContentCategoryId() {
        assertEquals("bio_biochem", Mcat.sectionForTopic("1A")?.key)
        assertEquals("bio_biochem", Mcat.sectionForTopic("3B")?.key)
        assertEquals("chem_phys", Mcat.sectionForTopic("4A")?.key)
        assertEquals("chem_phys", Mcat.sectionForTopic("5E")?.key)
        assertEquals("psych_soc", Mcat.sectionForTopic("6A")?.key)
        assertEquals("psych_soc", Mcat.sectionForTopic("10A")?.key)
    }

    @Test
    fun derivesSectionFromFoundationalConceptId() {
        // The coarse 10-FC map uses fc1..fc10 ids; they map to sections too.
        assertEquals("bio_biochem", Mcat.sectionForTopic("fc1")?.key)
        assertEquals("chem_phys", Mcat.sectionForTopic("fc4")?.key)
        assertEquals("psych_soc", Mcat.sectionForTopic("fc9")?.key)
    }

    @Test
    fun nonContentIdsHaveNoSection() {
        assertNull(Mcat.sectionForTopic("11A"))
        assertNull(Mcat.sectionForTopic("cars"))
        assertNull(Mcat.sectionForTopic(""))
    }

    private fun topic(id: String, cards: Int, review: Int, mature: Int, attempts: Int, correct: Int) =
        TopicUi(id, "T$id", Mcat.sectionForTopic(id)?.key ?: "", 3f, cards, cards > 0, review, mature, attempts, correct)

    @Test
    fun buildsDashboardGroupedBySection() {
        val topics = listOf(
            topic("1A", cards = 10, review = 10, mature = 9, attempts = 8, correct = 7),
            topic("4A", cards = 6, review = 0, mature = 0, attempts = 0, correct = 0),
        )
        val dash = buildTopicDashboard(topics)
        assertTrue(dash.hasTopics)
        val bio = dash.sections.first { it.key == "bio_biochem" }
        assertEquals(1, bio.topics.size)
        assertEquals(0.9f, bio.memory!!, 0.001f)
        val cars = dash.sections.first { it.key == "cars" }
        assertTrue(cars.reasoning)
        assertNull(cars.memory)
        assertNull(cars.coverage)
        assertTrue(cars.topics.isEmpty())
    }

    @Test
    fun unlinkedAttemptsFoldIntoSectionPerformance() {
        // A section with no per-topic rows still shows Perf from unlinked (MMLU/
        // CARS) practice folded in by subject -> section.
        val dash = buildTopicDashboard(
            topics = emptyList(),
            sectionExtra = mapOf("cars" to (4 to 3)),
        )
        val cars = dash.sections.first { it.key == "cars" }
        assertEquals(0.75f, cars.performance!!, 0.001f)
        assertTrue(dash.hasTopics)
    }

    @Test
    fun topicStatusReflectsEvidence() {
        assertEquals(TopicStatus.NOT_IN_DECKS, topic("1A", 0, 0, 0, 0, 0).status)
        assertEquals(TopicStatus.NOT_STARTED, topic("1A", 5, 0, 0, 0, 0).status)
        assertEquals(TopicStatus.NEEDS_WORK, topic("1A", 5, 4, 2, 6, 2).status)
        assertEquals(TopicStatus.STRONG, topic("1A", 5, 8, 7, 6, 5).status)
    }
}
