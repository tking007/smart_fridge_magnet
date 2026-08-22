plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "org.smartfridgemagnet"
    compileSdk = 36

    defaultConfig {
        applicationId = "org.smartfridgemagnet"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencyLocking {
    lockAllConfigurations()
}

dependencies {
    // AndroidX Core
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.4")
    implementation("androidx.activity:activity-compose:1.9.1")

    // Compose BOM
    implementation(platform("androidx.compose:compose-bom:2025.08.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    // Network
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // JSON
    implementation("com.google.code.gson:gson:2.10.1")

    // Image loading
    implementation("io.coil-kt:coil-compose:2.5.0")

    // 条码解码（本地，离线，纯 Java）。
    //
    // ── 为什么是 ZXing 而不是 ML Kit ──
    // 先试过 com.google.mlkit:barcode-scanning（模型打包版）：APK 从 19.8MB 涨到 44.9MB，
    // 25MB 换一个辅助入口不成比例。ML Kit 的 GMS 按需下发版只有几百 KB，但目标设备是
    // 部分设备没有 GMS，那一版在这些机器上运行时才会失败。
    // ZXing core 约 0.5MB、不依赖 GMS，对「静态图里的 EAN-13」这个具体场景完全够用 ——
    // 我们不需要实时预览扫码，也不需要 ML Kit 那些复杂格式与角度鲁棒性。
    //
    // 为什么不让多模态模型直接读条码数字：EAN-13 错一位就是另一个商品，视觉模型认数字
    // 不可靠；本地解码是确定性的。模型只用来回答「这个码是什么商品」。
    implementation("com.google.zxing:core:3.5.3")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")

    // Debug
    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")

    // 单元测试（JVM，不需要设备/模拟器）。
    // 只覆盖纯 Kotlin 的跨端契约与映射逻辑 —— 那些错了会静默失败、且真机复现代价最高的部分。
    testImplementation("junit:junit:4.13.2")
}
