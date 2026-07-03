// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import anki.backend.BackendError
import anki.card_rendering.RenderCardResponse
import anki.card_rendering.RenderExistingCardRequest
import anki.card_rendering.RenderedTemplateNode
import anki.collection.OpenCollectionRequest
import anki.decks.DeckId
import anki.decks.DeckTreeNode
import anki.decks.DeckTreeRequest
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
import anki.speedrun.QuestionItem
import anki.speedrun.QuestionItemId
import anki.speedrun.QuestionItems
import anki.speedrun.ReadinessSnapshot
import anki.speedrun.RecordAttemptRequest
import anki.speedrun.RecordAttemptResponse
import anki.speedrun.SessionReasoningRoundRequest
import anki.speedrun.TopicMap
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

    // --- Decks / scheduler ------------------------------------------------

    /** The full deck tree with counts (new/learn/review) after limits. */
    fun deckTree(): DeckTreeNode {
        val req = DeckTreeRequest.newBuilder()
            .setNow(System.currentTimeMillis() / 1000)
            .build()
        return DeckTreeNode.parseFrom(run(SVC_DECKS, M_DECK_TREE, req.toByteArray()))
    }

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

    fun seedMcatTopicOutline() {
        run(SVC_SPEEDRUN, M_SEED_MCAT_OUTLINE, ByteArray(0))
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
        val req = SyncCollectionRequest.newBuilder().setAuth(auth).setSyncMedia(false).build()
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

        private const val SVC_DECKS = 7
        private const val M_DECK_TREE = 4
        private const val M_SET_CURRENT_DECK = 22

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
