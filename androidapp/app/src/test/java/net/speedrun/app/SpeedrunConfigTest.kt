// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Host-JVM tests for the synced-settings contract ([SpeedrunConfig]). The
 * behavioral preferences converge across devices only if the phone reads and
 * writes the collection config in the EXACT JSON shape the desktop uses in
 * qt/aqt/speedrun.py. These round-trip and pin the wire encoding so a drift
 * from the desktop keys/values fails the build rather than silently breaking
 * cross-device sync. Pure JSON, no engine or device needed.
 */
class SpeedrunConfigTest {
    // --- Config keys must equal the desktop's (qt/aqt/speedrun.py) ---

    @Test
    fun configKeysMatchDesktop() {
        assertEquals("speedrunAutoReasoningRound", SpeedrunConfig.KEY_AUTO_ROUND)
        assertEquals("speedrunDelayedFeedbackExperiment", SpeedrunConfig.KEY_DELAYED_FB)
        assertEquals("speedrunSyncConflictPolicy", SpeedrunConfig.KEY_SYNC_CONFLICT)
        assertEquals("speedrunDiagnosticDone", SpeedrunConfig.KEY_DIAGNOSTIC)
    }

    // --- Bool preferences round-trip through the JSON config value ---

    @Test
    fun boolRoundTripsThroughConfig() {
        // Write true -> config JSON -> read back true (the exact path a toggle
        // takes: encode into config, then a later refresh decodes it).
        val stored = SpeedrunConfig.encodeBool(true)
        assertEquals("true", stored)
        assertTrue(SpeedrunConfig.decodeBool(stored, default = false))

        val storedOff = SpeedrunConfig.encodeBool(false)
        assertEquals("false", storedOff)
        assertFalse(SpeedrunConfig.decodeBool(storedOff, default = true))
    }

    @Test
    fun boolDecodeReadsDesktopWrittenValues() {
        // The desktop's bool config serializes as bare JSON `true`/`false`.
        assertTrue(SpeedrunConfig.decodeBool("true", default = false))
        assertFalse(SpeedrunConfig.decodeBool("false", default = true))
    }

    @Test
    fun boolDecodeFallsBackWhenUnsetOrGarbage() {
        // Unset key (null) or an unexpected value keeps the documented default,
        // so a missing/corrupt config never crashes and never flips a setting.
        assertTrue(SpeedrunConfig.decodeBool(null, default = true))
        assertFalse(SpeedrunConfig.decodeBool(null, default = false))
        assertTrue(SpeedrunConfig.decodeBool("\"nonsense\"", default = true))
    }

    // --- Sync-conflict policy uses the desktop's exact string encoding ---

    @Test
    fun conflictPolicyStringsMatchDesktop() {
        assertEquals("ask", SpeedrunConfig.policyToString(SyncConflictPolicy.Ask))
        assertEquals("phone", SpeedrunConfig.policyToString(SyncConflictPolicy.PreferPhone))
        assertEquals("desktop", SpeedrunConfig.policyToString(SyncConflictPolicy.PreferDesktop))
    }

    @Test
    fun conflictPolicyEncodesAsQuotedJsonString() {
        // The desktop stores the policy as a JSON string, e.g. `"ask"` (quoted),
        // not a bare token - so both platforms parse the same bytes.
        assertEquals("\"ask\"", SpeedrunConfig.encodeConflictPolicy(SyncConflictPolicy.Ask))
        assertEquals("\"phone\"", SpeedrunConfig.encodeConflictPolicy(SyncConflictPolicy.PreferPhone))
        assertEquals("\"desktop\"", SpeedrunConfig.encodeConflictPolicy(SyncConflictPolicy.PreferDesktop))
    }

    @Test
    fun conflictPolicyRoundTripsThroughConfig() {
        for (policy in SyncConflictPolicy.values()) {
            val stored = SpeedrunConfig.encodeConflictPolicy(policy)
            assertEquals(policy, SpeedrunConfig.decodeConflictPolicy(stored))
        }
    }

    @Test
    fun conflictPolicyDecodesDesktopWrittenString() {
        // A desktop that wrote the raw JSON string is read identically here.
        assertEquals(SyncConflictPolicy.Ask, SpeedrunConfig.decodeConflictPolicy("\"ask\""))
        assertEquals(SyncConflictPolicy.PreferPhone, SpeedrunConfig.decodeConflictPolicy("\"phone\""))
        assertEquals(SyncConflictPolicy.PreferDesktop, SpeedrunConfig.decodeConflictPolicy("\"desktop\""))
    }

    @Test
    fun conflictPolicyFallsBackToAskWhenUnsetOrUnknown() {
        assertEquals(SyncConflictPolicy.Ask, SpeedrunConfig.decodeConflictPolicy(null))
        assertEquals(SyncConflictPolicy.Ask, SpeedrunConfig.decodeConflictPolicy("\"bogus\""))
    }
}
