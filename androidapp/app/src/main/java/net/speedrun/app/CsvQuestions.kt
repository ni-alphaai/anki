// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

/**
 * Parse a CSV of multiple-choice questions (UWorld/AAMC-style exports) into the
 * question-bank shape. Flexible headers: a subject/topic column, a stem/question
 * column, option columns (option_a..option_e or bare a..e), a correct column (a
 * letter, a 1-based number, or the answer text), and an optional explanation.
 * Pure so it is unit-testable without the engine. Mirrors desktop `_csv_to_pack`.
 */
object CsvQuestions {
    data class Parsed(
        val topic: String,
        val stem: String,
        val options: List<String>,
        val correctIndex: Int,
        val explanation: String,
    )

    private val TOPIC_KEYS = listOf("subject", "topic", "section", "category")
    private val STEM_KEYS = listOf("stem", "question", "prompt", "question_text")
    private val CORRECT_KEYS = listOf("correct", "answer", "correct_index", "correct_answer", "key")
    private val EXPLANATION_KEYS = listOf("explanation", "rationale", "solution", "feedback")

    fun parse(text: String): List<Parsed> {
        val rows = parseCsv(text).filter { row -> row.any { it.isNotBlank() } }
        if (rows.isEmpty()) return emptyList()
        val headers = rows.first().map { it.trim().lowercase() }
        val out = mutableListOf<Parsed>()
        for (raw in rows.drop(1)) {
            val row = headers.mapIndexed { i, h -> h to (raw.getOrNull(i) ?: "") }.toMap()
            val stem = first(row, STEM_KEYS)
            val options = options(row)
            val correct = correctIndex(first(row, CORRECT_KEYS), options)
            if (stem.isBlank() || options.size < 2 || correct == null) continue
            val topic = first(row, TOPIC_KEYS)
            out.add(
                Parsed(
                    topic = if (topic.isNotBlank()) Mcat.canonicalSubject(topic) else "",
                    stem = stem,
                    options = options,
                    correctIndex = correct,
                    explanation = first(row, EXPLANATION_KEYS),
                ),
            )
        }
        return out
    }

    private fun first(row: Map<String, String>, keys: List<String>): String {
        for (key in keys) {
            val value = row[key]?.trim().orEmpty()
            if (value.isNotEmpty()) return value
        }
        return ""
    }

    private fun options(row: Map<String, String>): List<String> {
        val options = mutableListOf<String>()
        for (letter in "abcde") {
            for (key in listOf("option_$letter", "opt_$letter", "option$letter", "$letter")) {
                val value = row[key]?.trim().orEmpty()
                if (value.isNotEmpty()) {
                    options.add(value)
                    break
                }
            }
        }
        return options
    }

    /** A correct-answer cell (letter A-E, 1-based number, or answer text) -> 0-based index. */
    fun correctIndex(raw: String, options: List<String>): Int? {
        val t = raw.trim()
        if (t.isEmpty()) return null
        if (t.length == 1 && t[0].isLetter()) {
            val idx = t.uppercase()[0] - 'A'
            return if (idx in options.indices) idx else null
        }
        t.toIntOrNull()?.let { num ->
            if (num == 0) return 0
            if (num in 1..options.size) return num - 1
        }
        return options.indexOf(t).takeIf { it >= 0 }
    }

    /** A minimal RFC4180-ish parser (handles quoted fields, commas, newlines). */
    private fun parseCsv(text: String): List<List<String>> {
        val rows = mutableListOf<List<String>>()
        var row = mutableListOf<String>()
        val field = StringBuilder()
        var inQuotes = false
        val s = text.replace("\r\n", "\n").replace('\r', '\n')
        var i = 0
        while (i < s.length) {
            val ch = s[i]
            when {
                inQuotes -> when {
                    ch == '"' && i + 1 < s.length && s[i + 1] == '"' -> {
                        field.append('"'); i++
                    }
                    ch == '"' -> inQuotes = false
                    else -> field.append(ch)
                }
                ch == '"' -> inQuotes = true
                ch == ',' -> { row.add(field.toString()); field.clear() }
                ch == '\n' -> { row.add(field.toString()); rows.add(row); row = mutableListOf(); field.clear() }
                else -> field.append(ch)
            }
            i++
        }
        if (field.isNotEmpty() || row.isNotEmpty()) {
            row.add(field.toString())
            rows.add(row)
        }
        return rows
    }
}
