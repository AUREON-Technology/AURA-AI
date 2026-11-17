// The live-voice screen follows what the server actually did with the socket.
//
// INT-015: a server that refuses the socket at once (4503 feature off, 4429 session held) could
// close it before the view model subscribed to the broadcast frames stream; the close was lost
// and the screen sat on "listening" with the mic running.
// INT-020: the mic was started at a hard-coded 16 kHz, before the server's `ready` frame said
// which capture rate it expects.

import 'dart:async';
import 'dart:convert';

import 'package:aura_client/core/ai/voice/live/live_mic_source.dart';
import 'package:aura_client/core/ai/voice/live/live_voice_socket.dart';
import 'package:aura_client/core/ai/voice/voice_language.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/features/voice_live/presentation/live_voice_view_model.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

class _Tokens extends TokenManager {
  _Tokens(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';

  @override
  Future<String?> getAccessTokenValidFor(Duration minValidity) async => 'jwt-test';
}

class _FakeSink implements WebSocketSink {
  final sent = <dynamic>[];
  @override
  void add(dynamic data) => sent.add(data);
  @override
  Future<void> close([int? closeCode, String? closeReason]) async {}
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _FakeChannel implements WebSocketChannel {
  final incoming = StreamController<dynamic>();
  final fakeSink = _FakeSink();
  int? code;

  @override
  Stream<dynamic> get stream => incoming.stream;
  @override
  WebSocketSink get sink => fakeSink;
  @override
  int? get closeCode => code;
  @override
  String? get closeReason => null;
  @override
  String? get protocol => null;
  @override
  Future<void> get ready => Future.value();

  List<String> get sentTypes => fakeSink.sent
      .whereType<String>()
      .map((s) => (jsonDecode(s) as Map<String, dynamic>)['type'] as String)
      .toList();

  List<Map<String, dynamic>> get startFrames => fakeSink.sent
      .whereType<String>()
      .map((s) => jsonDecode(s) as Map<String, dynamic>)
      .where((f) => f['type'] == 'start')
      .toList();

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _FakeMic implements LiveMicSource {
  final starts = <int>[];
  final _pcm = StreamController<List<int>>.broadcast();
  final _amp = StreamController<double>.broadcast();
  bool stopped = false;

  @override
  bool get isSupported => true;
  @override
  Future<bool> ensurePermission() async => true;
  @override
  Future<void> start({int sampleRate = 16000}) async => starts.add(sampleRate);
  @override
  Future<void> stop() async => stopped = true;
  @override
  Future<void> dispose() async {
    stopped = true;
    await _pcm.close();
    await _amp.close();
  }

  @override
  Stream<List<int>> get pcm => _pcm.stream;
  @override
  Stream<double> get amplitude => _amp.stream;
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Future<void> _settle() => Future<void>.delayed(const Duration(milliseconds: 30));

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late _FakeChannel channel;
  late _FakeMic mic;
  late ProviderContainer container;

  late List<_FakeChannel> channels;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    channel = _FakeChannel();
    channels = [];
    mic = _FakeMic();
    container = ProviderContainer(overrides: [
      deviceLanguageCodeProvider.overrideWithValue('en'),
      tokenManagerProvider.overrideWith((ref) => _Tokens(ref)),
      liveVoiceSocketFactoryProvider.overrideWithValue(
        (ref) => LiveVoiceSocket(
          ref,
          connect: (_) {
            channels.add(channel);
            return channel;
          },
          attestation: (_) async => const {'X-Device-Platform': 'test'},
          reauthInterval: const Duration(hours: 1),
        ),
      ),
      liveMicSourceFactoryProvider.overrideWithValue(() => mic),
    ]);
  });

  tearDown(() => container.dispose());

  test('a 4503 close before auth ends in the error state, mic never started', () async {
    // The server refuses the socket as soon as it is accepted.
    channel.code = LiveVoiceSocket.closeUnavailable;
    unawaited(channel.incoming.close());

    final vm = container.read(liveVoiceViewModelProvider.notifier);
    container.listen(liveVoiceViewModelProvider, (_, _) {});
    await vm.startSession(conversationId: 'conv-1');
    await _settle();

    final state = container.read(liveVoiceViewModelProvider);
    expect(state.phase, 'error');
    expect(state.error, LiveVoiceSocket.errorForCloseCode(4503).message);
    expect(mic.starts, isEmpty, reason: 'no mic without a ready frame');
  });

