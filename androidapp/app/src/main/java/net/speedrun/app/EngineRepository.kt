// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import android.content.Context
import anki.decks.DeckTreeNode
import anki.import_export.ImportAnkiPackageOptions
import anki.scheduler.CardAnswer
import anki.speedrun.ClassifyAttemptRequest
import anki.speedrun.FeedbackReport
import anki.speedrun.QuestionItem
import anki.speedrun.RecordAttemptRequest
import anki.sync.SyncCollectionResponse
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.Executors

/**
 * Share/download URL for the MileDown MCAT `.apkg` (cards + images). A Google
 * Drive share link is fine - [net.speedrun.app.ui.screens] resolves it to a
 * direct download that skips Drive's virus-scan interstitial.
 */
const val MILEDOWN_DECK_URL: String =
    "https://drive.google.com/file/d/1K3Z2lbQIB_t_FhGq9wRp8IlXDC_shAzq/view?usp=sharing"

/**
 * Single owner of the shared engine on the phone. All calls are serialized onto
 * one background thread (the collection is a single-writer resource) and mapped
 * from protobuf into the UI domain models. Screens talk only to this repository.
 */
object EngineRepository {

    private val dispatcher = Executors.newSingleThreadExecutor().asCoroutineDispatcher()

    @Volatile
    private var backend: AnkiBackend? = null

    /** file:// base URL for the media folder, so card <img> tags resolve. */
    var mediaBaseUrl: String = ""
        private set

    /** The collection's media folder (images etc.), set once the collection opens. */
    private var mediaDir: File? = null

    // Paths kept so a full-download sync can reopen the (replaced) collection file.
    private var colPath: String? = null
    private var mediaPath: String? = null
    private var mediaDbPath: String? = null

    /** Stable id for this study session (groups recorded attempts). */
    private val sessionId: String = UUID.randomUUID().toString()

    /**
     * Open the collection, creating a fresh empty one if none exists yet
     * (idempotent). A brand-new phone - or one whose app data was wiped - lands
     * on the in-app import flow instead of a dead end, because the collection is
     * always openable and content is imported afterwards. Seeds the MCAT outline
     * when empty.
     */
    suspend fun open(context: Context): OpenState = withContext(dispatcher) {
        backend?.let { return@withContext OpenState.Ready }
        val dir = context.getExternalFilesDir(null) ?: context.filesDir
        val col = File(dir, "collection.anki2")
        try {
            val b = AnkiBackend.open()
            val media = File(dir, "collection.media").apply { mkdirs() }
            val mediaDb = File(dir, "collection.media.db2")
            // Opens the DB, creating it if the file is absent - so we never block
            // the user from importing their first deck.
            b.openCollection(col.absolutePath, media.absolutePath, mediaDb.absolutePath)
            mediaDir = media
            mediaBaseUrl = "file://${media.absolutePath}/"
            colPath = col.absolutePath
            mediaPath = media.absolutePath
            mediaDbPath = mediaDb.absolutePath
            runCatching {
                if (b.getTopicMap().entriesList.isEmpty()) b.seedMcatTopicOutline()
            }
            backend = b
            OpenState.Ready
        } catch (e: Exception) {
            OpenState.Error(e.message ?: e.toString())
        }
    }

    /**
     * True once the collection holds something worth studying: at least one deck
     * of cards or one held-out practice question. Drives the first-run import
     * flow (false = show Get started).
     */
    suspend fun hasContent(): Boolean = engine { b ->
        val hasDeck = b.deckTree().childrenList.any {
            it.deckId != 1L ||
                (it.newCount + it.learnCount + it.reviewCount) > 0 ||
                it.childrenList.isNotEmpty()
        }
        hasDeck || b.getPerformanceReport().questionItems > 0
    }

    /** How many media files (images etc.) are present; 0 means cards show broken images. */
    fun mediaFileCount(): Int = runCatching { mediaDir?.listFiles()?.count { it.isFile } ?: 0 }.getOrDefault(0)

    val deckPathHint: String get() = "Android/data/net.speedrun.app/files/collection.anki2"

    /** The deck tree (top-level decks with nested subdecks/topics). */
    suspend fun deckTree(): List<DeckNode> = engine { b ->
        b.deckTree().childrenList
            .map { it.toNode() }
            .filter { it.id != 1L || it.dueTotal > 0 || it.hasChildren }
    }

