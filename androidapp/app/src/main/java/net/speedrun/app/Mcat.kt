// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

/**
 * MCAT section/subject taxonomy shared by the Practice UI and the CSV importer.
 *
 * Questions in the bank are tagged with subject strings (biology, biochemistry,
 * general_chemistry, physics, psychology_sociology). The MCAT is organized into
 * four scored sections; this maps each section to the subject tags it draws from,
 * so Practice presents section -> subject -> topic-filtered questions instead of
 * one flat random list. Mirrors the desktop `qt/aqt/speedrun_mcat.py`.
 */
object Mcat {
    /**
     * A scored MCAT section and the subject tags it draws from. CARS is a
     * reasoning section: it has a passage-question bank (subject "cars") but no
     * content-category cards, so [reasoning] flags it for N/A memory/coverage.
     */
    data class Section(
        val key: String,
        val short: String,
        val full: String,
        val subjects: List<String>,
        val reasoning: Boolean = false,
    )

    val SECTIONS: List<Section> = listOf(
        Section(
            "chem_phys",
            "Chem/Phys",
            "Chemical & Physical Foundations of Biological Systems",
            listOf("general_chemistry", "physics"),
        ),
        Section(
            "cars",
            "CARS",
            "Critical Analysis & Reasoning Skills",
            listOf("cars"),
            reasoning = true,
        ),
        Section(
            "bio_biochem",
            "Bio/Biochem",
            "Biological & Biochemical Foundations of Living Systems",
            listOf("biology", "biochemistry"),
        ),
        Section(
            "psych_soc",
            "Psych/Soc",
            "Psychological, Social & Biological Foundations of Behavior",
            listOf("psychology_sociology"),
        ),
    )

    private val subjectLabels = mapOf(
        "biology" to "Biology",
        "biochemistry" to "Biochemistry",
        "general_chemistry" to "General Chemistry",
        "physics" to "Physics",
        "psychology_sociology" to "Psychology / Sociology",
        "cars" to "Critical Analysis & Reasoning",
    )

    fun sectionByKey(key: String): Section? = SECTIONS.firstOrNull { it.key == key }

    /** The section a subject tag belongs to (mirrors desktop section_key_for_subject). */
    fun sectionForSubject(subject: String): Section? =
        SECTIONS.firstOrNull { subject in it.subjects }

    /**
     * The MCAT section a content-category id (e.g. "1A".."10E") belongs to,
     * derived from its Foundational Concept number: FC1-3 -> Bio/Biochem,
     * FC4-5 -> Chem/Phys, FC6-10 -> Psych/Soc. Mirrors the content library's own
     * section assignment without needing per-topic metadata on device. Returns
     * null for ids that aren't content categories.
     */
    fun sectionForTopic(topicId: String): Section? {
        val fc = Regex("^(?:fc)?(\\d+)", RegexOption.IGNORE_CASE)
            .find(topicId)?.groupValues?.get(1)?.toIntOrNull()
            ?: return null
        val key = when (fc) {
            1, 2, 3 -> "bio_biochem"
            4, 5 -> "chem_phys"
            6, 7, 8, 9, 10 -> "psych_soc"
            else -> return null
        }
        return sectionByKey(key)
    }

    /** Human label for a subject tag, falling back to a title-cased slug. */
    fun subjectLabel(subject: String): String =
        subjectLabels[subject] ?: subject.split('_').joinToString(" ") { part ->
            part.replaceFirstChar { it.uppercase() }
        }

    /**
     * Normalize a free-text topic/subject cell from an import into a subject tag.
     * Accepts canonical keys, display labels ("General Chemistry"), and loose
     * punctuation ("Psychology/Sociology"); unknown input becomes an underscore
     * slug so it still imports and groups consistently.
     */
    fun canonicalSubject(text: String): String {
        val slug = text.trim().lowercase()
            .replace('/', ' ')
            .replace('-', ' ')
            .split(Regex("\\s+"))
            .filter { it.isNotEmpty() }
            .joinToString("_")
        if (subjectLabels.containsKey(slug)) return slug
        for ((subject, label) in subjectLabels) {
            val labelSlug = label.lowercase().replace('/', ' ')
                .split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString("_")
            if (slug == labelSlug) return subject
        }
        return slug
    }
}
