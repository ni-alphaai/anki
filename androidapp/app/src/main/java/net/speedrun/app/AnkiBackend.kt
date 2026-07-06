// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import anki.backend.BackendError
import anki.card_rendering.RenderCardResponse
import anki.card_rendering.RenderExistingCardRequest
import anki.card_rendering.RenderedTemplateNode
import anki.collection.OpChangesWithId
import anki.collection.OpenCollectionRequest
import anki.config.SetConfigJsonRequest
import anki.generic.Json
import anki.generic.String as GenericString
import com.google.protobuf.ByteString
import anki.decks.DeckId
import anki.decks.DeckTreeNode
import anki.decks.DeckTreeRequest
import anki.decks.FilteredDeckForUpdate
import anki.generic.StringList
import anki.import_export.ImportAnkiPackageOptions
import anki.import_export.ImportAnkiPackageRequest
import anki.import_export.ImportResponse
import anki.scheduler.CardAnswer
import anki.scheduler.CountsForDeckTodayResponse
import anki.scheduler.GetQueuedCardsRequest
import anki.scheduler.QueuedCards
import anki.scheduler.SchedulingStates
import anki.speedrun.CalibrationReport
import anki.speedrun.CoverageReport
import anki.speedrun.ExamPlan
import anki.speedrun.ExamProfile
import anki.speedrun.FeedbackReport
import anki.speedrun.GetDueReasoningRequest
import anki.speedrun.GetPracticeQuestionsRequest
import anki.speedrun.PerformanceReport
import anki.speedrun.PracticeBankSummary
import anki.speedrun.QuestionItem
import anki.speedrun.QuestionItemId
import anki.speedrun.QuestionItems
import anki.speedrun.ReadinessSnapshot
import anki.speedrun.RecordAttemptRequest
import anki.speedrun.RecordAttemptResponse
import anki.speedrun.SeedSampleHistoryRequest
import anki.speedrun.SeedSampleHistoryResponse
import anki.speedrun.SessionReasoningRoundRequest
import anki.speedrun.SetTopicMapResponse
import anki.speedrun.TopicMap
import anki.speedrun.TopicMapEntry
import anki.speedrun.TopicAttemptStat
import anki.speedrun.TopicAttemptStats
import anki.speedrun.TopicSignal
import anki.speedrun.TopicSignalsReport
import anki.sync.FullUploadOrDownloadRequest
import anki.sync.SyncAuth
import anki.sync.SyncCollectionRequest
import anki.sync.SyncCollectionResponse
import anki.sync.SyncLoginRequest

/** A backend RPC that returned an error instead of a response. */
class BackendException(val kind: String, message: String) : Exception(message)

/**
 * Typed wrapper over the shared Anki/Speedrun Rust engine.
 *
 * Every call goes through the same protobuf service boundary the desktop app
 * uses (`Backend::run_service_method`), addressed by the generated
 * (service, method) indices from `out/pylib/anki/_backend_generated.py`. The
 * phone therefore runs the exact same core - FSRS scheduler, collection
 * storage, and the Speedrun diagnostic engine - no logic is reimplemented here.
 */
class AnkiBackend private constructor(private var ptr: Long) {

    // --- Collection -------------------------------------------------------

    fun openCollection(collectionPath: String, mediaFolder: String, mediaDb: String) {
        val req = OpenCollectionRequest.newBuilder()
            .setCollectionPath(collectionPath)
            .setMediaFolderPath(mediaFolder)
            .setMediaDbPath(mediaDb)
            .build()
        run(SVC_COLLECTION, M_OPEN_COLLECTION, req.toByteArray())
    }

    fun closeCollection() {
        run(SVC_COLLECTION, M_CLOSE_COLLECTION, ByteArray(0))
    }

    // --- Collection config (synced JSON key/value) ------------------------

    /**
     * Read a raw JSON config value from the collection by key, or null when the
     * key is unset. Collection config rides Anki's native sync, so this is the
     * same synced store the desktop uses via `col.get_config` - the two
     * platforms share one source of truth for behavioral preferences.
     *
     * A missing key surfaces as a typed backend error (NotFound); we map that to
     * null so callers can fall back to a documented default instead of crashing.
     */
    fun getConfigJson(key: String): String? = try {
        val req = GenericString.newBuilder().setVal(key).build()
        val bytes = run(SVC_CONFIG, M_GET_CONFIG_JSON, req.toByteArray())
        Json.parseFrom(bytes).json.toStringUtf8()
    } catch (_: BackendException) {
        null
    }

