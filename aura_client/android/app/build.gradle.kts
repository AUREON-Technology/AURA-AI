import java.io.File
import java.util.Properties

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
    id("com.google.gms.google-services")
}

// Release signing is optional. Prefer env (CI / Play upload):
//   AURA_RELEASE_STORE_FILE, AURA_RELEASE_STORE_PASSWORD,
//   AURA_RELEASE_KEY_PASSWORD, AURA_RELEASE_KEY_ALIAS
// or a gitignored android/key.properties with storeFile, storePassword,
// keyPassword, keyAlias. This script never generates a keystore.
// When the store file is absent, release keeps debug signing so
// `flutter run --release` still works locally.
val keystoreProperties = Properties()
val keystorePropertiesFile = rootProject.file("key.properties")
if (keystorePropertiesFile.exists()) {
    keystorePropertiesFile.inputStream().use { keystoreProperties.load(it) }
}

fun auraReleaseProp(envName: String, propertyName: String): String? {
    val fromEnv = System.getenv(envName)?.trim()
    if (!fromEnv.isNullOrEmpty()) return fromEnv
    val fromFile = keystoreProperties.getProperty(propertyName)?.trim()
    return fromFile?.takeIf { it.isNotEmpty() }
}

val auraReleaseStorePath = auraReleaseProp("AURA_RELEASE_STORE_FILE", "storeFile")
val auraReleaseStorePassword = auraReleaseProp("AURA_RELEASE_STORE_PASSWORD", "storePassword")
val auraReleaseKeyPassword = auraReleaseProp("AURA_RELEASE_KEY_PASSWORD", "keyPassword")
val auraReleaseKeyAlias = auraReleaseProp("AURA_RELEASE_KEY_ALIAS", "keyAlias")

fun resolveAuraReleaseStoreFile(): File? {
    val path = auraReleaseStorePath ?: return null
    val candidate = File(path)
    val resolved =
        if (candidate.isAbsolute) {
            candidate
        } else {
            val fromAndroidRoot = rootProject.file(path)
            if (fromAndroidRoot.exists()) fromAndroidRoot else file(path)
        }
    return resolved.takeIf { it.isFile }
}

val auraReleaseStoreFile = resolveAuraReleaseStoreFile()
val hasAuraReleaseSigning =
    auraReleaseStoreFile != null &&
        !auraReleaseStorePassword.isNullOrEmpty() &&
        !auraReleaseKeyPassword.isNullOrEmpty() &&
        !auraReleaseKeyAlias.isNullOrEmpty()

android {
    namespace = "com.auraai.aura_client"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        // Must match backend `android_package_name` / Play Integrity packageName.
        applicationId = "com.auraai.aura_client"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (hasAuraReleaseSigning) {
            create("release") {
                storeFile = auraReleaseStoreFile
                storePassword = auraReleaseStorePassword
                keyPassword = auraReleaseKeyPassword
                keyAlias = auraReleaseKeyAlias
            }
        }
    }

    buildTypes {
        release {
            signingConfig =
                if (hasAuraReleaseSigning) {
                    signingConfigs.getByName("release")
                } else {
                    signingConfigs.getByName("debug")
                }
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
}

flutter {
    source = "../.."
}

dependencies {
    // Official Play Integrity library. Tokens go to X-Device-Attestation.
    implementation("com.google.android.play:integrity:1.4.0")
}
