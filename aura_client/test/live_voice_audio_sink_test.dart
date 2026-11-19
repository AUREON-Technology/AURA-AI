// INT-102: `createLiveAudioSink()` returned a no-op sink on every path, so live voice never played
// a word of the server's speech. The buffering/flush logic lives in [BufferedLiveAudioSink] over a
// thin [LivePcmBackend]; these tests drive it with a fake backend (no device, no native audio).
// Whether flutter_soloud actually makes sound on a phone is not something a test here can show.

import 'dart:typed_data';

import 'package:aura_client/core/ai/voice/live/live_audio_sink.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeVoice implements LivePcmVoice {
  _FakeVoice(this.sampleRate, this.channels);

  final int sampleRate;
  final int channels;
  final appended = <List<int>>[];
  bool ended = false;
  bool released = false;
  bool finished = false;
  Object? appendError;

  List<int> get bytes => appended.expand((c) => c).toList();

  @override
  void append(Uint8List pcm) {
    if (appendError != null) throw appendError!;
    appended.add(List<int>.of(pcm));
  }

  @override
  void end() => ended = true;

  @override
  bool get isFinished => finished || released;

  @override
  Future<void> release() async => released = true;
}

class _FakeBackend implements LivePcmBackend {
  _FakeBackend({this.initError});

  final Object? initError;
  int inits = 0;
  final voices = <_FakeVoice>[];

  @override
  Future<void> init() async {
    inits++;
    if (initError != null) throw initError!;
  }

  @override
  Future<LivePcmVoice> open({required int sampleRate, required int channels}) async {
    final voice = _FakeVoice(sampleRate, channels);
    voices.add(voice);
    return voice;
  }
}

List<int> _pcm(int length, [int fill = 1]) => List<int>.filled(length, fill);