    /**
     * Write a raw JSON config value by key (non-undoable - a settings toggle
     * shouldn't land on the review undo stack). Matches the desktop's
     * `col.set_config`, so a value set here converges on the other device on the
     * next sync.
     */
    fun setConfigJson(key: String, json: String) {
        val req = SetConfigJsonRequest.newBuilder()
            .setKey(key)
            .setValueJson(ByteString.copyFromUtf8(json))
            .setUndoable(false)
            .build()
        run(SVC_CONFIG, M_SET_CONFIG_JSON, req.toByteArray())
    }

    // --- Decks / scheduler ------------------------------------------------

    /** The full deck tree with counts (new/learn/review) after limits. */
    fun deckTree(): DeckTreeNode {
        val req = DeckTreeRequest.newBuilder()
            .setNow(System.currentTimeMillis() / 1000)
            .build()
        return DeckTreeNode.parseFrom(run(SVC_DECKS, M_DECK_TREE, req.toByteArray()))
    }

    /** A filtered deck ready to edit (did=0 to create a new one). */
    fun getOrCreateFilteredDeck(did: Long): FilteredDeckForUpdate =
        FilteredDeckForUpdate.parseFrom(
            run(
                SVC_DECKS,
                M_GET_OR_CREATE_FILTERED_DECK,
                DeckId.newBuilder().setDid(did).build().toByteArray(),
            ),
        )

    /** Create/rebuild a filtered deck; returns its deck id. */
    fun addOrUpdateFilteredDeck(deck: FilteredDeckForUpdate): Long =
        OpChangesWithId.parseFrom(
            run(SVC_DECKS, M_ADD_OR_UPDATE_FILTERED_DECK, deck.toByteArray()),
        ).id

    fun setCurrentDeck(deckId: Long) {
        run(SVC_DECKS, M_SET_CURRENT_DECK, DeckId.newBuilder().setDid(deckId).build().toByteArray())
    }

    fun countsForDeckToday(deckId: Long): CountsForDeckTodayResponse {
        val bytes = run(SVC_SCHEDULER, M_COUNTS_FOR_DECK, DeckId.newBuilder().setDid(deckId).build().toByteArray())
        return CountsForDeckTodayResponse.parseFrom(bytes)
    }

    /** Fetch the next due cards from the shared scheduler (v3 queue). */
    fun getQueuedCards(fetchLimit: Int): QueuedCards {
        val req = GetQueuedCardsRequest.newBuilder()
            .setFetchLimit(fetchLimit)
            .setIntradayLearningOnly(false)
            .build()
        return QueuedCards.parseFrom(run(SVC_SCHEDULER, M_GET_QUEUED_CARDS, req.toByteArray()))
    }

    /** Grade a card, letting the shared FSRS scheduler compute the next state. */
    fun answerCard(answer: CardAnswer) {
        run(SVC_SCHEDULER, M_ANSWER_CARD, answer.toByteArray())
    }

    /** Human interval labels for [again, hard, good, easy] from the engine. */
    fun describeNextStates(states: SchedulingStates): List<String> {
        val bytes = run(SVC_SCHEDULER, M_DESCRIBE_NEXT_STATES, states.toByteArray())
        return StringList.parseFrom(bytes).valsList
    }

    /** Render a card's question and answer HTML through the engine templates. */
    fun renderExistingCard(cardId: Long): RenderCardResponse {
        val req = RenderExistingCardRequest.newBuilder()
            .setCardId(cardId)
            .setBrowser(false)
            .setPartialRender(false)
            .build()
        return RenderCardResponse.parseFrom(run(SVC_CARD_RENDERING, M_RENDER_EXISTING_CARD, req.toByteArray()))
    }

    // --- Speedrun signals -------------------------------------------------

    /** The honest three-signal readiness snapshot (memory, performance, range). */
    fun computeReadiness(): ReadinessSnapshot =
        ReadinessSnapshot.parseFrom(run(SVC_SPEEDRUN, M_COMPUTE_READINESS, ByteArray(0)))

    fun getPerformanceReport(): PerformanceReport =
        PerformanceReport.parseFrom(run(SVC_SPEEDRUN, M_PERFORMANCE_REPORT, ByteArray(0)))

    fun getCoverageReport(): CoverageReport =
        CoverageReport.parseFrom(run(SVC_SPEEDRUN, M_COVERAGE_REPORT, ByteArray(0)))

    fun getCalibrationReport(): CalibrationReport =
        CalibrationReport.parseFrom(run(SVC_SPEEDRUN, M_CALIBRATION_REPORT, ByteArray(0)))

