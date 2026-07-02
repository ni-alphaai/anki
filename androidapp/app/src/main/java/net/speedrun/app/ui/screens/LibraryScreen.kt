// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui.screens

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import net.speedrun.app.CatalogDeck
import net.speedrun.app.EngineRepository
import net.speedrun.app.POPULAR_DECKS
import net.speedrun.app.ui.KeyValueRow
import net.speedrun.app.ui.PrimaryButton
import net.speedrun.app.ui.SecondaryButton
import net.speedrun.app.ui.SectionLabel
import net.speedrun.app.ui.SpeedrunCard
import net.speedrun.app.ui.theme.Display
import net.speedrun.app.ui.theme.Radius
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import java.io.File
import java.net.CookieHandler
import java.net.CookieManager
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * Library: the one place to get content onto the phone. Pick a popular deck for a
 * one-tap download + import (Google Drive links are resolved past the virus-scan
 * warning), add the bundled MMLU pack, paste any direct link, or pick a file -
 * plus a status card that makes a media-less collection obvious.
 */
@Composable
fun LibraryScreen() {
    val c = Speedrun.colors
    val scope = rememberCoroutineScope()
    var decks by remember { mutableStateOf<Int?>(null) }
    var questions by remember { mutableStateOf<Int?>(null) }
    var media by remember { mutableIntStateOf(0) }

    suspend fun refresh() {
        decks = runCatching { EngineRepository.deckTree().size }.getOrNull()
        questions = runCatching { EngineRepository.questionCount() }.getOrNull()
        media = EngineRepository.mediaFileCount()
    }
    LaunchedEffect(Unit) { refresh() }

    Column(
        Modifier.fillMaxSize().background(c.background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.l),
    ) {
        Spacer(Modifier.height(Space.s))
        Text("Library", color = c.textPrimary, fontFamily = Display, fontSize = 34.sp, fontWeight = FontWeight.Bold)
        Text(
            "Add your deck and practice questions \u2014 right here, no computer needed.",
            color = c.textSecondary, fontSize = 15.sp, modifier = Modifier.padding(top = Space.xs),
        )
        Spacer(Modifier.height(Space.l))

        SectionLabel("In your collection")
        SpeedrunCard {
            KeyValueRow("Decks", decks?.toString() ?: "\u2026")
            KeyValueRow("Practice questions", questions?.toString() ?: "\u2026")
            KeyValueRow(
                "Card images",
                if (media > 0) "$media files" else "None yet",
                valueColor = if (media > 0) c.readinessGood else c.readinessWarn,
            )
            if (media == 0) {
                Text(
                    "No images yet \u2014 import a deck below to restore card pictures.",
                    color = c.textTertiary, fontSize = 13.sp, modifier = Modifier.padding(top = Space.s),
                )
            }
        }
        Spacer(Modifier.height(Space.xxl))

        ImportPanel(onImported = { scope.launch { refresh() } })
        Spacer(Modifier.height(Space.xxl))
    }
}

/**
 * Reusable import actions (shared by Library and the first-run Get started
 * screen): a popular-deck catalog, a paste-a-link field, a file picker, and the
 * bundled MMLU pack, with a busy/progress card for large downloads.
 */