void main() {
  group('createLiveAudioSink', () {
    test('returns a real, available sink when the native engine starts', () async {
      final backend = _FakeBackend();
      final sink = await createLiveAudioSink(backend: backend);

      expect(sink, isA<BufferedLiveAudioSink>());
      expect(sink.isAvailable, isTrue);
      expect(backend.inits, 1, reason: 'initialised when a session asks for it, not at app start');
      await sink.dispose();
    });

    test('falls back to the null sink, and says so, only when the engine fails to start', () async {
      final sink = await createLiveAudioSink(backend: _FakeBackend(initError: StateError('no lib')));

      expect(sink, isA<NullLiveAudioSink>());
      expect(sink.isAvailable, isFalse, reason: 'the screen must be able to say audio is off');
      expect((sink as NullLiveAudioSink).unavailableReason, contains('no lib'));
      await sink.dispose();
    });
  });

  group('BufferedLiveAudioSink', () {
    late _FakeBackend backend;
    late BufferedLiveAudioSink sink;

    setUp(() {
      backend = _FakeBackend();
      sink = BufferedLiveAudioSink(backend);
    });

    tearDown(() => sink.dispose());

    test('opens one stream at the announced rate and queues every chunk on it in order', () async {
      await sink.start(sampleRate: 24000);
      await sink.pushPcm(_pcm(400, 1));
      await sink.pushPcm(_pcm(200, 2));

      expect(backend.voices, hasLength(1));
      final voice = backend.voices.single;
      expect(voice.sampleRate, 24000);
      expect(voice.channels, 1);
      expect(voice.bytes, [..._pcm(400, 1), ..._pcm(200, 2)]);
    });

    test('nothing opens before audio arrives', () async {
      await sink.start(sampleRate: 22050);
      expect(backend.voices, isEmpty);
    });

    test('keeps 16-bit samples whole when a chunk ends on an odd byte', () async {
      await sink.start(sampleRate: 22050);
      await sink.pushPcm([1, 2, 3]);
      await sink.pushPcm([4, 5, 6, 7, 8]);

      final voice = backend.voices.single;
      expect(voice.appended.every((c) => c.length.isEven), isTrue);
      expect(voice.bytes, [1, 2, 3, 4, 5, 6, 7, 8]);
    });

    test('end of utterance lets the queued audio play out, and the next reply gets a new stream', () async {
      await sink.start(sampleRate: 22050);
      await sink.pushPcm(_pcm(100));
      await sink.endOfUtterance();

      final first = backend.voices.single;
      expect(first.ended, isTrue);
      expect(first.released, isFalse, reason: 'ending is not cutting off: it drains');

      await sink.pushPcm(_pcm(100, 9));
      expect(backend.voices, hasLength(2));
      expect(backend.voices.last.bytes, _pcm(100, 9));
    });

    test('a drained stream is released once it has finished playing', () async {
      await sink.start(sampleRate: 22050);
      await sink.pushPcm(_pcm(100));
      await sink.endOfUtterance();
      final first = backend.voices.single..finished = true;

      await sink.pushPcm(_pcm(100));

      expect(first.released, isTrue);
    });

    test('barge-in flush stops everything queued or playing at once', () async {
      await sink.start(sampleRate: 22050);
      await sink.pushPcm(_pcm(100));
      await sink.endOfUtterance(); // previous sentence still draining
      await sink.pushPcm(_pcm(100)); // and the current one

      await sink.flush();

      expect(backend.voices, hasLength(2));
      expect(backend.voices.every((v) => v.released), isTrue);
    });

    test('after a flush the next audio starts a fresh stream; nothing from before leaks into it', () async {
      await sink.start(sampleRate: 22050);
      await sink.pushPcm([1, 2, 3]); // odd byte held back
      await sink.flush();
      await sink.pushPcm([7, 8]);

      expect(backend.voices, hasLength(2));
      expect(backend.voices.last.bytes, [7, 8], reason: 'the held-back byte belonged to the cancelled reply');
    });

    test('a rate change ends the stream at the old rate and opens one at the new rate', () async {
      await sink.start(sampleRate: 22050);
      await sink.pushPcm(_pcm(100));
      await sink.start(sampleRate: 24000);
      await sink.pushPcm(_pcm(100));

      expect(backend.voices.map((v) => v.sampleRate), [22050, 24000]);
      expect(backend.voices.first.ended, isTrue);
      expect(backend.voices.first.released, isFalse);
    });

    test('start at the same rate does not cut off the stream in progress', () async {
      await sink.start(sampleRate: 22050);
      await sink.pushPcm(_pcm(100));
      await sink.start(sampleRate: 22050);
      await sink.pushPcm(_pcm(100));

      expect(backend.voices, hasLength(1));
      expect(backend.voices.single.ended, isFalse);
    });

    test('a stream the engine refuses is dropped and the next chunk opens a new one', () async {
      await sink.start(sampleRate: 22050);
      await sink.pushPcm(_pcm(100));
      backend.voices.single.appendError = StateError('buffer full');

      await sink.pushPcm(_pcm(100)); // refused: must not throw into the socket listener
      await sink.pushPcm(_pcm(100, 3));

      expect(backend.voices.first.released, isTrue);
      expect(backend.voices.last.bytes, _pcm(100, 3));
    });

    test('reports playback amplitude, and silence after a flush', () async {
      final amps = <double>[];
      final sub = sink.amplitude.listen(amps.add);
      await sink.start(sampleRate: 22050);
      await sink.pushPcm(Uint8List.fromList([0xff, 0x7f, 0xff, 0x7f])); // full-scale positive
      await sink.flush();
      await Future<void>.delayed(Duration.zero);

      expect(amps.first, greaterThan(0.9));
      expect(amps.last, 0);
      await sub.cancel();
    });

    test('dispose releases every stream; later calls are harmless', () async {
      await sink.start(sampleRate: 22050);
      await sink.pushPcm(_pcm(100));
      await sink.dispose();

      expect(backend.voices.single.released, isTrue);
      await sink.pushPcm(_pcm(100));
      expect(backend.voices, hasLength(1));
    });
  });

  test('the null sink is unavailable and accepts every call', () async {
    final sink = NullLiveAudioSink(unavailableReason: 'web');
    expect(sink.isAvailable, isFalse);
    await sink.start(sampleRate: 16000);
    await sink.pushPcm(_pcm(10));
    await sink.endOfUtterance();
    await sink.flush();
    await sink.dispose();
  });
}
