package com.auraai.aura_client

import android.content.Context
import com.google.android.play.core.integrity.IntegrityManagerFactory
import com.google.android.play.core.integrity.IntegrityTokenRequest
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/// Play Integrity Standard API. The Dart client sends the returned JWS as
/// `X-Device-Attestation`; the backend verifies it with Google's decode API.
class IntegrityAttestationPlugin(
    private val context: Context,
) : MethodChannel.MethodCallHandler {
    companion object {
        const val CHANNEL = "com.auraai.aura_client/attestation"

        // Must match aura_client/android/app/google-services.json project_number
        // and the Play Console Cloud project linked for Integrity.
        const val CLOUD_PROJECT_NUMBER = 87789110323L

        fun register(flutterEngine: FlutterEngine, context: Context) {
            MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
                .setMethodCallHandler(IntegrityAttestationPlugin(context))
        }
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "requestPlayIntegrityToken" -> requestToken(call, result)
            "isPlayIntegrityAvailable" -> result.success(true)
            else -> result.notImplemented()
        }
    }

    private fun requestToken(call: MethodCall, result: MethodChannel.Result) {
        val nonce = call.argument<String>("nonce").orEmpty()
        val manager = IntegrityManagerFactory.create(context)
        val request = IntegrityTokenRequest.builder()
            .setNonce(nonce)
            .setCloudProjectNumber(CLOUD_PROJECT_NUMBER)
            .build()
        manager.requestIntegrityToken(request)
            .addOnSuccessListener { response ->
                result.success(response.token())
            }
            .addOnFailureListener { error ->
                result.error("PLAY_INTEGRITY_FAILED", error.message, null)
            }
    }
}
