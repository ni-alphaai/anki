// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * The source-grounded AI diagnosis coach, native to the phone.
 *
 * The desktop runs the coach as a Python subprocess; the phone can't, so this
 * calls OpenAI directly (no extra dependency - HttpURLConnection + org.json).
 * It mirrors the desktop coach (`tools/speedrun_ai/coach.py` + `taxonomy.py`):
 * same rubric, same kinds/actions, same JSON contract, so the two stay
 * interchangeable and both fall back to the deterministic classifier when off.
 *
 * On any error or low confidence it returns null (abstain), and the caller keeps
 * the deterministic diagnosis - the AI is strictly optional enrichment.
 */
object AiCoach {
    private const val ENDPOINT = "https://api.openai.com/v1/chat/completions"
    private const val MODEL = "gpt-4o"
    private const val CUTOFF = 0.55

    // Diagnosis kinds + routed actions mirror rslib/src/speedrun/mod.rs.
    private val KIND_NAME = mapOf(1 to "memory", 2 to "reasoning", 3 to "passage", 4 to "test_taking")
    private val NAME_KIND = KIND_NAME.entries.associate { (k, v) -> v to k }
    private val DEFAULT_ACTION = mapOf(1 to 1, 2 to 2, 3 to 2, 4 to 3) // resurface/passage/passage/strategy

    private val RUBRIC = listOf(
        "memory" to "The student could not retrieve the underlying fact/definition; the chosen option reflects not knowing the fact, not misapplying it. Repair: resurface via spaced repetition.",
        "reasoning" to "The student knew the relevant facts but applied them incorrectly (a logic/application error, often a classic distractor trap). Repair: concept-linked application practice.",
        "passage" to "The student missed, misread, or ignored evidence given in the passage/figure/data. Repair: passage-comprehension practice.",
        "test_taking" to "The student very likely knew the concept but answered carelessly/rushed (fast + high confidence, an avoidable slip). Repair: test-taking strategy.",
    )
    private const val RUBRIC_SOURCE =
        "Speedrun failure-mode rubric v1 (rslib/src/speedrun/mod.rs; project_brainlift.md diagnostic taxonomy)"

    private val SYSTEM = buildString {
        append("You are Speedrun's diagnostic coach for MCAT study. A student answered an ")
        append("exam-style question incorrectly. Classify the single ROOT-CAUSE failure mode ")
        append("using ONLY these definitions:\n")
        RUBRIC.forEach { (k, v) -> append("- $k: $v\n") }
        append("The student's own self-explanation of their reasoning is your PRIMARY ")
        append("evidence when present: pinpoint the specific misconception or misstep in ")
        append("THEIR reasoning, and use it to separate the modes - did they not know the ")
        append("fact (memory), know the facts but misapply them (reasoning), miss evidence ")
        append("given in the passage/figure (passage), or know it but slip while rushing ")
        append("(test_taking)? ")
        append("Ground your reasoning in the provided answer explanation, which is the ")
        append("named source; refer to it. Explain the failure - do not simply restate the ")
        append("correct answer. If the evidence is insufficient to choose one mode ")
        append("confidently, set \"abstain\" to true.\n")
        append("Respond with JSON only: {\"kind\": one of ")
        append("[\"memory\",\"reasoning\",\"passage\",\"test_taking\"], \"confidence\": number 0..1, ")
        append("\"rationale\": short string grounded in the explanation, \"source\": short ")
        append("citation of what grounded the call, \"abstain\": boolean}.")
    }

    /** The coach's call. `kind`/`routedAction` mirror the engine's ints. */
    data class Diagnosis(
        val kind: Int,
        val kindName: String,
        val routedAction: Int,
        val rationale: String,
        val source: String,
    )

    private fun userPrompt(
        topic: String,
        stem: String,
        options: List<String>,
        correctIndex: Int,
        selectedIndex: Int,
        explanation: String,
        selfExplanation: String,
        tookMs: Int,
        confidence: Float,
        questionType: Int,
    ): String = buildString {
        append("Topic: ").append(topic.ifBlank { "?" }).append('\n')
        append("Question: ").append(stem).append('\n')
        options.forEachIndexed { i, o ->
            append("  (").append('A' + i).append(") ").append(o)
            if (i == correctIndex) append(" [correct answer]")
            if (i == selectedIndex) append(" [student chose this]")
            append('\n')
        }
        append("Answer explanation (named source): ").append(explanation).append('\n')
        val expl = selfExplanation.trim()
        if (expl.isNotEmpty()) {
            append("Student self-explanation (PRIMARY evidence): \"").append(expl).append("\"\n")
        } else {
            append("Student self-explanation: (none provided)\n")
        }
        append("Behaviour: took_ms=").append(tookMs)
        append(", self_confidence=").append(String.format("%.2f", confidence))
        append(", question_type=").append(questionType)
    }

    suspend fun diagnose(
        apiKey: String,
        topic: String,
        stem: String,
        options: List<String>,
        correctIndex: Int,
        selectedIndex: Int,
        explanation: String,
        selfExplanation: String,
        tookMs: Int,
        confidence: Float,
        questionType: Int,
    ): Diagnosis? = withContext(Dispatchers.IO) {
        if (apiKey.isBlank()) return@withContext null
        runCatching {
            val body = JSONObject()
                .put("model", MODEL)
                .put("temperature", 0)
                .put("seed", 7)
                .put("response_format", JSONObject().put("type", "json_object"))
                .put(
                    "messages",
                    JSONArray()
                        .put(JSONObject().put("role", "system").put("content", SYSTEM))
                        .put(
                            JSONObject().put("role", "user").put(
                                "content",
                                userPrompt(
                                    topic, stem, options, correctIndex, selectedIndex,
                                    explanation, selfExplanation, tookMs, confidence, questionType,
                                ),
                            ),
                        ),
                )
                .toString()

            val conn = (URL(ENDPOINT).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                setRequestProperty("Authorization", "Bearer $apiKey")
                setRequestProperty("Content-Type", "application/json")
                doOutput = true
                connectTimeout = 15_000
                readTimeout = 30_000
            }
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            val ok = conn.responseCode in 200..299
            val text = (if (ok) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use { it.readText() } ?: ""
            conn.disconnect()
            if (!ok) return@runCatching null

            val content = JSONObject(text)
                .getJSONArray("choices").getJSONObject(0)
                .getJSONObject("message").getString("content")
            val out = JSONObject(content)
            val name = out.optString("kind").lowercase().trim()
            val kind = NAME_KIND[name] ?: return@runCatching null
            val conf = out.optDouble("confidence", 0.0)
            if (out.optBoolean("abstain", false) || conf < CUTOFF) return@runCatching null
            Diagnosis(
                kind = kind,
                kindName = KIND_NAME[kind] ?: name,
                routedAction = DEFAULT_ACTION[kind] ?: 0,
                rationale = out.optString("rationale", ""),
                source = out.optString("source", RUBRIC_SOURCE),
            )
        }.getOrNull()
    }
}
