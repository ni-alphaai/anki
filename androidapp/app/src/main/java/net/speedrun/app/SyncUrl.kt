// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import java.net.URI

/** Validate and normalize self-hosted sync server URLs (mirrors desktop guards). */
object SyncUrl {
    fun normalize(url: String): String {
        var u = url.trim()
        if (!u.startsWith("http://") && !u.startsWith("https://")) {
            u = "http://$u"
        }
        if (!u.endsWith("/")) u += "/"
        return u
    }

    /**
     * True when [url] has a real host. Rejects a dangling ":" with no port (the
     * stale embedded-server case, ``http://127.0.0.1:``), but accepts either an
     * explicit port (self-hosted server) OR a public host on its default port
     * (AnkiWeb, ``https://sync.ankiweb.net``).
     */
    fun isValid(url: String): Boolean {
        if (url.isBlank()) return false
        return try {
            val n = normalize(url)
            val uri = URI(n)
            if (uri.host.isNullOrBlank()) return false
            // Authority is everything between "://" and the first "/"; a trailing
            // ":" there means an empty port (invalid), e.g. "127.0.0.1:".
            val authority = n.substringAfter("://").substringBefore("/")
            !authority.endsWith(":")
        } catch (_: Exception) {
            false
        }
    }

    /** AnkiWeb's default sync endpoint (login resolves the account's shard). */
    const val ANKIWEB_ENDPOINT = "https://sync.ankiweb.net"
}
