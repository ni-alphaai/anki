import com.google.protobuf.gradle.id

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.protobuf") version "0.9.4"
}

android {
    namespace = "net.speedrun.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "net.speedrun.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // v1 targets arm64 devices/emulators; add more ABIs as needed.
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    buildFeatures {
        compose = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    // librsandroid.so is produced by cargo-ndk into app/src/main/jniLibs/<abi>/.
    // See ../README.md for the build command.
    sourceSets["main"].jniLibs.srcDirs("src/main/jniLibs")

    // Protobuf sources are picked up from the plugin's default dir,
    // app/src/main/proto, which is a symlink to the shared ../../proto tree.
    // The phone therefore generates the exact same messages as the desktop.
}

protobuf {
    protoc {
        artifact = "com.google.protobuf:protoc:3.25.3"
    }
    generateProtoTasks {
        all().forEach { task ->
            task.builtins {
                // Lite runtime: small, no reflection - the right fit for Android.
                id("java") {
                    option("lite")
                }
            }
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.02")
    implementation(composeBom)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.navigation:navigation-compose:2.8.2")

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.webkit:webkit:1.11.0")
    debugImplementation("androidx.compose.ui:ui-tooling")

    // Matches the protoc artifact above; the generated code needs the lite runtime.
    implementation("com.google.protobuf:protobuf-javalite:3.25.3")

    // QR scanning for one-tap sync pairing (scan the desktop's code). Bundles a
    // self-contained scanner Activity driven via the ActivityResult ScanContract.
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")

    // Pure-JVM unit tests (host, no device): plain JUnit4.
    testImplementation("junit:junit:4.13.2")
    // Real org.json on the host test classpath (the android.jar one is a stub
    // that throws in unit tests) so SyncPairing parsing can be tested off-device.
    testImplementation("org.json:json:20240303")

    // Instrumented Compose UI tests (on-device): the compose test rule + AndroidX
    // JUnit runner, pinned via the same compose BOM as the app deps above.
    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