    private fun DeckTreeNode.toNode(): DeckNode = DeckNode(
        id = deckId,
        name = name,
        level = level,
        newCount = newCount,
        learnCount = learnCount,
        reviewCount = reviewCount,
        children = childrenList.map { it.toNode() },
    )

    suspend fun setCurrentDeck(deckId: Long) = engine { it.setCurrentDeck(deckId) }

    suspend fun readiness(): Readiness = engine { b ->
        val s = b.computeReadiness()
        Readiness(
            memory = s.memory,
            performance = s.performance,
            coverage = s.coverage,
            recallPerfGap = s.recallPerfGap,
            readinessScaled = s.readinessScaled,
            low = s.lowScaled,
            high = s.highScaled,
            sufficient = s.sufficient,
            memorySufficient = s.memorySufficient,
            performanceSufficient = s.performanceSufficient,
            blockingDimension = s.blockingDimension,
            reason = s.reason,
        )
    }

    suspend fun examProfile(): ExamProfileUi = engine { b ->
        val p = b.getExamProfile()
        ExamProfileUi(p.examDateMs, p.targetScore)
    }

    suspend fun setExamProfile(examDateMs: Long, targetScore: Int): ExamProfileUi = engine { b ->
        val p = b.setExamProfile(examDateMs, targetScore)
        ExamProfileUi(p.examDateMs, p.targetScore)
    }

    suspend fun examPlan(): ExamPlanUi = engine { b ->
        val p = b.getExamPlan()
        ExamPlanUi(
            hasProfile = p.hasProfile,
            daysLeft = p.daysLeft,
            currentReadiness = p.currentReadiness,
            targetScore = p.targetScore,
            onTrack = p.onTrack,
            neededPoints = p.neededPoints,
            pointsPerWeek = p.pointsPerWeekNeeded,
            studyMode = p.studyMode,
            recommendedTier = p.recommendedTier,
            readinessSufficient = p.readinessSufficient,
            note = p.note,
        )
    }

    suspend fun performance(): PerformanceUi = engine { b ->
        val r = b.getPerformanceReport()
        PerformanceUi(
            cardsEvaluated = r.cardsEvaluated,
            examAttempts = r.examAttempts,
            recallRate = r.recallRate,
            performanceRate = r.performanceRate,
            gap = r.recallPerfGap,
            sufficient = r.sufficient,
            note = r.note,
        )
    }

    suspend fun coverage(): CoverageUi = engine { b ->
        val r = b.getCoverageReport()
        CoverageUi(
            topicsTotal = r.topicsTotal,
            topicsCovered = r.topicsCovered,
            coverage = r.coverage,
            weightedCoverage = r.weightedCoverage,
            topics = r.topicsList.map { TopicCoverageUi(it.label.ifBlank { it.topic }, it.weight, it.cards, it.covered) },
        )
    }

    suspend fun calibration(): CalibrationUi = engine { b ->
        val r = b.getCalibrationReport()
        CalibrationUi(
            n = r.n,
            brier = r.brier,
            logLoss = r.logLoss,
            sufficient = r.sufficient,
            note = r.note,
            bins = r.binsList.map { CalibrationBinUi(it.lo, it.hi, it.count, it.meanPredicted, it.meanOutcome) },
        )
    }

    suspend fun nextCard(): ReviewCard? = engine { b ->
        val q = b.getQueuedCards(1)
        val card = q.cardsList.firstOrNull() ?: return@engine null
        val r = b.renderExistingCard(card.card.id)
        val intervals = runCatching { b.describeNextStates(card.states) }.getOrDefault(emptyList())
        ReviewCard(
            cardId = card.card.id,
            noteId = card.card.noteId,
            questionHtml = AnkiBackend.nodesToHtml(r.questionNodesList),
            answerHtml = AnkiBackend.nodesToHtml(r.answerNodesList),
            css = r.css,
            newCount = q.newCount,
            learnCount = q.learningCount,
            reviewCount = q.reviewCount,
            states = card.states,
            intervals = intervals,
        )
    }

