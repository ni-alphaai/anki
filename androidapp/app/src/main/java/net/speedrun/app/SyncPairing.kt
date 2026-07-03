// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import org.json.JSONObject

/**
 * The pairing payload the desktop encodes in its "Sync with phone" QR code:
 * `{"v":1,"url":"http://<lan-ip>:<port>","user":"speedrun","token":"<hex>"}`.
 * Scanning it is all the phone needs to sync -- no typing.
 */
data class SyncPairing(val url: String, val user: String, val token: String) {
    companion object {
        fun parse(text: String): SyncPairing? = runCatching {
            val o = JSONObject(text)
            val url = o.optString("url").trim()
            val user = o.optString("user").trim()
            val token = o.optString("token").trim()
            if (url.isBlank() || user.isBlank() || token.isBlank()) {
                null
            } else {
                SyncPairing(url, user, token)
            }
        }.getOrNull()
    }
}