  test('a 4429 close mid-session ends in the error state with its own message', () async {
    final vm = container.read(liveVoiceViewModelProvider.notifier);
    container.listen(liveVoiceViewModelProvider, (_, _) {});
    await vm.startSession(conversationId: 'conv-1');
    channel.incoming.add(jsonEncode({
      'type': 'ready',
      'capture': {'sample_rate': 16000, 'channels': 1, 'format': 'pcm_s16le'},
      'playback': {'sample_rate': 22050, 'channels': 1, 'format': 'pcm_s16le'},
    }));
    await _settle();
    expect(container.read(liveVoiceViewModelProvider).phase, 'listening');

    channel.code = LiveVoiceSocket.closeTooMany;
    await channel.incoming.close();
    await _settle();

    final state = container.read(liveVoiceViewModelProvider);
    expect(state.phase, 'error');
    expect(state.error, LiveVoiceSocket.errorForCloseCode(4429).message);
    expect(mic.stopped, isTrue);
  });

  test('the mic starts only after ready, at the announced capture rate', () async {
    final vm = container.read(liveVoiceViewModelProvider.notifier);
    container.listen(liveVoiceViewModelProvider, (_, _) {});
    await vm.startSession(conversationId: 'conv-1');
    await _settle();

    expect(channel.sentTypes.take(2), ['auth', 'start']);
    expect(mic.starts, isEmpty);
    expect(container.read(liveVoiceViewModelProvider).phase, 'connecting');

    channel.incoming.add(jsonEncode({
      'type': 'ready',
      'capture': {'sample_rate': 24000, 'channels': 1, 'format': 'pcm_s16le'},
      'playback': {'sample_rate': 22050, 'channels': 1, 'format': 'pcm_s16le'},
    }));
    await _settle();

    expect(mic.starts, [24000]);
    expect(container.read(liveVoiceViewModelProvider).phase, 'listening');
  });

  // Phase V: the start frame carries the chosen voice language, not a hard-coded 'en'.
  test('the start frame carries the chosen voice language', () async {
    await container.read(voiceLanguageProvider.notifier).set(VoiceLanguage.arabic);
    final vm = container.read(liveVoiceViewModelProvider.notifier);
    container.listen(liveVoiceViewModelProvider, (_, _) {});
    await vm.startSession(conversationId: 'conv-1');
    await _settle();

    expect(channel.startFrames.single['language'], 'ar');
  });

  test('switching language restarts the open session in the new language', () async {
    final vm = container.read(liveVoiceViewModelProvider.notifier);
    container.listen(liveVoiceViewModelProvider, (_, _) {});
    await vm.startSession(conversationId: 'conv-1');
    await _settle();
    expect(channel.startFrames.single['language'], 'en');

    final first = channel;
    channel = _FakeChannel();
    await vm.setLanguage(VoiceLanguage.arabic);
    await _settle();

    expect(channels, [first, channel], reason: 'a fresh socket for the new language');
    expect(channel.startFrames.single['language'], 'ar');
    expect(channel.startFrames.single['session_id'], 'conv-1', reason: 'same conversation');
  });

  test('switching language with no session open only saves the choice', () async {
    final vm = container.read(liveVoiceViewModelProvider.notifier);
    await vm.setLanguage(VoiceLanguage.arabic);
    await _settle();

    expect(channels, isEmpty);
    expect(container.read(voiceLanguageProvider), VoiceLanguage.arabic);
  });

  test('a server that cannot hear the language says to switch, not a generic error', () async {
    await container.read(voiceLanguageProvider.notifier).set(VoiceLanguage.arabic);
    final vm = container.read(liveVoiceViewModelProvider.notifier);
    container.listen(liveVoiceViewModelProvider, (_, _) {});
    await vm.startSession(conversationId: 'conv-1');
    channel.incoming.add(jsonEncode({
      'type': 'error',
      'error': 'language_unsupported',
      'message': 'live voice STT is English-only with the configured model',
    }));
    channel.code = LiveVoiceSocket.closeBadRequest;
    await channel.incoming.close();
    await _settle();

    final state = container.read(liveVoiceViewModelProvider);
    expect(state.phase, 'error');
    expect(state.error, languageUnavailableMessage(VoiceLanguage.arabic));
  });
}
