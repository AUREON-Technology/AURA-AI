import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../../config/config.dart';
import '../../auth/token_manager.dart';
import '../../security/device_validation_service.dart';

/// Client for the v3 voice APIs (`/api/v3/voice/*`, Phase F2). Both endpoints keep inference on
/// our own infrastructure -- `transcribe` calls the server's self-hosted `faster-whisper` engine,
/// `speak` calls its self-hosted `espeak-ng` engine -- so this class never touches a hosted
/// vendor API, matching the same self-hosting guarantee [KnowledgeCenter] and the chat client
/// already hold to.
class VoiceClient {
  final Ref _ref;
  final http.Client? _http;
  static const String _backendUrl = "${AppConfig.backendUrl}/api/v3/voice";

  VoiceClient(this._ref, {http.Client? httpClient}) : _http = httpClient;

  Future<Map<String, String>> _headers(String? token) async {
    final headers = <String, String>{};
    if (token != null) headers['Authorization'] = 'Bearer $token';
    headers.addAll(await DeviceValidationService.getAttestationHeaders());
    return headers;
  }

  /// Sends a recorded voice note and returns the transcript, or `null` on any failure --
  /// callers treat a `null` result as "nothing to insert" rather than surfacing raw errors for
  /// what is, at worst, a failed convenience feature.
  Future<String?> transcribe(List<int> audioBytes, {String filename = 'voice.wav'}) async {
    final tokens = _ref.read(tokenManagerProvider);
    Future<http.StreamedResponse> send() async {
      final token = await tokens.getValidAccessToken();
      final uri = Uri.parse("$_backendUrl/transcribe");
      final request = http.MultipartRequest('POST', uri)
        ..files.add(http.MultipartFile.fromBytes('file', audioBytes, filename: filename));
      request.headers.addAll(await _headers(token));
      final client = _http;
      return client != null ? client.send(request) : http.Client().send(request);
    }

    try {
      var streamed = await send();
      if (streamed.statusCode == 401) {
        tokens.invalidateAccessToken();
        streamed = await send();
      }
      final response = await http.Response.fromStream(streamed);
      if (response.statusCode < 200 || response.statusCode >= 300) return null;
      final parsed = jsonDecode(response.body);
      if (parsed is! Map<String, dynamic>) return null;
      final text = parsed['text']?.toString();
      return (text == null || text.trim().isEmpty) ? null : text;
    } catch (e) {
      debugPrint("VoiceClient: transcribe failed: $e");
      return null;
    }
  }

  /// Synthesizes `text` to WAV bytes for playback, or `null` on any failure.
  Future<Uint8List?> speak(String text, {String? voice}) async {
    final tokens = _ref.read(tokenManagerProvider);
    Future<http.Response> send() async {
      final token = await tokens.getValidAccessToken();
      final uri = Uri.parse("$_backendUrl/speak");
      final headers = await _headers(token);
      headers['Content-Type'] = 'application/json';
      final payload = jsonEncode({'text': text, if (voice != null) 'voice': voice});
      final client = _http;
      final sent = client != null
          ? client.post(uri, headers: headers, body: payload)
          : http.post(uri, headers: headers, body: payload);
      return sent.timeout(const Duration(seconds: 30));
    }

    try {
      var response = await send();
      if (response.statusCode == 401) {
        tokens.invalidateAccessToken();
        response = await send();
      }
      if (response.statusCode < 200 || response.statusCode >= 300) return null;
      return response.bodyBytes;
    } catch (e) {
      debugPrint("VoiceClient: speak failed: $e");
      return null;
    }
  }
}

final voiceClientProvider = Provider<VoiceClient>((ref) {
  return VoiceClient(ref);
});
