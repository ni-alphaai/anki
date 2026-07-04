// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The QR pairing payload parser must accept the desktop's payload and reject
 *  anything malformed, so a bad scan never half-configures sync. */
class SyncPairingTest {
    @Test
    fun parsesDesktopPayload() {
        val p = SyncPairing.parse(
            """{"v":1,"url":"http://192.168.1.20:57539","user":"speedrun","token":"9f2c1ab4"}""",
        )
        assertEquals(SyncPairing("http://192.168.1.20:57539", "speedrun", "9f2c1ab4"), p)
    }

    @Test
    fun prefersUsbUrlWhenRequested() {
        val p = SyncPairing.parse(
            """{"v":1,"url":"http://192.168.1.20:57539","usb_url":"http://127.0.0.1:55413/","user":"speedrun","token":"tok"}""",
        )!!
        assertEquals("http://127.0.0.1:55413/", p.resolveUrl(preferUsb = true))
        assertEquals("http://192.168.1.20:57539", p.resolveUrl(preferUsb = false))
    }

    @Test
    fun trimsWhitespace() {
        val p = SyncPairing.parse("""{"url":" http://x:1 ","user":" speedrun ","token":" tok "}""")
        assertEquals(SyncPairing("http://x:1", "speedrun", "tok"), p)
    }

    @Test
    fun rejectsMissingToken() {
        assertNull(SyncPairing.parse("""{"url":"http://x:1","user":"speedrun"}"""))
    }

    @Test
    fun rejectsBlankField() {
        assertNull(SyncPairing.parse("""{"url":"","user":"speedrun","token":"tok"}"""))
    }

    @Test
    fun rejectsNonJson() {
        assertNull(SyncPairing.parse("just some text"))
    }
}