    /**
     * Grade the card through FSRS and record a Speedrun attempt (with any voice
     * self-explanation), returning the miss diagnosis if the engine classified one.
     */
    suspend fun answer(
        card: ReviewCard,
        rating: Rating,
        tookMs: Long,
        selfExplanation: String,
    ): Diagnosis? = engine { b ->
        val s = card.states
        val newState = when (rating) {
            Rating.AGAIN -> s.again
            Rating.HARD -> s.hard
            Rating.GOOD -> s.good
            Rating.EASY -> s.easy
        }
        val ratingEnum = when (rating) {
            Rating.AGAIN -> CardAnswer.Rating.AGAIN
            Rating.HARD -> CardAnswer.Rating.HARD
            Rating.GOOD -> CardAnswer.Rating.GOOD
            Rating.EASY -> CardAnswer.Rating.EASY
        }
        val ms = tookMs.coerceIn(1, 60_000).toInt()
        b.answerCard(
            CardAnswer.newBuilder()
                .setCardId(card.cardId)
                .setCurrentState(s.current)
                .setNewState(newState)
                .setRating(ratingEnum)
                .setAnsweredAtMillis(System.currentTimeMillis())
                .setMillisecondsTaken(ms)
                .build(),
        )

        // Speedrun evidence: same shape the desktop reviewer records.
        val correct = rating != Rating.AGAIN
        val recallFailed = rating == Rating.AGAIN
        runCatching {
            val signals = ClassifyAttemptRequest.newBuilder()
                .setCorrect(correct)
                .setTookMs(ms)
                .setRecallFailed(recallFailed)
                .setPassageEvidenceMissed(false)
                .setQuestionType(QUESTION_TYPE_SRS)
                .build()
            val data = JSONObject().put("self_explanation", selfExplanation).toString()
            val req = RecordAttemptRequest.newBuilder()
                .setCardId(card.cardId)
                .setNoteId(card.noteId)
                .setSessionId(sessionId)
                .setAnsweredAtMs(System.currentTimeMillis())
                .setTookMs(ms)
                .setQuestionType(QUESTION_TYPE_SRS)
                .setCorrect(correct)
                .setSignals(signals)
                .setData(data)
                .build()
            val resp = b.recordAttempt(req)
            Diagnosis(resp.diagnosis.kind, resp.diagnosis.routedAction)
        }.getOrNull()
    }

    private const val QUESTION_TYPE_SRS = 0
    private const val QUESTION_TYPE_DISCRETE = 2

    /** Number of held-out question items currently in the bank. */
    suspend fun questionCount(): Int = engine { it.getPerformanceReport().questionItems }

    /** Import the bundled MMLU pack from app assets; returns questions added. */
    suspend fun importMmluAsset(context: Context): Int = engine { b ->
        val text = context.assets.open("speedrun_mmlu_pack.json")
            .bufferedReader().use { it.readText() }
        importPackText(b, text)
    }

    /**
     * Curated end-to-end test: import the bundled biology deck (.apkg) plus its
     * topic-matched held-out questions, so reviewing the deck and finishing it
     * pulls a relevant reasoning round (not random). Mirrors the desktop e2e pack.
     */
    suspend fun importE2eBiology(context: Context): String = engine { b ->
        val tmp = File(context.cacheDir, "speedrun_e2e_biology.apkg")
        context.assets.open("speedrun_e2e_biology.apkg").use { input ->
            tmp.outputStream().use { input.copyTo(it) }
        }
        val options = runCatching { b.getImportAnkiPackagePresets() }
            .getOrDefault(ImportAnkiPackageOptions.getDefaultInstance())
        val notes = runCatching { b.importAnkiPackage(tmp.absolutePath, options).log.foundNotes }
            .getOrDefault(0)
        runCatching { tmp.delete() }
        val qtext = context.assets.open("speedrun_e2e_biology.json")
            .bufferedReader().use { it.readText() }
        val added = importPackText(b, qtext)
        "Imported $notes biology cards + $added matched questions"
    }

    /**
     * Import a local file the user picked or downloaded: a `.json` question pack
     * (added to the bank) or a `.apkg`/`.colpkg` deck (imported via the engine).
     * Returns a short human summary.
     */
    suspend fun importLocalFile(path: String, name: String): String = engine { b ->
        if (name.lowercase().endsWith(".json")) {
            val n = importPackText(b, File(path).readText())
            "Added $n practice questions"
        } else {
            val options = runCatching { b.getImportAnkiPackagePresets() }
                .getOrDefault(ImportAnkiPackageOptions.getDefaultInstance())
            val resp = b.importAnkiPackage(path, options)
            val found = resp.log.foundNotes
            if (found > 0) "Imported deck ($found notes)" else "Deck imported"
        }
    }

