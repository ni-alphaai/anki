// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import org.json.JSONObject

/**
 * The pairing payload the desktop encodes in its "Sync with phone" QR code:
 * `{"v":1,"url":"http://<lan-ip>:<port>","usb_url":"http://127.0.0.1:<port>/",
 * "user":"speedrun","token":"<hex>","exp":<epoch-ms>}`.
 * Scanning it is all the phone needs to sync -- no typing. `exp` is a short-lived
 * deadline (epoch millis) so a stale screenshot of the code cannot be used later.
 */
data class SyncPairing(
    val url: String,
    val user: String,
    val token: String,
    val usbUrl: String = "",
    val expiresAtMs: Long = 0L,
) {
    /** Pick the USB loopback URL when requested and present. */
    fun resolveUrl(preferUsb: Boolean): String {
        if (preferUsb && usbUrl.isNotBlank()) return usbUrl
        return url
    }

    /**
     * True once the desktop-stamped expiry has passed. A zero/absent `exp` means
     * "no expiry" (older desktops, manual entry), so those are never rejected.
     */
    fun isExpired(nowMs: Long = System.currentTimeMillis()): Boolean =
        expiresAtMs in 1 until nowMs

    companion object {
        fun parse(text: String): SyncPairing? = runCatching {
            val o = JSONObject(text)
            val url = o.optString("url").trim()
            val usbUrl = o.optString("usb_url").trim()
            val user = o.optString("user").trim()
            val token = o.optString("token").trim()
            val exp = o.optLong("exp", 0L)
            if (url.isBlank() || user.isBlank() || token.isBlank()) {
                null
            } else {
                SyncPairing(url, user, token, usbUrl, exp)
            }
        }.getOrNull()
    }
}
