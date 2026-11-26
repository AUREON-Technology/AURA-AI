// The live-voice screen follows what the server actually did with the socket.
//
// INT-015: a server that refuses the socket at once (4503 feature off, 4429 session held) could
// close it before the view model subscribed to the broadcast frames stream; the close was lost
// and the screen sat on "listening" with the mic running.
// INT-020: the mic was started at a hard-coded 16 kHz, before the server's `ready` frame said
// which capture rate it expects.
// INT-102: reply audio went to a no-op sink; it now reaches a real one, ends with `audio_end`, and
// a barge-in (either side) cuts it off at once.
// INT-116: mic chunks went to the server as the platform buffered them, not in the 20 ms frames
// its endpointer counts.

import 'dart:async';
import 'dart:convert';

import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/ai/voice/live/live_audio_sink.dart';
import 'package:aura_client/core/ai/voice/live/live_mic_source.dart';
import 'package:aura_client/core/ai/voice/live/live_voice_socket.dart';
import 'package:aura_client/core/ai/voice/voice_language.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/voice_live/presentation/live_voice_view_model.dart';
import 'package:aura_client/l10n/l10n.dart';
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

/// Records what the view model asks of the playback sink.
class _RecordingSink implements LiveAudioSink {
  _RecordingSink({this.available = true});

  final bool available;
  final calls = <String>[];
  final pushed = <List<int>>[];
  final _amp = StreamController<double>.broadcast();

  @override
  int sampleRate = 0;
  @override
  bool get isAvailable => available;
  @override
  Stream<double> get amplitude => _amp.stream;
  @override
  Future<void> start({required int sampleRate, int channels = 1}) async {
    this.sampleRate = sampleRate;
    calls.add('start:$sampleRate');
  }

  @override
  Future<void> pushPcm(List<int> pcm16le) async {
    pushed.add(pcm16le);
    calls.add('pcm:${pcm16le.length}');
    _amp.add(0.5);
  }

  @override
  Future<void> endOfUtterance() async => calls.add('end');
  @override
  Future<void> flush() async => calls.add('flush');
  @override
  Future<void> dispose() async {
    calls.add('dispose');
    await _amp.close();
  }
}

