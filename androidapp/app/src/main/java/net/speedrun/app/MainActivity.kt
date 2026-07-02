// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import net.speedrun.app.ui.SpeedrunApp
import net.speedrun.app.ui.theme.SpeedrunTheme

/**
 * Speedrun Android host. A single Compose activity; all UI lives in
 * [SpeedrunApp] and all engine access goes through [EngineRepository], which
 * drives the shared Rust core over JNI.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppSettings.load(this)
        enableEdgeToEdge()
        setContent {
            val dark = when (AppSettings.themeMode) {
                ThemeMode.System -> isSystemInDarkTheme()
                ThemeMode.Light -> false
                ThemeMode.Dark -> true
            }
            SpeedrunTheme(darkTheme = dark) {
                SpeedrunApp()
            }
        }
    }

    override fun onDestroy() {
        EngineRepository.shutdown()
        super.onDestroy()
    }
}