    fun getExamProfile(): ExamProfile =
        ExamProfile.parseFrom(run(SVC_SPEEDRUN, M_GET_EXAM_PROFILE, ByteArray(0)))

    fun setExamProfile(examDateMs: Long, targetScore: Int): ExamProfile {
        val req = ExamProfile.newBuilder()
            .setExamDateMs(examDateMs)
            .setTargetScore(targetScore)
            .build()
        return ExamProfile.parseFrom(run(SVC_SPEEDRUN, M_SET_EXAM_PROFILE, req.toByteArray()))
    }

    fun getExamPlan(): ExamPlan =
        ExamPlan.parseFrom(run(SVC_SPEEDRUN, M_GET_EXAM_PLAN, ByteArray(0)))

    /** Record a graded attempt (with any self-explanation) and get its diagnosis. */
    fun recordAttempt(req: RecordAttemptRequest): RecordAttemptResponse =
        RecordAttemptResponse.parseFrom(run(SVC_SPEEDRUN, M_RECORD_ATTEMPT, req.toByteArray()))

    /** Fetch a batch of held-out practice questions (optionally by topic). */
    fun getPracticeQuestions(limit: Int, topic: String): List<QuestionItem> {
        val req = GetPracticeQuestionsRequest.newBuilder().setLimit(limit).setTopic(topic).build()
        val bytes = run(SVC_SPEEDRUN, M_GET_PRACTICE_QUESTIONS, req.toByteArray())
        return QuestionItems.parseFrom(bytes).itemsList
    }

    /**
     * The end-of-session reasoning round: held-out questions for the concepts
     * just reviewed (card-linked, then topic-matched via the deck-name map, then
     * unseen fallback). Runs in the shared engine, identical to desktop.
     */
    fun getSessionReasoningRound(reviewedCardIds: List<Long>, limit: Int): List<QuestionItem> {
        val req = SessionReasoningRoundRequest.newBuilder()
            .addAllReviewedCardIds(reviewedCardIds)
            .setLimit(limit)
            .build()
        val bytes = run(SVC_SPEEDRUN, M_GET_SESSION_REASONING_ROUND, req.toByteArray())
        return QuestionItems.parseFrom(bytes).itemsList
    }

    /**
     * The engine-scheduled reasoning-due queue (Design 2 / D1): held-out
     * questions for the most-due topics, ranked by reasoning debt
     * (recall-vs-performance gap + uncovered + recency).
     */
    fun getDueReasoning(limit: Int): List<QuestionItem> {
        val req = GetDueReasoningRequest.newBuilder().setLimit(limit).build()
        val bytes = run(SVC_SPEEDRUN, M_GET_DUE_REASONING, req.toByteArray())
        return QuestionItems.parseFrom(bytes).itemsList
    }

    /** The end-of-session feedback report (Design 2 / D2): miss counts by cause + weak topics. */
    fun getFeedbackReport(): FeedbackReport =
        FeedbackReport.parseFrom(run(SVC_SPEEDRUN, M_GET_FEEDBACK_REPORT, ByteArray(0)))

    /** Per-topic counts of the held-out bank, for the MCAT-section Practice landing. */
    /** Seed a labeled sample study history (mature cards + attempts) for demos. */
    fun seedSampleHistory(): SeedSampleHistoryResponse =
        SeedSampleHistoryResponse.parseFrom(
            run(
                SVC_SPEEDRUN,
                M_SEED_SAMPLE_HISTORY,
                SeedSampleHistoryRequest.newBuilder().build().toByteArray(),
            ),
        )

    fun getPracticeBankSummary(): PracticeBankSummary =
        PracticeBankSummary.parseFrom(run(SVC_SPEEDRUN, M_GET_PRACTICE_BANK_SUMMARY, ByteArray(0)))

    /** Per-topic coverage/memory/performance raw counts for the topic dashboard. */
    fun getTopicSignals(): List<TopicSignal> =
        TopicSignalsReport.parseFrom(
            run(SVC_SPEEDRUN, M_GET_TOPIC_SIGNALS, ByteArray(0)),
        ).topicsList

    /**
     * Exam-style attempts grouped by stored topic for unlinked (cardId=0)
     * practice questions, so the section dashboard can fold them into per-section
     * performance even when they have no source card to join through.
     */
    fun getTopicAttemptStats(): List<TopicAttemptStat> =
        TopicAttemptStats.parseFrom(
            run(SVC_SPEEDRUN, M_GET_TOPIC_ATTEMPT_STATS, ByteArray(0)),
        ).statsList

