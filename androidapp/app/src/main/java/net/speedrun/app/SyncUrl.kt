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

    /** True when [url] has a host and explicit port (rejects ``http://127.0.0.1:``). */
    fun isValid(url: String): Boolean {
        if (url.isBlank()) return false
        return try {
            val uri = URI(normalize(url))
            !uri.host.isNullOrBlank() && uri.port > 0
        } catch (_: Exception) {
            false
        }
    }
}