    private fun importPackText(b: AnkiBackend, text: String): Int {
        val arr = JSONObject(text).optJSONArray("questions") ?: JSONArray()
        var added = 0
        for (i in 0 until arr.length()) {
            val q = arr.getJSONObject(i)
            val payload = JSONObject()
                .put("stem", q.optString("stem"))
                .put("options", q.optJSONArray("options") ?: JSONArray())
                .put("correct_index", q.optInt("correct_index", 0))
                .put("explanation", q.optString("explanation", ""))
                .toString()
            val item = QuestionItem.newBuilder()
                .setCardId(0L)
                .setTopic(q.optString("topic", ""))
                .setProvenance(q.optInt("provenance", 0))
                .setPayload(payload)
                .build()
            b.addQuestionItem(item)
            added++
        }
        return added
    }

    /** Fetch a batch of held-out practice questions, parsed from their payloads. */
    suspend fun practiceQuestions(limit: Int, topic: String = ""): List<QuestionItemUi> = engine { b ->
        b.getPracticeQuestions(limit, topic).mapNotNull { it.toUi() }.filter { it.options.size >= 2 }
    }

    /**
     * The end-of-session reasoning round: held-out questions for the concepts
     * just reviewed (card-linked -> topic-matched via the deck-name map ->
     * unseen fallback). Selection runs in the shared engine, same as desktop.
     */
    suspend fun sessionReasoningRound(reviewedCardIds: List<Long>, limit: Int): List<QuestionItemUi> =
        engine { b ->
            val session = b.getSessionReasoningRound(reviewedCardIds, limit)
                .mapNotNull { it.toUi() }
                .filter { it.options.size >= 2 }
            if (session.size >= limit) return@engine session
            // Top up from the engine's scheduled reasoning-due queue (Design 2 /
            // D1), de-duped by question id, mirroring the desktop reviewer.
            val seen = session.map { it.id }.toMutableSet()
            val merged = session.toMutableList()
            for (q in b.getDueReasoning(limit).mapNotNull { it.toUi() }.filter { it.options.size >= 2 }) {
                if (merged.size >= limit) break
                if (seen.add(q.id)) merged.add(q)
            }
            merged
        }

    /** The engine's scheduled reasoning-due queue (Design 2 / D1). */
    suspend fun dueReasoning(limit: Int): List<QuestionItemUi> =
        engine { b ->
            b.getDueReasoning(limit).mapNotNull { it.toUi() }.filter { it.options.size >= 2 }
        }

    /** The end-of-session feedback report (Design 2 / D2). */
    suspend fun feedbackReport(): FeedbackReport =
        engine { b -> b.getFeedbackReport() }

    private fun anki.speedrun.QuestionItem.toUi(): QuestionItemUi? = runCatching {
        val o = JSONObject(payload)
        val optsArr = o.getJSONArray("options")
        val options = (0 until optsArr.length()).map { optsArr.getString(it) }
        QuestionItemUi(
            id = id,
            cardId = cardId,
            topic = topic,
            stem = o.optString("stem"),
            options = options,
            correctIndex = o.optInt("correct_index", 0),
            explanation = o.optString("explanation", ""),
        )
    }.getOrNull()

    /**
     * Record an answered practice question as an exam-style attempt
     * (question_type=2), feeding the performance signal + calibration.
     */
    suspend fun recordQuestionAttempt(
        item: QuestionItemUi,
        selectedIndex: Int,
        tookMs: Long,
        confidence: Float?,
        selfExplanation: String,
    ): Diagnosis? = engine { b ->
        val correct = selectedIndex == item.correctIndex
        val ms = tookMs.coerceIn(1, 600_000).toInt()
        runCatching {
            val signals = ClassifyAttemptRequest.newBuilder()
                .setCorrect(correct)
                .setTookMs(ms)
                .setRecallFailed(false)
                .setPassageEvidenceMissed(false)
                .setQuestionType(QUESTION_TYPE_DISCRETE)
                .build()
            val data = JSONObject().put("self_explanation", selfExplanation).toString()
            val builder = RecordAttemptRequest.newBuilder()
                .setCardId(item.cardId)
                .setNoteId(0)
                .setSessionId(sessionId)
                .setAnsweredAtMs(System.currentTimeMillis())
                .setTookMs(ms)
                .setQuestionType(QUESTION_TYPE_DISCRETE)
                .setSelected(selectedIndex)
                .setCorrect(correct)
                .setSignals(signals)
                .setData(data)
            if (confidence != null) builder.setPredicted(confidence)
            val resp = b.recordAttempt(builder.build())
            Diagnosis(resp.diagnosis.kind, resp.diagnosis.routedAction)
        }.getOrNull()
    }

