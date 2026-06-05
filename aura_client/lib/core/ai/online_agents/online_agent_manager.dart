import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:google_generative_ai/google_generative_ai.dart' as google_ai;
import 'package:google_sign_in/google_sign_in.dart';
import '../../config/config.dart';
import '../../../features/auth/presentation/auth_provider.dart';
import '../../security/device_validation_service.dart';

class LocalApiKeyNotifier extends Notifier<String> {
  @override
  String build() => "";
  void setKey(String key) => state = key;
}

final localApiKeyProvider = NotifierProvider<LocalApiKeyNotifier, String>(() {
  return LocalApiKeyNotifier();
});

class OnlineAgentManager {
  final Ref _ref;
  static const String _backendUrl = "${AppConfig.backendUrl}/api/chat/agent";

  OnlineAgentManager(this._ref);

  /// Streams response from FastAPI backend agent endpoint with direct SDK fallback
  Stream<String> streamResponse({
    required String agentId,
    required List<Map<String, String>> messages,
    Map<String, String>? customInstructions,
    bool useRag = true,
  }) async* {
    bool backendSuccess = false;
    final token = await _ref.read(authProvider.notifier).getIdToken();

    // 1. Try streaming from Hosted FastAPI Backend
    try {
      final client = http.Client();
      final request = http.Request('POST', Uri.parse(_backendUrl));
      request.headers['Content-Type'] = 'application/json';
      if (token != null) {
        request.headers['Authorization'] = 'Bearer $token';
      }
      // Inject device validation integrity headers
      request.headers.addAll(DeviceValidationService.getAttestationHeaders());

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

      final response = await client.send(request).timeout(const Duration(seconds: 15));

      if (response.statusCode == 200) {
        backendSuccess = true;
        await for (final chunk in response.stream.transform(utf8.decoder)) {
          yield chunk;
        }
      }
    } catch (e) {
      debugPrint("Hosted FastAPI stream failed: $e. Attemping client-side SDK fallback.");
    }

    // 2. Direct Gemini SDK fallback if FastAPI server is down
    if (!backendSuccess) {
      final localApiKey = _ref.read(localApiKeyProvider);
      if (localApiKey.isNotEmpty) {
        try {
          final model = google_ai.GenerativeModel(
            model: 'gemini-1.5-flash',
            apiKey: localApiKey,
          );

          final geminiHistory = messages.map((m) {
            final role = m["role"] == "assistant" || m["role"] == "model" ? "model" : "user";
            return google_ai.Content(role, [google_ai.TextPart(m["content"] ?? "")]);
          }).toList();

          final prompt = messages.last["content"] ?? "";
          
          // History must exclude the current message
          final historyList = geminiHistory.length > 1 
              ? geminiHistory.sublist(0, geminiHistory.length - 1) 
              : <google_ai.Content>[];

          final chat = model.startChat(history: historyList);
          final responseStream = chat.sendMessageStream(google_ai.Content.text(prompt));

          await for (final chunk in responseStream) {
            if (chunk.text != null) {
              yield chunk.text!;
            }
          }
          backendSuccess = true;
        } catch (e) {
          throw Exception("Direct Gemini SDK fallback failed: $e");
        }
      } else {
        throw Exception("AURA AI backend is offline and no local API key is configured.");
      }
    }
  }
}

final onlineAgentManagerProvider = Provider<OnlineAgentManager>((ref) {
  return OnlineAgentManager(ref);
});
