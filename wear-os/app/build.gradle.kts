plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}
val apiBase = System.getenv("ZENNAY_API_BASE") ?: "http://198.244.191.182:8765"
val apiToken = System.getenv("ZENNAY_WATCH_TOKEN") ?: ""
fun quoted(value: String) = "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

android {
    namespace = "com.zennay.cloud.watch"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.zennay.cloud.watch"
        minSdk = 30
        targetSdk = 35
        versionCode = 3
        versionName = "0.3.0"
        buildConfigField("String", "API_BASE", quoted(apiBase))
        buildConfigField("String", "API_TOKEN", quoted(apiToken))
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.compose.ui:ui:1.11.0")
    implementation("androidx.compose.ui:ui-graphics:1.11.0")
    implementation("androidx.compose.foundation:foundation:1.11.0")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.10.0")
    implementation("androidx.wear.compose:compose-foundation:1.6.1")
    implementation("androidx.wear.compose:compose-material:1.6.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
}