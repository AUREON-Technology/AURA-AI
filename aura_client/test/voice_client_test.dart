import 'dart:convert';
import 'dart:typed_data';

import 'package:aura_client/core/ai/voice/voice_client.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

ProviderContainer _containerFor(MockClient httpClient) {
  return ProviderContainer(
    overrides: [
      tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
      voiceClientProvider.overrideWith((ref) => VoiceClient(ref, httpClient: httpClient)),
    ],
  );
}

void main() {
  test('speak() POSTs JSON to /api/v3/voice/speak and returns the WAV bytes', () async {
    http.Request? recorded;
    final wavBytes = Uint8List.fromList([0x52, 0x49, 0x46, 0x46]); // "RIFF"
    final httpClient = MockClient((request) async {
      recorded = request;
      return http.Response.bytes(wavBytes, 200, headers: {'content-type': 'audio/wav'});
    });

    final container = _containerFor(httpClient);
    addTearDown(container.dispose);

    final audio = await container.read(voiceClientProvider).speak('hello world', voice: 'default');

    expect(audio, wavBytes);
    expect(recorded, isNotNull);
    expect(recorded!.method, 'POST');
    expect(recorded!.url.path, '/api/v3/voice/speak');
    expect(recorded!.headers['Authorization'], 'Bearer jwt-test');
    final body = jsonDecode(recorded!.body) as Map<String, dynamic>;
    expect(body['text'], 'hello world');
    expect(body['voice'], 'default');
  });

  test('speak() returns null when the deployment has no TTS engine configured', () async {
    final httpClient = MockClient((request) async {
      return http.Response(
        jsonEncode({'error': 'dependency_unavailable', 'message': 'no engine'}),
        503,
      );
    });

    final container = _containerFor(httpClient);
    addTearDown(container.dispose);

    final audio = await container.read(voiceClientProvider).speak('hello');
    expect(audio, isNull);
  });

  test('transcribe() POSTs multipart form data to /api/v3/voice/transcribe', () async {
    http.Request? recorded;
    final httpClient = MockClient((request) async {
      recorded = request;
      return http.Response(jsonEncode({'text': 'hello there', 'engine': 'faster-whisper'}), 200);
    });

    final container = _containerFor(httpClient);
    addTearDown(container.dispose);

    final text = await container
        .read(voiceClientProvider)
        .transcribe([1, 2, 3, 4], filename: 'note.wav');

    expect(text, 'hello there');
    expect(recorded, isNotNull);
    expect(recorded!.method, 'POST');
    expect(recorded!.url.path, '/api/v3/voice/transcribe');
    expect(recorded!.headers['Authorization'], 'Bearer jwt-test');
    expect(recorded!.bodyBytes, isNotEmpty);
  });

  test('transcribe() returns null for an empty transcript', () async {
    final httpClient = MockClient((request) async {
      return http.Response(jsonEncode({'text': '', 'engine': 'faster-whisper'}), 200);
    });

    final container = _containerFor(httpClient);
    addTearDown(container.dispose);

    final text = await container.read(voiceClientProvider).transcribe([1, 2, 3]);
    expect(text, isNull);
  });
}
