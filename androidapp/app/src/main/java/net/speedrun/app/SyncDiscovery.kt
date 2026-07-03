// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Handler
import android.os.Looper

/**
 * Finds the desktop's embedded sync server on the LAN via mDNS/Bonjour (the
 * desktop advertises `_speedrun-sync._tcp`), so a paired phone re-discovers the
 * desktop after its IP changes without re-scanning the QR. Only the address is
 * discovered; the credential still comes from the original pairing.
 */
object SyncDiscovery {
    private const val TYPE = "_speedrun-sync._tcp."

    /**
     * Discover the server and call [onFound] on the main thread with
     * `http://host:port` for the first match, then stop. No-op if nothing is
     * found within [timeoutMs].
     */
    @Suppress("DEPRECATION") // resolveService is fine down to our minSdk (26)
    fun findServer(context: Context, timeoutMs: Long = 4000L, onFound: (String) -> Unit) {
        val nsd = context.applicationContext.getSystemService(Context.NSD_SERVICE)
            as? NsdManager ?: return
        val main = Handler(Looper.getMainLooper())
        var done = false
        var discovery: NsdManager.DiscoveryListener? = null

        fun stop() {
            discovery?.let { runCatching { nsd.stopServiceDiscovery(it) } }
            discovery = null
        }

        val resolveListener = object : NsdManager.ResolveListener {
            override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {}
            override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                if (done) return
                val host = serviceInfo.host?.hostAddress ?: return
                done = true
                main.post { onFound("http://$host:${serviceInfo.port}") }
                stop()
            }
        }

        discovery = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {}
            override fun onDiscoveryStopped(serviceType: String) {}
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onServiceLost(serviceInfo: NsdServiceInfo) {}
            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                if (!done && serviceInfo.serviceType.contains("speedrun-sync")) {
                    runCatching { nsd.resolveService(serviceInfo, resolveListener) }
                }
            }
        }

        runCatching { nsd.discoverServices(TYPE, NsdManager.PROTOCOL_DNS_SD, discovery) }
        main.postDelayed({ stop() }, timeoutMs)
    }
}