    /**
     * Two-way collection sync against a self-hosted anki-sync-server. Runs an
     * incremental sync; on a first sync it resolves the required full
     * upload/download, reopening the collection after a download (which replaces
     * the local file). Media is skipped. Requires the native TLS/HTTP stack in
     * rsandroid to be built (track A2); until then this will error at the network
     * layer, and the flow degrades to the local-only experience.
     */
    suspend fun sync(url: String, username: String, password: String): SyncResult =
        withContext(dispatcher) {
            val b = backend ?: return@withContext SyncResult.Error("Collection is not open")
            try {
                val endpoint = normalizeEndpoint(url)
                val auth = b.syncLogin(username, password, endpoint)
                when (b.syncCollection(auth).required) {
                    SyncCollectionResponse.ChangesRequired.NO_CHANGES,
                    SyncCollectionResponse.ChangesRequired.NORMAL_SYNC ->
                        SyncResult.Ok("In sync")
                    SyncCollectionResponse.ChangesRequired.FULL_UPLOAD -> {
                        b.fullUploadOrDownload(auth, upload = true)
                        SyncResult.Ok("Uploaded this device's collection to the server")
                    }
                    SyncCollectionResponse.ChangesRequired.FULL_DOWNLOAD -> {
                        b.fullUploadOrDownload(auth, upload = false)
                        reopenCollection(b)
                        SyncResult.Ok("Mirrored the server's collection to this device")
                    }
                    else ->
                        SyncResult.Conflict(
                            "Both sides changed since the last sync - choose upload or download to resolve.",
                        )
                }
            } catch (e: Exception) {
                SyncResult.Error(e.message ?: e.toString())
            }
        }

    /**
     * Resolve a two-sided sync conflict by forcing a direction: [upload] = true
     * pushes this device's collection to the server, false mirrors the server's
     * copy down (reopening the replaced local collection).
     */
    suspend fun resolveSyncConflict(
        url: String,
        username: String,
        password: String,
        upload: Boolean,
    ): SyncResult = withContext(dispatcher) {
        val b = backend ?: return@withContext SyncResult.Error("Collection is not open")
        try {
            val endpoint = normalizeEndpoint(url)
            val auth = b.syncLogin(username, password, endpoint)
            b.fullUploadOrDownload(auth, upload = upload)
            if (!upload) reopenCollection(b)
            SyncResult.Ok(
                if (upload) {
                    "Uploaded this device's collection to the server"
                } else {
                    "Mirrored the server's collection to this device"
                },
            )
        } catch (e: Exception) {
            SyncResult.Error(e.message ?: e.toString())
        }
    }

    /** Reopen the collection after a full download replaced the local file. */
    private fun reopenCollection(b: AnkiBackend) {
        val col = colPath ?: return
        runCatching { b.closeCollection() }
        b.openCollection(col, mediaPath ?: "", mediaDbPath ?: "")
    }

    private fun normalizeEndpoint(url: String): String {
        var u = url.trim()
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://$u"
        if (!u.endsWith("/")) u += "/"
        return u
    }

    fun shutdown() {
        backend?.close()
        backend = null
    }

    private suspend fun <T> engine(block: (AnkiBackend) -> T): T = withContext(dispatcher) {
        val b = backend ?: error("Collection is not open")
        block(b)
    }
}

/** Outcome of a collection sync attempt, for the UI to surface. */
sealed class SyncResult {
    data class Ok(val message: String) : SyncResult()
    data class Conflict(val message: String) : SyncResult()
    data class Error(val message: String) : SyncResult()
}