@Composable
fun ImportPanel(onImported: () -> Unit) {
    val c = Speedrun.colors
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    var progress by remember { mutableStateOf<Float?>(null) }
    var link by remember { mutableStateOf("") }

    fun runImport(label: String, block: suspend () -> String) {
        if (busy) return
        busy = true
        status = label
        progress = null
        scope.launch {
            status = runCatching { block() }.getOrElse { "Failed: ${it.message}" }
            progress = null
            busy = false
            onImported()
        }
    }

    fun downloadAndImport(url: String, label: String) = runImport(label) {
        val (path, name) = withContext(Dispatchers.IO) {
            downloadWithProgress(context, url) { f -> progress = f }
        }
        status = "Importing \u2014 almost there\u2026"
        progress = null
        val summary = EngineRepository.importLocalFile(path, name)
        withContext(Dispatchers.IO) { runCatching { File(path).delete() } }
        summary
    }

    val filePicker = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri: Uri? ->
        if (uri != null) {
            runImport("Importing file\u2026") {
                val (path, name) = withContext(Dispatchers.IO) { copyToCache(context, uri) }
                EngineRepository.importLocalFile(path, name)
            }
        }
    }

    Column {
        if (busy || status.isNotBlank()) {
            SpeedrunCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (busy) {
                        CircularProgressIndicator(
                            color = c.accent,
                            strokeWidth = 2.dp,
                            modifier = Modifier.size(18.dp).padding(end = Space.s),
                        )
                    }
                    Text(status.ifBlank { "Working\u2026" }, color = c.textPrimary, fontSize = 15.sp)
                }
                progress?.let { p ->
                    Spacer(Modifier.height(Space.s))
                    LinearProgressIndicator(
                        progress = { p.coerceIn(0f, 1f) },
                        modifier = Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(Radius.pill)),
                        color = c.accent,
                        trackColor = c.separator,
                    )
                    Text(
                        "${(p * 100).toInt()}%",
                        color = c.textTertiary, fontSize = 13.sp,
                        modifier = Modifier.padding(top = Space.xs),
                    )
                }
            }
            Spacer(Modifier.height(Space.l))
        }

        SectionLabel("Popular decks")
        POPULAR_DECKS.forEach { deck ->
            PopularDeckCard(deck = deck, enabled = !busy) {
                downloadAndImport(deck.url, "Downloading ${deck.name}\u2026")
            }
            Spacer(Modifier.height(Space.m))
        }
        Spacer(Modifier.height(Space.l))

        SectionLabel("Practice questions")
        SpeedrunCard {
            Text(
                "Open-licensed college science + medicine MCQs (MMLU, MIT). Feeds the performance signal separately from recall.",
                color = c.textSecondary, fontSize = 15.sp,
            )
            Spacer(Modifier.height(Space.m))
            PrimaryButton("Add MMLU pack", enabled = !busy) {
                runImport("Adding MMLU questions\u2026") {
                    val n = EngineRepository.importMmluAsset(context)
                    "Added $n MMLU practice questions"
                }
            }
        }
        Spacer(Modifier.height(Space.xxl))

        SectionLabel("Import your own")
        SpeedrunCard {
            Text(
                "Paste a direct link to any Anki deck (.apkg / .colpkg) or question pack (.json). Google Drive share links work.",
                color = c.textSecondary, fontSize = 15.sp,
            )
            Spacer(Modifier.height(Space.m))
            OutlinedTextField(
                value = link,
                onValueChange = { link = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Deck or pack link") },
                singleLine = true,
            )
            Spacer(Modifier.height(Space.s))
            PrimaryButton("Download & import", enabled = !busy && link.startsWith("http")) {
                downloadAndImport(link.trim(), "Downloading\u2026")
            }
            Spacer(Modifier.height(Space.s))
            SecondaryButton("Choose a file instead", enabled = !busy) {
                filePicker.launch(arrayOf("*/*"))
            }
        }
    }
}

@Composable
private fun PopularDeckCard(deck: CatalogDeck, enabled: Boolean, onImport: () -> Unit) {
    val c = Speedrun.colors
    SpeedrunCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                Icons.Filled.CloudDownload,
                contentDescription = null,
                tint = c.accent,
                modifier = Modifier.size(28.dp).padding(end = Space.m),
            )
            Column(Modifier.weight(1f)) {
                Text(deck.name, color = c.textPrimary, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                Text(deck.section, color = c.textSecondary, fontSize = 14.sp)
                Text(deck.sizeLabel, color = c.textTertiary, fontSize = 13.sp, modifier = Modifier.padding(top = Space.xs))
            }
        }
        Spacer(Modifier.height(Space.m))
        PrimaryButton("Download & import", enabled = enabled, onClick = onImport)
    }
}

private fun copyToCache(context: Context, uri: Uri): Pair<String, String> {
    val name = displayName(context, uri)
    val out = File(context.cacheDir, sanitize(name))
    context.contentResolver.openInputStream(uri)?.use { input ->
        out.outputStream().use { input.copyTo(it) }
    } ?: error("Couldn't open the selected file")
    return out.absolutePath to name
}

/**
 * Stream a (possibly large) download to files dir, reporting 0..1 progress.
 * Resolves Google Drive share links to a direct download and follows Drive's
 * virus-scan confirmation page when it appears.
 */
