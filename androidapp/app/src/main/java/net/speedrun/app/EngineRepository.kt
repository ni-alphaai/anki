// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import android.content.Context
import anki.decks.Deck
import anki.decks.DeckTreeNode
import anki.import_export.ImportAnkiPackageOptions
import anki.scheduler.CardAnswer
import anki.speedrun.ClassifyAttemptRequest
import anki.speedrun.QuestionItem
import anki.speedrun.RecordAttemptRequest
import anki.speedrun.TopicMapEntry
import anki.sync.SyncAuth
import anki.sync.SyncCollectionResponse
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
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

    /** Bumped after imports or sample seeding so the Dashboard can reload readiness. */
    private val _readinessRefresh = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val readinessRefresh: SharedFlow<Unit> = _readinessRefresh

    private fun bumpReadinessRefresh() {
        _readinessRefresh.tryEmit(Unit)
    }

    /**
     * Open the collection, creating a fresh empty one if none exists yet
     * (idempotent). A brand-new phone - or one whose app data was wiped - lands
     * on the in-app import flow instead of a dead end, because the collection is
     * always openable and content is imported afterwards. Seeds the MCAT outline
     * when empty.
     */
    suspend fun open(context: Context): OpenState = withContext(dispatcher) {
        attachContext(context)
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

    /**
     * Read a raw JSON value from the synced collection config (the same store
     * the desktop uses via `col.get_config`), or null when the key is unset or
     * the collection isn't open yet. Behavioral settings converge across devices
     * by riding this config through Anki's native sync - see [AppSettings].
     */
    suspend fun getConfigJson(key: String): String? = withContext(dispatcher) {
        val b = backend ?: return@withContext null
        runCatching { b.getConfigJson(key) }.getOrNull()
    }

    /**
     * Write a raw JSON value into the synced collection config; a no-op when the
     * collection isn't open. Mirrors `col.set_config`, so the value converges on
     * the other device on the next sync.
     */
    suspend fun setConfigJson(key: String, json: String): Unit = withContext(dispatcher) {
        val b = backend ?: return@withContext
        runCatching { b.setConfigJson(key, json) }
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

    /**
     * Build (or reuse) a single "Speedrun review" filtered deck holding a topic's
     * cards, make it current, and return its id so the caller can open review.
     * Returns null if the topic has no cards / anything fails.
     */
    suspend fun reviewTopic(topicId: String): Long? = engine { b ->
        runCatching {
            val name = "Speedrun review"
            val existingId = findDeckIdByName(b.deckTree(), name)
            val current = b.getOrCreateFilteredDeck(existingId)
            val term = Deck.Filtered.SearchTerm.newBuilder()
                .setSearch("tag:$topicId")
                .setLimit(200)
                .setOrder(Deck.Filtered.SearchTerm.Order.OLDEST_REVIEWED_FIRST)
                .build()
            val config = current.config.toBuilder()
                .setReschedule(true)
                .clearSearchTerms()
                .addSearchTerms(term)
                .build()
            val deck = current.toBuilder().setName(name).setConfig(config).build()
            val id = b.addOrUpdateFilteredDeck(deck)
            b.setCurrentDeck(id)
            id
        }.getOrNull()
    }

    private fun findDeckIdByName(root: DeckTreeNode, name: String): Long {
        fun walk(node: DeckTreeNode): Long? {
            if (node.name == name) return node.deckId
            for (child in node.childrenList) walk(child)?.let { return it }
            return null
        }
        return walk(root) ?: 0L
    }

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

    suspend fun topicDashboard(): TopicDashboardUi = engine { b ->
        // Unlinked (cardId=0) practice attempts grouped by subject, folded into
        // each MCAT section's performance so a section backed only by the MMLU
        // bank / CARS still shows a real Perf number instead of "-".
        val sectionExtra = mutableMapOf<String, Pair<Int, Int>>()
        runCatching {
            for (st in b.getTopicAttemptStats()) {
                val key = Mcat.sectionForSubject(st.topic)?.key ?: "other"
                val acc = sectionExtra[key] ?: (0 to 0)
                sectionExtra[key] = (acc.first + st.attempts) to (acc.second + st.correct)
            }
        }
        val topics = b.getTopicSignals().map { s ->
            TopicUi(
                id = s.topic,
                name = s.label.ifBlank { s.topic },
                sectionKey = Mcat.sectionForTopic(s.topic)?.key ?: "",
                weight = s.weight,
                cards = s.cards,
                covered = s.covered,
                review = s.reviewCards,
                mature = s.matureCards,
                attempts = s.examAttempts,
                correct = s.examCorrect,
            )
        }
        buildTopicDashboard(topics, sectionExtra)
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
    private const val QUESTION_TYPE_PASSAGE = 1
    private const val QUESTION_TYPE_DISCRETE = 2

    /** Number of held-out question items currently in the bank. */
    suspend fun questionCount(): Int = engine { it.getPerformanceReport().questionItems }

    /**
     * Seed a labeled sample study history (mature review cards + exam/SRS attempts
     * with predictions) so the three scores show with ranges for a demo. Returns
     * (cards matured, attempts recorded). The score stays computed, not hand-set.
     *
     * If the collection has no cards yet, imports the bundled MCAT content library
     * first (cards need topic tags that match the coverage map).
     */
    suspend fun seedSampleHistory(context: Context): Pair<Int, Int> {
        val result = engine { b ->
            var r = b.seedSampleHistory()
            if (r.cardsMatured == 0) {
                importContentLibraryLocked(b, context)
                r = b.seedSampleHistory()
            } else {
                val covered = runCatching { b.getCoverageReport().topicsCovered }.getOrDefault(0)
                if (covered == 0) {
                    // Deck cards lack topic tags that match the coverage map (e.g. MileDown).
                    importContentLibraryLocked(b, context)
                    r = b.seedSampleHistory()
                }
            }
            r.cardsMatured to r.attemptsRecorded
        }
        bumpReadinessRefresh()
        return result
    }

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
     * Import the open-licensed MCAT content library: the bundled multi-topic
     * deck (.apkg, cards tagged by content-category id + subject), its matched
     * practice questions, and the 31-category coverage map. Mirrors the desktop
     * first-run import so both platforms ship the same content.
     */
    suspend fun importContentLibrary(context: Context): String {
        val (notes, added) = engine { b -> importContentLibraryLocked(b, context) }
        bumpReadinessRefresh()
        return "Imported $notes cards + $added questions across 31 MCAT categories"
    }

    /** Shared import path for first-run, Library, and sample seeding. */
    private fun importContentLibraryLocked(b: AnkiBackend, context: Context): Pair<Int, Int> {
        val tmp = File(context.cacheDir, "speedrun_content_library.apkg")
        context.assets.open("speedrun_content_library.apkg").use { input ->
            tmp.outputStream().use { input.copyTo(it) }
        }
        val options = runCatching { b.getImportAnkiPackagePresets() }
            .getOrDefault(ImportAnkiPackageOptions.getDefaultInstance())
        val notes = runCatching { b.importAnkiPackage(tmp.absolutePath, options).log.foundNotes }
            .getOrDefault(0)
        runCatching { tmp.delete() }
        val qtext = context.assets.open("speedrun_content_questions.json")
            .bufferedReader().use { it.readText() }
        val added = importPackText(b, qtext)
        runCatching {
            val ttext = context.assets.open("speedrun_content_topics.json")
                .bufferedReader().use { it.readText() }
            val arr = JSONObject(ttext).optJSONArray("topics") ?: JSONArray()
            val entries = (0 until arr.length()).map {
                val t = arr.getJSONObject(it)
                TopicMapEntry.newBuilder()
                    .setTopic(t.optString("topic"))
                    .setLabel(t.optString("label"))
                    .setWeight(t.optDouble("weight", 1.0).toFloat())
                    .build()
            }
            if (entries.isNotEmpty()) b.setTopicMap(entries)
        }
        return notes to added
    }

    /**
     * A balanced placement set for the onboarding diagnostic: up to [perSection]
     * exam-style questions from each scored section that has a bank (CARS has
     * none), de-duped. Mirrors the desktop `_diagnostic_questions`.
     */
    suspend fun diagnosticQuestions(perSection: Int = 5): List<QuestionItemUi> = engine { b ->
        val out = mutableListOf<QuestionItemUi>()
        val seen = mutableSetOf<Long>()
        for (sec in Mcat.SECTIONS) {
            if (sec.subjects.isEmpty()) continue
            var taken = 0
            for (subject in sec.subjects) {
                if (taken >= perSection) break
                for (q in b.getPracticeQuestions(perSection, subject).mapNotNull { it.toUi() }
                    .filter { it.options.size >= 2 }) {
                    if (taken >= perSection) break
                    if (seen.add(q.id)) {
                        out.add(q)
                        taken++
                    }
                }
            }
        }
        out
    }

    /**
     * Import a local file the user picked or downloaded: a `.json` question pack
     * (added to the bank) or a `.apkg`/`.colpkg` deck (imported via the engine).
     * Returns a short human summary.
     */
    suspend fun importLocalFile(path: String, name: String): String = engine { b ->
        val lower = name.lowercase()
        when {
            lower.endsWith(".json") -> {
                val n = importPackText(b, File(path).readText())
                "Added $n practice questions"
            }
            lower.endsWith(".csv") -> {
                val n = importCsvText(b, File(path).readText())
                "Added $n practice questions from CSV"
            }
            else -> {
                val options = runCatching { b.getImportAnkiPackagePresets() }
                    .getOrDefault(ImportAnkiPackageOptions.getDefaultInstance())
                val resp = b.importAnkiPackage(path, options)
                val found = resp.log.foundNotes
                if (found > 0) "Imported deck ($found notes)" else "Deck imported"
            }
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

    /**
     * Import a CSV question bank (UWorld/AAMC-style export): parse rows into the
     * same payload shape as the JSON packs, then register each. Parsing is pure
     * (see [CsvQuestions]); this only maps to the engine.
     */
    private fun importCsvText(b: AnkiBackend, text: String): Int {
        val parsed = CsvQuestions.parse(text)
        for (q in parsed) {
            val payload = JSONObject()
                .put("stem", q.stem)
                .put("options", JSONArray(q.options))
                .put("correct_index", q.correctIndex)
                .put("explanation", q.explanation)
                .toString()
            b.addQuestionItem(
                QuestionItem.newBuilder()
                    .setCardId(0L)
                    .setTopic(q.topic)
                    .setProvenance(0)
                    .setPayload(payload)
                    .build(),
            )
        }
        return parsed.size
    }

    /** Fetch a batch of held-out practice questions, parsed from their payloads. */
    suspend fun practiceQuestions(limit: Int, topic: String = ""): List<QuestionItemUi> = engine { b ->
        b.getPracticeQuestions(limit, topic).mapNotNull { it.toUi() }.filter { it.options.size >= 2 }
    }

    /** Per-subject counts of the whole bank, for the MCAT-section Practice landing. */
    suspend fun practiceBankSummary(): PracticeBankSummaryUi = engine { b ->
        val s = b.getPracticeBankSummary()
        PracticeBankSummaryUi(total = s.total, byTopic = s.topicsList.associate { it.topic to it.count })
    }

    /**
     * A topic-filtered practice set (empty [topics] = a mixed diagnostic across
     * the whole bank). With topics, pulls an even split from each subject and
     * merges de-duped, so practicing a whole section draws from all its subjects.
     * Mirrors the desktop `_fetch_practice_questions`.
     */
    suspend fun practiceQuestionsForTopics(topics: List<String>, limit: Int = 20): List<QuestionItemUi> =
        engine { b ->
            if (topics.isEmpty()) {
                return@engine b.getPracticeQuestions(limit, "")
                    .mapNotNull { it.toUi() }.filter { it.options.size >= 2 }
            }
            val per = maxOf(limit / topics.size, 4)
            val seen = mutableSetOf<Long>()
            val merged = mutableListOf<QuestionItemUi>()
            for (topic in topics) {
                for (q in b.getPracticeQuestions(per, topic).mapNotNull { it.toUi() }
                    .filter { it.options.size >= 2 }) {
                    if (merged.size >= limit) break
                    if (seen.add(q.id)) merged.add(q)
                }
            }
            merged.take(limit)
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
    suspend fun feedbackReport(): FeedbackReportUi =
        engine { b ->
            val r = b.getFeedbackReport()
            FeedbackReportUi(
                total = r.total,
                correct = r.correct,
                memoryMisses = r.memoryMisses,
                reasoningMisses = r.reasoningMisses,
                passageMisses = r.passageMisses,
                testTakingMisses = r.testTakingMisses,
                weakTopics = r.weakTopicsList,
            )
        }

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
            passage = o.optString("passage", ""),
            passageId = o.optString("passage_id", ""),
            passageTitle = o.optString("passage_title", ""),
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
        session: String? = null,
    ): Diagnosis? = engine { b ->
        val correct = selectedIndex == item.correctIndex
        val ms = tookMs.coerceIn(1, 600_000).toInt()
        // CARS/passage items record as passage MCQ so a miss is diagnosed as a
        // reasoning/passage gap, never as forgotten memory.
        val qType = if (item.isPassage) QUESTION_TYPE_PASSAGE else QUESTION_TYPE_DISCRETE
        runCatching {
            val signals = ClassifyAttemptRequest.newBuilder()
                .setCorrect(correct)
                .setTookMs(ms)
                .setRecallFailed(false)
                .setPassageEvidenceMissed(false)
                .setQuestionType(qType)
                .build()
            val data = JSONObject().put("self_explanation", selfExplanation).toString()
            val builder = RecordAttemptRequest.newBuilder()
                .setCardId(item.cardId)
                .setNoteId(0)
                .setSessionId(session ?: sessionId)
                .setAnsweredAtMs(System.currentTimeMillis())
                .setTookMs(ms)
                .setQuestionType(qType)
                .setSelected(selectedIndex)
                .setCorrect(correct)
                .setTopic(item.topic)
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
                var session = beginSync(b, url, username, password)
                // Note-encode Speedrun's sr_attempts before syncing so evidence
                // rides the standard note sync a stock/AnkiWeb peer keeps (it drops
                // the custom sr_attempts chunk). Idempotent; never block a sync.
                runCatching { b.encodeAttemptsAsNotes() }
                val resp = b.syncCollection(session.auth)
                session = applyNewEndpoint(session, resp)
                when (resp.required) {
                    SyncCollectionResponse.ChangesRequired.NO_CHANGES,
                    SyncCollectionResponse.ChangesRequired.NORMAL_SYNC -> {
                        afterSyncRefresh()
                        SyncResult.Ok("In sync")
                    }
                    SyncCollectionResponse.ChangesRequired.FULL_UPLOAD -> {
                        b.fullUploadOrDownload(session.auth, upload = true)
                        afterSyncRefresh()
                        SyncResult.Ok("Uploaded this device's collection to the server")
                    }
                    SyncCollectionResponse.ChangesRequired.FULL_DOWNLOAD -> {
                        b.fullUploadOrDownload(session.auth, upload = false)
                        reopenCollection(b)
                        afterSyncRefresh()
                        SyncResult.Ok("Mirrored the server's collection to this device")
                    }
                    else -> {
                        // FULL_SYNC: schemas differ AND both sides hold data, so the
                        // engine can't pick a safe direction (the empty-server /
                        // empty-phone cases already resolved to FULL_UPLOAD /
                        // FULL_DOWNLOAD above). Surfacing a conflict lets the user
                        // choose which copy wins instead of silently overwriting the
                        // phone's data.
                        SyncResult.Conflict(
                            "This phone and the desktop have different data. Choose which " +
                                "copy to keep - the other side will be replaced.",
                        )
                    }
                }
            } catch (e: Exception) {
                SyncResult.Error(syncErrorMessage(e))
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
            val session = beginSync(b, url, username, password)
            b.fullUploadOrDownload(session.auth, upload = upload)
            if (!upload) reopenCollection(b)
            afterSyncRefresh()
            SyncResult.Ok(
                if (upload) {
                    "Uploaded this device's collection to the server"
                } else {
                    "Mirrored the server's collection to this device"
                },
            )
        } catch (e: Exception) {
            SyncResult.Error(syncErrorMessage(e))
        }
    }

    /** Reopen the collection after a full download replaced the local file. */
    private fun reopenCollection(b: AnkiBackend) {
        val col = colPath ?: return
        runCatching { b.closeCollection() }
        b.openCollection(col, mediaPath ?: "", mediaDbPath ?: "")
    }

    /**
     * After sync, reconcile the topic map (not chunk-synced) and recompute
     * readiness so the Dashboard reflects cards and attempts from the other device.
     */
    private suspend fun afterSyncRefresh() {
        val b = backend ?: return
        // Decode attempts that arrived as notes back into sr_attempts (idempotent),
        // then reconcile the topic map and recompute readiness so the Dashboard
        // reflects cards and attempts from the other device.
        runCatching { b.decodeNotesToAttempts() }
        ensureTopicMapFromAssets(b, appContext ?: return)
        runCatching { b.computeReadiness() }
        bumpReadinessRefresh()
    }

    private var appContext: Context? = null

    /** Remember app context for post-sync refresh (set when the engine opens). */
    fun attachContext(context: Context) {
        appContext = context.applicationContext
    }

    private data class SyncSession(val endpoint: String, val auth: SyncAuth)

    private fun beginSync(
        b: AnkiBackend,
        url: String,
        username: String,
        password: String,
    ): SyncSession {
        val endpoint = SyncUrl.normalize(url)
        require(SyncUrl.isValid(endpoint)) {
            "Invalid sync server URL. On desktop open Sync, tap Start & show code, " +
                "then re-scan the QR (USB debugging + cable for guest Wi-Fi)."
        }
        val auth = pinAuth(b.syncLogin(username, password, endpoint), endpoint)
        return SyncSession(endpoint, auth)
    }

    private fun applyNewEndpoint(
        session: SyncSession,
        resp: SyncCollectionResponse,
    ): SyncSession {
        val neu = resp.newEndpoint?.trim().orEmpty()
        if (neu.isBlank()) return session
        val endpoint = SyncUrl.normalize(neu)
        if (!SyncUrl.isValid(endpoint)) return session
        return session.copy(endpoint = endpoint, auth = pinAuth(session.auth, endpoint))
    }

    private fun pinAuth(auth: SyncAuth, endpoint: String): SyncAuth =
        auth.toBuilder().setEndpoint(endpoint).build()

    private const val CONNECTIVITY_MESSAGE =
        "Couldn't reach the sync server. On the desktop open Sync with phone to host " +
            "it, and make sure USB or Wi-Fi is connected."

    // Substrings that mark a connectivity/timeout failure rather than a bad URL.
    // Used only as a fallback for errors that arrive without a typed backend kind;
    // note "error sending request for url ()" is a reqwest *network* failure, not
    // an invalid-URL error, so it must map to connectivity.
    private val CONNECTIVITY_HINTS = listOf(
        "error sending request",
        "url ()",
        "connection refused",
        "connection reset",
        "connection timed out",
        "timed out",
        "timeout",
        "failed to connect",
        "network is unreachable",
        "no route to host",
        "could not resolve",
        "dns error",
    )

    /**
     * Map a raw sync exception to an accurate, actionable message. A genuinely
     * invalid URL is only ever rejected by [beginSync]'s `require`, so that case
     * keeps its own message; everything else is classified by the engine's typed
     * error kind (network vs auth) so a timeout is never misreported as a bad URL.
     */
    private fun syncErrorMessage(e: Exception): String {
        if (e is IllegalArgumentException) {
            return e.message ?: "Invalid sync server URL."
        }
        when ((e as? BackendException)?.kind) {
            "NETWORK_ERROR" -> return CONNECTIVITY_MESSAGE
            "SYNC_AUTH_ERROR" -> return "The desktop rejected the pairing key. On the " +
                "desktop open Sync with phone and scan the fresh code."
        }
        val raw = e.message ?: e.toString()
        val lower = raw.lowercase()
        if (CONNECTIVITY_HINTS.any { lower.contains(it) }) return CONNECTIVITY_MESSAGE
        return raw
    }

    private fun ensureTopicMapFromAssets(b: AnkiBackend, context: Context) {
        runCatching {
            if (b.getCoverageReport().topicsCovered > 0) return
            val ttext = context.assets.open("speedrun_content_topics.json")
                .bufferedReader().use { it.readText() }
            val arr = JSONObject(ttext).optJSONArray("topics") ?: JSONArray()
            val entries = (0 until arr.length()).map {
                val t = arr.getJSONObject(it)
                TopicMapEntry.newBuilder()
                    .setTopic(t.optString("topic"))
                    .setLabel(t.optString("label"))
                    .setWeight(t.optDouble("weight", 1.0).toFloat())
                    .build()
            }
            if (entries.isNotEmpty()) b.setTopicMap(entries)
        }
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
