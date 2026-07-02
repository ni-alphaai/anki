// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

/**
 * JNI surface onto the shared Anki/Speedrun Rust engine (see ../../rsandroid).
 *
 * This mirrors the desktop client: every call goes through the same protobuf
 * service boundary (`runMethod(service, method, input)`), so the phone runs the
 * exact same Rust core - including the Speedrun diagnostic engine and the
 * points-at-stake queue.
 */
object NativeBackend {
    init {
        System.loadLibrary("rsandroid")
    }

    /** Open a backend from an encoded `anki.backend.BackendInit` message. */
    external fun openBackend(initMsg: ByteArray): Long

    /**
     * Run a protobuf service method by its generated service/method index.
     *
     * The result is status-tagged: byte 0 is `1` (success, followed by the
     * encoded response) or `0` (failure, followed by an encoded
     * `anki.backend.BackendError`). [AnkiBackend] unwraps this tag.
     */
    external fun runMethod(ptr: Long, service: Int, method: Int, input: ByteArray): ByteArray

    /** Free a backend handle. The handle must not be used afterwards. */
    external fun closeBackend(ptr: Long)
}
