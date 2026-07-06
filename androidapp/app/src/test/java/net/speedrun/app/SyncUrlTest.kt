// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncUrlTest {
    @Test
    fun rejectsMissingPort() {
        assertFalse(SyncUrl.isValid("http://127.0.0.1:"))
        assertFalse(SyncUrl.isValid("http://127.0.0.1:/"))
        assertFalse(SyncUrl.isValid(""))
    }

    @Test
    fun acceptsUsbLoopback() {
        assertTrue(SyncUrl.isValid("http://127.0.0.1:55413/"))
        assertTrue(SyncUrl.isValid("http://127.0.0.1:55413"))
    }

    @Test
    fun acceptsAnkiWebDefaultPort() {
        // AnkiWeb (and any public host) uses the default port - no explicit port.
        assertTrue(SyncUrl.isValid(SyncUrl.ANKIWEB_ENDPOINT))
        assertTrue(SyncUrl.isValid("https://sync.ankiweb.net"))
        assertTrue(SyncUrl.isValid("https://sync.ankiweb.net/"))
    }
}
