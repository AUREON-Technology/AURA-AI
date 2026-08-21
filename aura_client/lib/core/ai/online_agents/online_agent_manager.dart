import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:google_sign_in/google_sign_in.dart';
import '../../config/config.dart';
import '../../../features/auth/presentation/auth_provider.dart';
import '../../security/device_validation_service.dart';
import 'llm_timeouts.dart';

/// The backend did not produce an answer.
///
/// A named type rather than a bare `Exception` so the UI can tell an outage from a refusal and
/// word itself accordingly: a 429 is "try again shortly", a 503 is "AURA is starting up", and a
/// connection failure is "check your connection". `statusCode` is null when the request never got
/// far enough to receive one.
class AuraBackendException implements Exception {
  final String message;
  final int? statusCode;

  const AuraBackendException(this.message, {this.statusCode});

  bool get isRetryable =>
      statusCode == null || statusCode == 429 || (statusCode! >= 500 && statusCode! < 600);

  @override
  String toString() => message;
}

/// Streams an agent turn from the AURA backend.
///
/// There is deliberately no second path. This class previously held a Gemini API key on the
/// device (`localApiKeyProvider`) and called Google directly whenever the backend was
/// unreachable. That was removed for three reasons, in order of severity:
///
///  1. **It shipped a credential to every install.** An API key on a user's device is a key you
///     cannot rotate out of circulation and cannot stop anyone extracting from the APK.
///  2. **It bypassed every server-side control.** Authentication, rate limiting, prompt-injection
///     guards, tool permissions, audit logging and memory scoping all live behind the backend.
///     A request that went straight to Google had none of them.
///  3. **It made the platform's self-hosting guarantee unenforceable.** Removing hosted inference
///     server-side means nothing while any client can reach a vendor on its own.
///
/// When the backend cannot be reached the correct behaviour is to say so. The caller surfaces
/// that; it does not invent an answer.
class OnlineAgentManager {
  final Ref _ref;
  static const String _backendUrl = "${AppConfig.backendUrl}/api/chat/agent";

  OnlineAgentManager(this._ref);

  /// Streams the response from the AURA backend.
  ///
  /// Throws when the backend cannot be reached or refuses the request. Throwing rather than
  /// yielding an error string is what lets the caller distinguish "the assistant said something"
  /// from "we never got an answer" -- the same distinction the server-side generator makes.
  Stream<String> streamResponse({
    required String agentId,
    required List<Map<String, String>> messages,
    Map<String, String>? customInstructions,
    bool useRag = true,
  }) async* {
    final token = await _ref.read(authProvider.notifier).getIdToken();

    final client = http.Client();
    try {
      final request = http.Request('POST', Uri.parse(_backendUrl));
      request.headers['Content-Type'] = 'application/json';
      if (token != null) {
        request.headers['Authorization'] = 'Bearer $token';
      }
      // Inject device validation integrity headers
      request.headers.addAll(await DeviceValidationService.getAttestationHeaders());

      // Retrieve Google Access Token if logged in via Google Sign-In
      String? googleAccessToken;
      try {
        final scopes = ['https://www.googleapis.com/auth/drive.readonly'];
        final authorization = await GoogleSignIn.instance.authorizationClient.authorizeScopes(scopes);
        googleAccessToken = authorization.accessToken;
      } catch (e) {
        debugPrint("Failed to fetch Google access token: $e");
      }

      request.body = jsonEncode({
        "agent_id": agentId,
        "messages": messages,
        "custom_instructions": customInstructions,
        "use_rag": useRag,
        "google_access_token": googleAccessToken,
      });

      final response = await client.send(request).timeout(kLlmConnectTimeout);

      if (response.statusCode != 200) {
        // Surface the server's own message when it sent one. A 429 with a retry hint or a 503
        // naming the unavailable dependency is far more actionable than a bare status code, and
        // the backend takes care to produce both.
        String detail = "HTTP ${response.statusCode}";
        try {
          final bodyString = utf8.decode(await response.stream.toBytes());
          final parsed = jsonDecode(bodyString);
          if (parsed is Map && parsed["error"] != null) {
            detail = parsed["error"].toString();
          } else if (parsed is Map && parsed["detail"] != null) {
            detail = parsed["detail"].toString();
          }
        } catch (_) {
          // Body was not JSON; the status code stands on its own.
        }
        throw AuraBackendException(detail, statusCode: response.statusCode);
      }

      await for (final chunk in response.stream.transform(utf8.decoder)) {
        final trimmed = chunk.trim();
        if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
          try {
            final parsed = jsonDecode(trimmed);
            if (parsed is Map && parsed.containsKey("error")) {
              yield "\n[Error: ${parsed["error"]}]";
              continue;
            }
          } catch (_) {}
        }
        yield chunk;
      }
    } on AuraBackendException {
      rethrow;
    } catch (e) {
      debugPrint("AURA backend stream failed: $e");
      throw AuraBackendException("Could not reach AURA: $e");
    } finally {
      client.close();
    }
  }
}

final onlineAgentManagerProvider = Provider<OnlineAgentManager>((ref) {
  return OnlineAgentManager(ref);
});
