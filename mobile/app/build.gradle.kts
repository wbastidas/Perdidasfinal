plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "ec.cnel.ptnt.field"
    compileSdk = 35

    defaultConfig {
        applicationId = "ec.cnel.ptnt.field"
        // API 24 (Android 7): cubre los equipos de gama baja que efectivamente
        // se entregan a las cuadrillas. Subir el mínimo dejaría fuera parque real.
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        vectorDrawables { useSupportLibrary = true }
    }

    buildTypes {
        release {
            // R8 con reducción de recursos: el APK viaja por la red de la
            // distribuidora y se instala en equipos con poco espacio libre.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // Necesario para java.time en API < 26.
        isCoreLibraryDesugaringEnabled = true
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }

    packaging {
        resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.10.00")
    implementation(composeBom)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    // MapLibre GL: motor de mapas libre, sin licencias ArcGIS. Lee MBTiles y
    // teselas de GeoPackage, y renderiza con GPU — que es lo que permite un mapa
    // fluido en equipos modestos.
    implementation("org.maplibre.gl:android-sdk:11.5.2")

    // EXIF: metadatos de ubicación y fecha en las fotografías.
    implementation("androidx.exifinterface:exifinterface:1.3.7")

    // No se incluye CameraX: la captura la hace la app de cámara del sistema,
    // que ya trae enfoque, HDR y estabilización ajustados a cada sensor.
    // Reimplementarla daría fotos peores en medio parque de equipos y sumaría
    // megabytes al APK. Lo propio —ubicación, hora, autor y hash— se escribe
    // después sobre el archivo devuelto.

    // Ubicación
    implementation("com.google.android.gms:play-services-location:21.3.0")

    // Red
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // Token de sesión cifrado: en un teléfono compartido entre cuadrillas,
    // guardarlo en claro permitiría sincronizar a nombre de otro técnico.
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    // Trabajo en segundo plano: la sincronización se reintenta cuando vuelve la
    // señal, sin que el técnico tenga que acordarse.
    implementation("androidx.work:work-runtime-ktx:2.9.1")

    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.2")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
}