private fun downloadWithProgress(
    context: Context,
    urlStr: String,
    onProgress: (Float) -> Unit,
): Pair<String, String> {
    if (CookieHandler.getDefault() == null) CookieHandler.setDefault(CookieManager())
    var conn = openConn(resolveDownloadUrl(urlStr))
    // Drive occasionally still returns the HTML "can't scan for viruses" page;
    // follow its confirm form once to get the real bytes.
    if ((conn.contentType ?: "").contains("text/html", ignoreCase = true)) {
        val html = conn.inputStream.bufferedReader().use { it.readText() }
        conn.disconnect()
        val confirmed = parseGoogleConfirm(html)
            ?: throw java.io.IOException("This link needs a manual confirmation step")
        conn = openConn(confirmed)
    }
    val total = conn.contentLengthLong
    val name = guessName(conn, urlStr)
    val out = File(context.filesDir, sanitize(name))
    try {
        conn.inputStream.use { input ->
            out.outputStream().use { output ->
                val buf = ByteArray(64 * 1024)
                var done = 0L
                var lastPct = -1
                while (true) {
                    val read = input.read(buf)
                    if (read < 0) break
                    output.write(buf, 0, read)
                    done += read
                    if (total > 0) {
                        val pct = ((done * 100) / total).toInt()
                        if (pct != lastPct) {
                            lastPct = pct
                            onProgress(done.toFloat() / total)
                        }
                    }
                }
            }
        }
    } finally {
        conn.disconnect()
    }
    return out.absolutePath to name
}

private fun openConn(url: String): HttpURLConnection =
    (URL(url).openConnection() as HttpURLConnection).apply {
        instanceFollowRedirects = true
        connectTimeout = 30_000
        readTimeout = 60_000
        setRequestProperty("User-Agent", "Mozilla/5.0 (Android) Speedrun")
        connect()
    }

/** Turn a Google Drive share/view link into a direct, warning-skipping download. */
private fun resolveDownloadUrl(url: String): String {
    val id = driveFileId(url) ?: return url
    return "https://drive.usercontent.google.com/download?id=$id&export=download&confirm=t"
}

private fun driveFileId(url: String): String? {
    if (!url.contains("drive.google.com") && !url.contains("drive.usercontent.google.com")) return null
    Regex("/d/([A-Za-z0-9_-]{10,})").find(url)?.let { return it.groupValues[1] }
    Regex("[?&]id=([A-Za-z0-9_-]{10,})").find(url)?.let { return it.groupValues[1] }
    return null
}

/** Best-effort parse of Drive's confirmation page into a direct download URL. */
private fun parseGoogleConfirm(html: String): String? {
    Regex("href=\"(/uc\\?export=download[^\"]+)\"").find(html)?.let {
        return "https://drive.google.com" + it.groupValues[1].replace("&amp;", "&")
    }
    val action = Regex("action=\"([^\"]+)\"").find(html)?.groupValues?.get(1)?.replace("&amp;", "&")
        ?: return null
    if (!action.startsWith("http")) return null
    val params = Regex("name=\"([^\"]+)\"\\s+value=\"([^\"]*)\"").findAll(html)
        .joinToString("&") { m ->
            "${m.groupValues[1]}=${URLEncoder.encode(m.groupValues[2], "UTF-8")}"
        }
    return if (params.isBlank()) action else "$action?$params"
}

private fun guessName(conn: HttpURLConnection, urlStr: String): String {
    conn.getHeaderField("Content-Disposition")?.let { cd ->
        Regex("filename=\"?([^\";]+)\"?").find(cd)?.groupValues?.getOrNull(1)?.let { return it }
    }
    val fromUrl = urlStr.substringAfterLast('/').substringBefore('?')
    return fromUrl.ifBlank { "deck.apkg" }
}

/** Keep a downloaded/derived filename safe for the files dir. */
private fun sanitize(name: String): String =
    name.replace('/', '_').replace('\\', '_').ifBlank { "import.dat" }

private fun displayName(context: Context, uri: Uri): String {
    var name = "import.dat"
    context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
        val idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
        if (idx >= 0 && cursor.moveToFirst()) {
            cursor.getString(idx)?.let { name = it }
        }
    }
    return name
}
