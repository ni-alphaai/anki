// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import net.speedrun.app.ui.theme.Radius
import net.speedrun.app.ui.theme.Space
import net.speedrun.app.ui.theme.Speedrun
import java.util.Locale

/** Thin wrapper over Android's on-device SpeechRecognizer with live partials. */
class SpeechController(private val context: Context) {
    private var recognizer: SpeechRecognizer? = null

    var onPartial: (String) -> Unit = {}
    var onFinal: (String) -> Unit = {}
    var onError: (String) -> Unit = {}
    var onReady: () -> Unit = {}
    var onEnd: () -> Unit = {}

    fun available(): Boolean =
        runCatching { SpeechRecognizer.isRecognitionAvailable(context) }.getOrDefault(false)

    fun start() {
        val rec = create()
        if (rec == null) {
            onError("Voice recognition unavailable on this device")
            return
        }
        recognizer = rec
        rec.setRecognitionListener(listener)
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault())
        }
        runCatching { rec.startListening(intent) }.onFailure { onError(it.message ?: "Couldn't start mic") }
    }

    fun stop() {
        runCatching { recognizer?.stopListening() }
    }

    fun destroy() {
        runCatching { recognizer?.destroy() }
        recognizer = null
    }

    private fun create(): SpeechRecognizer? = runCatching {
        if (Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context)) {
            SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        } else if (SpeechRecognizer.isRecognitionAvailable(context)) {
            SpeechRecognizer.createSpeechRecognizer(context)
        } else {
            null
        }
    }.getOrNull()

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) = onReady()
        override fun onBeginningOfSpeech() {}
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() {}
        override fun onError(error: Int) {
            onError(errorText(error))
            onEnd()
        }
        override fun onResults(results: Bundle?) {
            onFinal(firstResult(results))
            onEnd()
        }
        override fun onPartialResults(partialResults: Bundle?) {
            val t = firstResult(partialResults)
            if (t.isNotBlank()) onPartial(t)
        }
        override fun onEvent(eventType: Int, params: Bundle?) {}
    }

    private fun firstResult(b: Bundle?): String =
        b?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()

    private fun errorText(code: Int): String = when (code) {
        SpeechRecognizer.ERROR_NO_MATCH -> "Didn't catch that \u2014 try again"
        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech heard"
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Mic permission needed"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Recognizer busy \u2014 try again"
        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Network error"
        SpeechRecognizer.ERROR_AUDIO -> "Audio error"
        else -> "Voice error"
    }
}

/**
 * Pre-reveal self-explanation capture: voice-first with live transcription and a
 * text fallback. Mirrors the desktop reviewer's on-device self-explanation.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun VoiceExplainSheet(
    initial: String,
    onDismiss: () -> Unit,
    onCapture: (String) -> Unit,
) {
    val c = Speedrun.colors
    val context = LocalContext.current
    val controller = remember { SpeechController(context) }
    val voiceAvailable = remember { controller.available() }

    var text by remember { mutableStateOf(initial) }
    var partial by remember { mutableStateOf("") }
    var listening by remember { mutableStateOf(false) }
    var status by remember {
        mutableStateOf(
            if (voiceAvailable) "Tap the mic and say why you think what you think" else "Voice unavailable \u2014 type your reasoning",
        )
    }

    LaunchedEffect(Unit) {
        controller.onReady = { listening = true; status = "Listening\u2026" }
        controller.onPartial = { partial = it }
        controller.onFinal = { finalText ->
            text = listOf(text, finalText).filter { it.isNotBlank() }.joinToString(" ").trim()
            partial = ""
            listening = false
            status = "Captured \u2014 tap the mic to add more, or Save"
        }
        controller.onError = { msg -> partial = ""; listening = false; status = msg }
        controller.onEnd = { listening = false }
    }
    DisposableEffect(Unit) { onDispose { controller.destroy() } }

    val micPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) controller.start() else status = "Mic permission denied \u2014 type instead"
    }

    fun toggleMic() {
        if (listening) {
            controller.stop()
            listening = false
            return
        }
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (granted) controller.start() else micPermission.launch(Manifest.permission.RECORD_AUDIO)
    }

    ModalBottomSheet(onDismissRequest = onDismiss, containerColor = c.surface) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = Space.xl).padding(bottom = Space.xxxl),
        ) {
            Text(
                "Explain your reasoning",
                color = c.textPrimary,
                fontSize = 22.sp,
                fontWeight = FontWeight.Bold,
            )
            Text(
                "Before you reveal the answer. Captured on-device \u2014 nothing is uploaded.",
                color = c.textSecondary,
                fontSize = 15.sp,
                modifier = Modifier.padding(top = Space.xs),
            )
            Spacer(Modifier.height(Space.l))

            if (listening) {
                Text(
                    partial.ifBlank { "\u2026" },
                    color = c.accent,
                    fontSize = 17.sp,
                    modifier = Modifier.padding(bottom = Space.s),
                )
            }

            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Your reasoning") },
                minLines = 3,
            )

            Text(
                status,
                color = c.textSecondary,
                fontSize = 13.sp,
                modifier = Modifier.padding(top = Space.s),
            )
            Spacer(Modifier.height(Space.l))

            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.size(56.dp).clip(CircleShape)
                        .background(if (listening) c.again else c.accent)
                        .clickable { toggleMic() },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        if (listening) Icons.Filled.Stop else Icons.Filled.Mic,
                        contentDescription = if (listening) "Stop" else "Record",
                        tint = Color.White,
                    )
                }
                Spacer(Modifier.width(Space.l))
                PrimaryButton(
                    text = "Save",
                    modifier = Modifier.weight(1f),
                ) {
                    controller.destroy()
                    onCapture(text.trim())
                }
            }
        }
    }
}