    /** Register one held-out question item; returns its stored id. */
    fun addQuestionItem(item: QuestionItem): Long =
        QuestionItemId.parseFrom(run(SVC_SPEEDRUN, M_ADD_QUESTION_ITEM, item.toByteArray())).id

    /** Sensible default import options from the engine. */
    fun getImportAnkiPackagePresets(): ImportAnkiPackageOptions =
        ImportAnkiPackageOptions.parseFrom(run(SVC_IMPORT_EXPORT, M_GET_IMPORT_PRESETS, ByteArray(0)))

    /** Import an .apkg/.colpkg from a local file path via the shared engine. */
    fun importAnkiPackage(packagePath: String, options: ImportAnkiPackageOptions): ImportResponse {
        val req = ImportAnkiPackageRequest.newBuilder()
            .setPackagePath(packagePath)
            .setOptions(options)
            .build()
        return ImportResponse.parseFrom(run(SVC_IMPORT_EXPORT, M_IMPORT_ANKI_PACKAGE, req.toByteArray()))
    }

    fun getTopicMap(): TopicMap =
        TopicMap.parseFrom(run(SVC_SPEEDRUN, M_GET_TOPIC_MAP, ByteArray(0)))

    /** Replace the coverage topic map (e.g. the 31-category content outline). */
    fun setTopicMap(entries: List<TopicMapEntry>): Int =
        SetTopicMapResponse.parseFrom(
            run(
                SVC_SPEEDRUN,
                M_SET_TOPIC_MAP,
                TopicMap.newBuilder().addAllEntries(entries).build().toByteArray(),
            ),
        ).topics

    fun seedMcatTopicOutline() {
        run(SVC_SPEEDRUN, M_SEED_MCAT_OUTLINE, ByteArray(0))
    }

    /**
     * Note-encoding sync (Phase 5): mirror sr_attempts into hidden "Speedrun
     * Data" notes so exam-practice evidence rides the standard note sync every
     * peer keeps - AnkiWeb and other stock servers drop the custom sr_attempts
     * chunk. Idempotent; shares the exact rslib implementation with desktop.
     */
    fun encodeAttemptsAsNotes() {
        run(SVC_SPEEDRUN, M_ENCODE_ATTEMPTS_AS_NOTES, ByteArray(0))
    }

    /** Decode attempts carried by notes back into sr_attempts (insert-if-absent). */
    fun decodeNotesToAttempts() {
        run(SVC_SPEEDRUN, M_DECODE_NOTES_TO_ATTEMPTS, ByteArray(0))
    }

    // --- Sync (self-hosted collection sync) -------------------------------

    /** Log in to a sync server; returns auth (hkey + endpoint) for later calls. */
    fun syncLogin(username: String, password: String, endpoint: String): SyncAuth {
        val req = SyncLoginRequest.newBuilder()
            .setUsername(username)
            .setPassword(password)
            .setEndpoint(endpoint)
            .build()
        return SyncAuth.parseFrom(run(SVC_SYNC, M_SYNC_LOGIN, req.toByteArray()))
    }

    /**
     * Run a collection sync. The engine performs the incremental (normal) merge
     * in-call; the response's `required` says whether a *full* up/download is
     * still needed (e.g. on a first sync).
     */
    fun syncCollection(auth: SyncAuth): SyncCollectionResponse {
        // Sync media too, so card images (e.g. the MCAT deck's diagrams) reach
        // the phone. The media folder + DB are configured at openCollection.
        val req = SyncCollectionRequest.newBuilder().setAuth(auth).setSyncMedia(true).build()
        return SyncCollectionResponse.parseFrom(run(SVC_SYNC, M_SYNC_COLLECTION, req.toByteArray()))
    }

    /** Whole-collection upload (seed the server) or download (mirror it). */
    fun fullUploadOrDownload(auth: SyncAuth, upload: Boolean) {
        val req = FullUploadOrDownloadRequest.newBuilder().setAuth(auth).setUpload(upload).build()
        run(SVC_SYNC, M_FULL_UPLOAD_OR_DOWNLOAD, req.toByteArray())
    }

    fun abortSync() {
        run(SVC_SYNC, M_ABORT_SYNC, ByteArray(0))
    }

    // --- Lifecycle --------------------------------------------------------

    fun close() {
        if (ptr != 0L) {
            NativeBackend.closeBackend(ptr)
            ptr = 0
        }
    }