Future<void> _settle() => Future<void>.delayed(const Duration(milliseconds: 30));

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late _FakeChannel channel;
  late _FakeMic mic;
  late ProviderContainer container;

  late List<_FakeChannel> channels;
  late _RecordingSink sink;

  setUp(() {
    sink = _RecordingSink();
    SharedPreferences.setMockInitialValues({});
    channel = _FakeChannel();
    channels = [];
    mic = _FakeMic();
    container = ProviderContainer(overrides: [
      appLanguageCodeProvider.overrideWithValue('en'),
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
      liveAudioSinkFactoryProvider.overrideWithValue(() async => sink),
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

  test('a server that cannot hear Arabic restarts the session in English and says so', () async {
    await container.read(voiceLanguageProvider.notifier).set(VoiceLanguage.arabic);
    final vm = container.read(liveVoiceViewModelProvider.notifier);
    container.listen(liveVoiceViewModelProvider, (_, _) {});
    await vm.startSession(conversationId: 'conv-1');
    await _settle();
    expect(channel.startFrames.single['language'], 'ar');

    final refused = channel;
    channel = _FakeChannel();
    refused.incoming.add(jsonEncode({
      'type': 'error',
      'error': 'language_unsupported',
      'message': 'live voice STT is English-only with the configured model',
    }));
    await _settle();

    expect(channel.startFrames.single['language'], 'en');
    expect(channel.startFrames.single['session_id'], 'conv-1');
    expect(container.read(liveVoiceViewModelProvider).error,
        currentL10n.voiceLiveArabicUnavailable);
  });

  group('a refused turn reads like the HTTP refusal (F7)', () {
    Future<String?> refuse(Map<String, dynamic> frame) async {
      final vm = container.read(liveVoiceViewModelProvider.notifier);
      container.listen(liveVoiceViewModelProvider, (_, _) {});
      await vm.startSession(conversationId: 'conv-1');
      await _settle();
      channel.incoming.add(jsonEncode({'type': 'error', ...frame}));
      await _settle();
      return container.read(liveVoiceViewModelProvider).error;
    }

    test('a rate limit says when to try again, not the server text', () async {
      final shown = await refuse({
        'error': 'rate_limited',
        'message': 'rate limit exceeded',
        'retryable': true,
        'details': {'retry_after': 30},
      });
      expect(shown, isNot('rate limit exceeded'));
      expect(shown, AuraFailure.from(const AuraApiError(
        error: 'rate_limited', message: 'x', retryable: true,
        retryAfter: Duration(seconds: 30), details: {'retry_after': 30},
      )).message);
    });

    test('a plan limit uses the plan wording', () async {
      const details = {'plan': 'free', 'limit': 50, 'used': 50};
      final shown = await refuse({
        'error': 'plan_limit',
        'message': 'monthly message limit reached',
        'retryable': false,
        'details': details,
      });
      expect(shown, isNot('monthly message limit reached'));
      expect(shown, AuraFailure.from(const AuraApiError(
        error: 'plan_limit', message: 'x', retryable: false, details: details,
      )).message);
    });

    test('an unknown error still shows the server message', () async {
      expect(await refuse({'error': 'something_new', 'message': 'server says'}), 'server says');
    });
  });

  test('choosing Arabic again retries it after an English-only fallback', () async {
    await container.read(voiceLanguageProvider.notifier).set(VoiceLanguage.arabic);
    final vm = container.read(liveVoiceViewModelProvider.notifier);
    container.listen(liveVoiceViewModelProvider, (_, _) {});
    await vm.startSession(conversationId: 'conv-1');
    await _settle();
    final refused = channel;
    channel = _FakeChannel();
    refused.incoming.add(jsonEncode({'type': 'error', 'error': 'language_unsupported'}));
    await _settle();
    expect(channel.startFrames.single['language'], 'en');

    channel = _FakeChannel();
    await vm.setLanguage(VoiceLanguage.arabic);
    await _settle();
    expect(channel.startFrames.single['language'], 'ar');
  });

  Future<LiveVoiceViewModel> readySession({int captureRate = 16000, int playbackRate = 22050}) async {
    final vm = container.read(liveVoiceViewModelProvider.notifier);
    container.listen(liveVoiceViewModelProvider, (_, _) {});
    await vm.startSession(conversationId: 'conv-1');
    channel.incoming.add(jsonEncode({
      'type': 'ready',
      'capture': {'sample_rate': captureRate, 'channels': 1, 'format': 'pcm_s16le'},
      'playback': {'sample_rate': playbackRate, 'channels': 1, 'format': 'pcm_s16le'},
    }));
    await _settle();
    return vm;
  }

  group('reply audio (INT-102)', () {
    test('reaches the sink at the announced playback rate and ends with audio_end', () async {
      await readySession(playbackRate: 24000);
      channel.incoming.add(List<int>.filled(4410, 1));
      await _settle();
      channel.incoming.add(jsonEncode({'type': 'done'}));
      channel.incoming.add(jsonEncode({'type': 'audio_end'}));
      await _settle();

      expect(sink.calls, ['start:24000', 'pcm:4410', 'end']);
      expect(container.read(liveVoiceViewModelProvider).playbackAmplitude, 0.5);
    });

    test('a playback frame switches the rate before the audio that uses it', () async {
      await readySession();
      channel.incoming.add(jsonEncode({'type': 'playback', 'sample_rate': 16000}));
      await _settle();
      channel.incoming.add(List<int>.filled(100, 1));
      await _settle();

      expect(sink.calls, ['start:22050', 'start:16000', 'pcm:100']);
    });

    test("the user's barge-in cuts playback at once and drops audio the server already sent", () async {
      final vm = await readySession();
      channel.incoming.add(List<int>.filled(100, 1));
      await _settle();

      await vm.bargeIn();
      expect(channel.sentTypes, contains('barge_in'));
      expect(sink.calls.last, 'flush');

      // Still in flight from the reply being cancelled: must not play.
      channel.incoming.add(List<int>.filled(200, 2));
      await _settle();
      expect(sink.pushed.map((p) => p.length), [100]);

      // The server confirms; audio after that is the next reply's.
      channel.incoming.add(jsonEncode({'type': 'cancelled'}));
      await _settle();
      channel.incoming.add(List<int>.filled(300, 3));
      await _settle();
      expect(sink.pushed.map((p) => p.length), [100, 300]);
    });

    test('a barge-in the server detected (cancelled) stops what is playing', () async {
      await readySession();
      channel.incoming.add(List<int>.filled(100, 1));
      await _settle();
      channel.incoming.add(jsonEncode({'type': 'cancelled'}));
      await _settle();

      expect(sink.calls.last, 'flush');
    });

    test('says audio is unavailable when the native engine could not start', () async {
      sink = _RecordingSink(available: false);
      await readySession();

      expect(container.read(liveVoiceViewModelProvider).audioUnavailable, isTrue);
    });

    test('a working sink does not claim audio is unavailable', () async {
      await readySession();

      expect(container.read(liveVoiceViewModelProvider).audioUnavailable, isFalse);
    });
  });

  group('mic framing (INT-116)', () {
    List<List<int>> sentAudio() => channel.fakeSink.sent.whereType<List<int>>().toList();

    test('mic chunks reach the socket as exact 20 ms frames at the capture rate', () async {
      await readySession(captureRate: 16000);
      mic._pcm.add(List<int>.filled(1000, 1)); // 31.25 ms
      mic._pcm.add(List<int>.filled(1000, 2));
      await _settle();

      expect(sentAudio().map((f) => f.length), [640, 640, 640]);
      expect(sentAudio().expand((f) => f).toList(),
          [...List<int>.filled(1000, 1), ...List<int>.filled(920, 2)]);
    });

    test('the frame size follows the capture rate the server announced', () async {
      await readySession(captureRate: 24000);
      mic._pcm.add(List<int>.filled(1920, 1));
      await _settle();

      expect(sentAudio().map((f) => f.length), [960, 960]);
    });

    test('commit sends the partial last frame, padded with silence, before the commit frame', () async {
      final vm = await readySession(captureRate: 16000);
      mic._pcm.add(List<int>.filled(700, 1));
      await _settle();

      vm.commitUtterance();

      final audio = sentAudio();
      expect(audio.map((f) => f.length), [640, 640]);
      expect(audio.last.sublist(0, 60), List<int>.filled(60, 1));
      expect(audio.last.sublist(60).every((b) => b == 0), isTrue);
      final sent = channel.fakeSink.sent;
      expect((jsonDecode(sent.last as String) as Map)['type'], 'commit');
      expect(sent.indexOf(audio.last), sent.length - 2, reason: 'the audio precedes the commit');
    });
  });

  test('an actions frame populates pendingActionsProvider for the approval card', () async {
    await readySession();
    channel.incoming.add(jsonEncode({
      'type': 'actions',
      'actions': [
        {
          'type': 'add_task',
          'params': {'title': 'Buy milk', 'priority': 'medium', 'category': 'personal'},
        },
      ],
    }));
    await _settle();

    final actions = container.read(pendingActionsProvider);
    expect(actions, hasLength(1));
    expect((actions.single as Map)['type'], 'add_task');
  });

  test('a confirmation_invalid error clears pending confirmation, returns to listening, and shows chatApprovalExpiredNotice', () async {
    await readySession();
    channel.incoming.add(jsonEncode({
      'type': 'confirmation',
      'tools': ['run_command'],
      'calls': [
        {
          'approval_id': 'appr-1',
          'call_id': 'step_1',
          'tool': 'run_command',
          'arguments': {'command': 'pytest'},
        },
      ],
    }));
    await _settle();
    expect(container.read(liveVoiceViewModelProvider).phase, 'awaiting_confirmation');
    expect(container.read(liveVoicePendingProvider), hasLength(1));

    channel.incoming.add(jsonEncode({
      'type': 'error',
      'error': 'confirmation_invalid',
      'message': 'There is nothing pending to confirm.',
    }));
    await _settle();

    final state = container.read(liveVoiceViewModelProvider);
    expect(state.phase, 'listening');
    expect(state.pendingTools, isEmpty);
    expect(state.error, currentL10n.chatApprovalExpiredNotice);
    expect(container.read(liveVoicePendingProvider), isEmpty);
  });
}
