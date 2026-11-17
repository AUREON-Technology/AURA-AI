import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../../auth/token_manager.dart';
import '../../config/backend_url_provider.dart';
import '../../network/aura_api_error.dart';
import '../../security/device_validation_service.dart';
import 'speech_text.dart';

/// Outcome of `POST /api/v3/voice/speak`. [audio] is WAV bytes on success;
/// [errorMessage] is a short, user-facing reason on failure.
class VoiceSpeakResult {
  const VoiceSpeakResult._({this.audio, this.errorMessage});

  factory VoiceSpeakResult.success(Uint8List audio) =>
      VoiceSpeakResult._(audio: audio);

  factory VoiceSpeakResult.failure(String errorMessage) =>
      VoiceSpeakResult._(errorMessage: errorMessage);

  final Uint8List? audio;
  final String? errorMessage;

  bool get ok => audio != null && audio!.isNotEmpty;
}

/// Client for the v3 voice APIs (`/api/v3/voice/*`, Phase F2). Both endpoints keep inference on
/// our own infrastructure -- `transcribe` calls the server's self-hosted `faster-whisper` engine,
/// `speak` calls its self-hosted Piper (espeak-ng fallback) engine -- so this class never
/// touches a hosted vendor API, matching the same self-hosting guarantee [KnowledgeCenter]
/// and the chat client already hold to.
class VoiceClient {
  final Ref _ref;
  final http.Client? _http;
  final Duration transcribeTimeout;
  VoiceClient(
    this._ref, {
    http.Client? httpClient,
    this.transcribeTimeout = defaultTranscribeTimeout,
  }) : _http = httpClient;

  /// Upload plus a self-hosted Whisper pass on CPU; generous, but bounded so a stalled server
  /// cannot leave the recorder spinner up forever (INT-017).
  static const defaultTranscribeTimeout = Duration(seconds: 90);

  String get _backendUrl => '${_ref.read(backendUrlProvider)}/api/v3/voice';

  Future<Map<String, String>> _headers(String? token) async {
    final headers = <String, String>{};
    if (token != null) headers['Authorization'] = 'Bearer $token';
    headers.addAll(
      await DeviceValidationService.getAttestationHeaders(
        backendUrl: _ref.read(backendUrlProvider),
      ),
    );
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

    Future<http.Response> exchange() async {
      var streamed = await send();
      if (streamed.statusCode == 401) {
        tokens.invalidateAccessToken();
        streamed = await send();
      }
      return http.Response.fromStream(streamed);
    }

    try {
      final response = await exchange().timeout(transcribeTimeout);
      if (response.statusCode < 200 || response.statusCode >= 300) return null;
      final parsed = jsonDecode(response.body);
      if (parsed is! Map<String, dynamic>) return null;
      final text = parsed['text']?.toString();
      return (text == null || text.trim().isEmpty) ? null : text;
    } catch (e) {
      debugPrint("[VoiceClient] transcribe failed: $e");
      return null;
    }
  }

  /// Synthesizes a reply to WAV bytes for playback.
  Future<VoiceSpeakResult> speak(String text, {String? voice}) async {
    final spoken = prepareSpeechText(text);
    if (spoken.isEmpty) {
      return VoiceSpeakResult.failure('Nothing to read in this reply.');
    }

    final tokens = _ref.read(tokenManagerProvider);
    Future<http.Response> send() async {
      final token = await tokens.getValidAccessToken();
      final uri = Uri.parse("$_backendUrl/speak");
      final headers = await _headers(token);
      headers['Content-Type'] = 'application/json';
      final payload = jsonEncode({'text': spoken, 'voice': ?voice});
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
      if (response.statusCode < 200 || response.statusCode >= 300) {
        debugPrint(
          "[VoiceClient] speak failed: ${response.statusCode} ${response.body}",
        );
        return VoiceSpeakResult.failure(
          _speakFailureMessage(response.statusCode, response.body),
        );
      }
      final audio = response.bodyBytes;
      if (audio.isEmpty) {
        debugPrint("[VoiceClient] speak returned an empty body");
        return VoiceSpeakResult.failure('Could not generate speech for this reply.');
      }
      return VoiceSpeakResult.success(audio);
    } catch (e) {
      debugPrint("[VoiceClient] speak failed: $e");
      return VoiceSpeakResult.failure('Could not reach AURA to generate speech.');
    }
  }

  static String _speakFailureMessage(int statusCode, String body) {
    return switch (statusCode) {
      401 => 'Sign in again to continue.',
      404 => 'Speech playback is not available on this server.',
      422 => _speak422Message(body),
      429 => 'Too many speech requests. Try again shortly.',
      503 => 'This server has no speech engine configured.',
      _ => 'Could not generate speech for this reply.',
    };
  }

  /// The gateway sends 422 for two different things (INT-022): the engine produced no audio
  /// (`ValidationError("text contains nothing speakable")`, an `{error, message}` envelope),
  /// or the text failed request validation (FastAPI's `{detail: [{type: ...}]}`), of which
  /// only `string_too_long` means "too long".
  static String _speak422Message(String body) {
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map && decoded['detail'] is List) {
        final tooLong = (decoded['detail'] as List)
            .whereType<Map>()
            .any((e) => e['type']?.toString() == 'string_too_long');
        if (tooLong) return 'This reply is too long to read aloud.';
      }
    } catch (_) {
      // Not JSON; fall through to the envelope parser, which never throws.
    }
    final error = AuraApiError.fromResponse(statusCode: 422, body: body);
    if (error.message.toLowerCase().contains('speakable')) {
      return 'Nothing in this reply can be read aloud.';
    }
    return 'Could not read this reply aloud.';
  }
}

final voiceClientProvider = Provider<VoiceClient>((ref) {
  return VoiceClient(ref);
});