    private fun run(service: Int, method: Int, input: ByteArray): ByteArray {
        check(ptr != 0L) { "backend is closed" }
        val tagged = NativeBackend.runMethod(ptr, service, method, input)
        check(tagged.isNotEmpty()) { "empty response from engine (service=$service method=$method)" }
        val payload = tagged.copyOfRange(1, tagged.size)
        if (tagged[0].toInt() == 0) {
            val err = runCatching { BackendError.parseFrom(payload) }.getOrNull()
            throw BackendException(
                err?.kind?.name ?: "UNKNOWN",
                err?.message?.takeIf { it.isNotBlank() }
                    ?: "backend error (service=$service method=$method)",
            )
        }
        return payload
    }

    companion object {
        // (service, method) indices from out/pylib/anki/_backend_generated.py.
        private const val SVC_COLLECTION = 3
        private const val M_OPEN_COLLECTION = 0
        private const val M_CLOSE_COLLECTION = 1

        // BackendConfigService (service index 9): GetConfigJson / SetConfigJson.
        private const val SVC_CONFIG = 9
        private const val M_GET_CONFIG_JSON = 0
        private const val M_SET_CONFIG_JSON = 1

        private const val SVC_DECKS = 7
        private const val M_DECK_TREE = 4
        private const val M_SET_CURRENT_DECK = 22
        private const val M_GET_OR_CREATE_FILTERED_DECK = 19
        private const val M_ADD_OR_UPDATE_FILTERED_DECK = 20

        private const val SVC_SCHEDULER = 13
        private const val M_GET_QUEUED_CARDS = 3
        private const val M_ANSWER_CARD = 4
        private const val M_COUNTS_FOR_DECK = 10
        private const val M_DESCRIBE_NEXT_STATES = 24

        private const val SVC_CARD_RENDERING = 27
        private const val M_RENDER_EXISTING_CARD = 6

        private const val SVC_IMPORT_EXPORT = 39
        private const val M_IMPORT_ANKI_PACKAGE = 2
        private const val M_GET_IMPORT_PRESETS = 3

        private const val SVC_SPEEDRUN = 43
        private const val M_RECORD_ATTEMPT = 0
        private const val M_COMPUTE_READINESS = 3
        private const val M_ADD_QUESTION_ITEM = 5
        private const val M_PERFORMANCE_REPORT = 7
        private const val M_SET_TOPIC_MAP = 8
        private const val M_GET_TOPIC_MAP = 9
        private const val M_SEED_MCAT_OUTLINE = 10
        private const val M_COVERAGE_REPORT = 11
        private const val M_CALIBRATION_REPORT = 12
        private const val M_SET_EXAM_PROFILE = 14
        private const val M_GET_EXAM_PROFILE = 15
        private const val M_GET_EXAM_PLAN = 16
        private const val M_GET_PRACTICE_QUESTIONS = 21
        private const val M_GET_SESSION_REASONING_ROUND = 22
        private const val M_GET_DUE_REASONING = 23
        private const val M_GET_FEEDBACK_REPORT = 24
        private const val M_GET_PRACTICE_BANK_SUMMARY = 25
        private const val M_SEED_SAMPLE_HISTORY = 26
        private const val M_GET_TOPIC_SIGNALS = 27
        private const val M_GET_TOPIC_ATTEMPT_STATS = 28
        private const val M_ENCODE_ATTEMPTS_AS_NOTES = 29
        private const val M_DECODE_NOTES_TO_ATTEMPTS = 30

        // BackendSyncService (service index 1) - see out/pylib/anki/_backend_generated.py.
        private const val SVC_SYNC = 1
        private const val M_SYNC_LOGIN = 3
        private const val M_SYNC_COLLECTION = 5
        private const val M_FULL_UPLOAD_OR_DOWNLOAD = 6
        private const val M_ABORT_SYNC = 7

        /** Open the shared engine. Defaults are fine for an on-device client. */
        fun open(): AnkiBackend {
            val ptr = NativeBackend.openBackend(ByteArray(0))
            check(ptr != 0L) { "failed to open the Speedrun engine" }
            return AnkiBackend(ptr)
        }

        /**
         * Join rendered template nodes into an HTML fragment. A full
         * (non-partial) render resolves standard field filters, so text nodes
         * are literal HTML and replacement nodes carry their final text.
         */
        fun nodesToHtml(nodes: List<RenderedTemplateNode>): String {
            val sb = StringBuilder()
            for (node in nodes) {
                when (node.valueCase) {
                    RenderedTemplateNode.ValueCase.TEXT -> sb.append(node.text)
                    RenderedTemplateNode.ValueCase.REPLACEMENT ->
                        sb.append(node.replacement.currentText)
                    else -> {}
                }
            }
            return sb.toString()
        }
    }
}
