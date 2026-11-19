import 'dart:async';

import 'package:flutter/foundation.dart';

import 'soloud_pcm_backend.dart';

/// Playback sink for the live-voice reply audio (INT-102).
///
/// The server (`aura_core/voice/session.py` `_speak_sentence`) sends each spoken sentence as one
/// binary frame of raw PCM: signed 16-bit little-endian, mono, at the rate its `ready` frame's
/// `playback` block announced (a `playback` frame announces a change before the audio that uses
/// it). `audio_end` follows the last sentence of a reply; `cancelled` means the server dropped the
/// reply (a barge-in, either side). So the sink needs: [start] (the rate), [pushPcm] (queue a
/// sentence), [endOfUtterance] (let what is queued play out), [flush] (stop now) and [dispose].
abstract class LiveAudioSink {
  int get sampleRate;
  Stream<double> get amplitude;

  /// False when native audio could not start: replies then arrive as text only, and the screen
  /// says so instead of silently playing nothing.
  bool get isAvailable;

  Future<void> start({required int sampleRate, int channels = 1});
  Future<void> pushPcm(List<int> pcm16le);

  /// The reply is complete (`audio_end`): what is queued plays to the end.
  Future<void> endOfUtterance();

  /// Barge-in: everything queued or playing stops immediately.
  Future<void> flush();

  Future<void> dispose();
}

/// The native side of a [BufferedLiveAudioSink]: kept this thin so the buffering and flush logic
/// is testable without a device. [SoloudPcmBackend] is the real one.
abstract class LivePcmBackend {
  /// Starts the audio engine. Throws when native audio is unavailable on this device/build.
  Future<void> init();

  /// Opens a playing PCM stream (s16le) that audio can be appended to.
  Future<LivePcmVoice> open({required int sampleRate, required int channels});
}

/// One open playback stream.
abstract class LivePcmVoice {
  void append(Uint8List pcm16le);

  /// No more audio will come: play what is queued, then stop.
  void end();

  /// Whether it has played out (or been stopped), so it can be released.
  bool get isFinished;

  /// Stops playback now and frees the stream.
  Future<void> release();
}

/// No-op sink used on unsupported platforms / when native audio fails to load.
class NullLiveAudioSink implements LiveAudioSink {
  NullLiveAudioSink({this.unavailableReason});

  /// Why native audio is not available, for the log.
  final String? unavailableReason;

  final _ampCtrl = StreamController<double>.broadcast();

  @override
  int sampleRate = 22050;

  @override
  bool get isAvailable => false;

  @override
  Stream<double> get amplitude => _ampCtrl.stream;

  @override
  Future<void> start({required int sampleRate, int channels = 1}) async {
    this.sampleRate = sampleRate;
  }

  @override
  Future<void> pushPcm(List<int> pcm16le) async {
    if (!_ampCtrl.isClosed) _ampCtrl.add(pcmLevel(pcm16le));
  }

  @override
  Future<void> endOfUtterance() async {}

  @override
  Future<void> flush() async {
    if (!_ampCtrl.isClosed) _ampCtrl.add(0);
  }

  @override
  Future<void> dispose() async {
    await _ampCtrl.close();
  }
}

/// Mean-square level of s16le [pcm] in 0..1, for the UI only.
double pcmLevel(List<int> pcm) {
  if (pcm.length < 2) return 0;
  var acc = 0.0;
  final n = pcm.length ~/ 2;
  for (var i = 0; i + 1 < pcm.length; i += 2) {
    final s = pcm[i] | (pcm[i + 1] << 8);
    final signed = s >= 0x8000 ? s - 0x10000 : s;
    acc += (signed * signed).toDouble();
  }
  return (acc / n / (32767.0 * 32767.0)).clamp(0.0, 1.0);
}

/// Streams reply audio into [LivePcmBackend] voices: one per reply, opened when its first audio
/// arrives, ended on [endOfUtterance] so it drains, released at once on [flush].
///
/// Calls are applied strictly in order (each waits for the one before), so a [flush] that follows
/// a [pushPcm] always cancels that audio rather than racing it.
class BufferedLiveAudioSink implements LiveAudioSink {
  BufferedLiveAudioSink(this._backend);

  final LivePcmBackend _backend;
  final _ampCtrl = StreamController<double>.broadcast();

  LivePcmVoice? _current;
  final List<LivePcmVoice> _draining = [];

  /// A trailing odd byte, held until the next chunk completes its sample.
  int? _carry;
  int _channels = 1;
  bool _disposed = false;
  Future<void> _queue = Future<void>.value();

  @override
  int sampleRate = 22050;

  @override
  bool get isAvailable => true;

  @override
  Stream<double> get amplitude => _ampCtrl.stream;

  Future<void> _serial(Future<void> Function() op) {
    final next = _queue.then((_) async {
      try {
        await op();
      } catch (e) {
        debugPrint('[LiveAudioSink] $e');
      }
    });
    _queue = next;
    return next;
  }

  @override
  Future<void> start({required int sampleRate, int channels = 1}) => _serial(() async {
        if (_disposed) return;
        if (sampleRate != this.sampleRate || channels != _channels) {
          // A stream plays at one rate: end the old one (its audio still plays out) and open the
          // next at the new rate when audio for it arrives.
          _endCurrent();
        }
        this.sampleRate = sampleRate;
        _channels = channels;
      });

  @override
  Future<void> pushPcm(List<int> pcm16le) => _serial(() async {
        if (_disposed || pcm16le.isEmpty) return;
        if (!_ampCtrl.isClosed) _ampCtrl.add(pcmLevel(pcm16le));
        final aligned = _align(pcm16le);
        if (aligned.isEmpty) return;
        var voice = _current;
        if (voice == null) {
          await _releaseFinished();
          voice = await _backend.open(sampleRate: sampleRate, channels: _channels);
          if (_disposed) {
            await voice.release();
            return;
          }
          _current = voice;
        }
        try {
          voice.append(aligned);
        } catch (e) {
          // The engine refused the stream (full, already ended): drop it; the next chunk opens a
          // fresh one rather than every later sentence failing the same way.
          debugPrint('[LiveAudioSink] dropping stream: $e');
          if (identical(_current, voice)) _current = null;
          await voice.release();
        }
      });

  @override
  Future<void> endOfUtterance() => _serial(() async {
        _carry = null;
        _endCurrent();
      });

  @override
  Future<void> flush() => _serial(_stopAll);

  @override
  Future<void> dispose() async {
    await _serial(() async {
      _disposed = true;
      await _stopAll();
    });
    await _ampCtrl.close();
  }

  Uint8List _align(List<int> pcm) {
    final carry = _carry;
    final bytes = carry == null ? pcm : [carry, ...pcm];
    _carry = bytes.length.isOdd ? bytes.last : null;
    return Uint8List.fromList(bytes.sublist(0, bytes.length - bytes.length % 2));
  }

  void _endCurrent() {
    final voice = _current;
    _current = null;
    if (voice == null) return;
    try {
      voice.end();
    } catch (e) {
      debugPrint('[LiveAudioSink] end: $e');
    }
    _draining.add(voice);
  }

  Future<void> _releaseFinished() async {
    final done = _draining.where((v) => v.isFinished).toList();
    _draining.removeWhere(done.contains);
    for (final voice in done) {
      await voice.release();
    }
  }

  Future<void> _stopAll() async {
    _carry = null;
    final voices = [?_current, ..._draining];
    _current = null;
    _draining.clear();
    for (final voice in voices) {
      try {
        await voice.release();
      } catch (e) {
        debugPrint('[LiveAudioSink] release: $e');
      }
    }
    if (!_ampCtrl.isClosed) _ampCtrl.add(0);
  }
}

/// Builds the playback sink for a live session: flutter_soloud's buffered stream, initialised
/// here -- when a live session first asks for it, not at app start. Falls back to
/// [NullLiveAudioSink] only when the engine cannot start; that sink reports
/// [LiveAudioSink.isAvailable] false so the screen can say audio is off.
///
/// Device verification of soloud alongside the mic's voice-communication session is still open.
Future<LiveAudioSink> createLiveAudioSink({LivePcmBackend? backend}) async {
  if (kIsWeb && backend == null) {
    return NullLiveAudioSink(unavailableReason: 'live voice playback is not supported on web');
  }
  final engine = backend ?? SoloudPcmBackend();
  try {
    await engine.init();
    return BufferedLiveAudioSink(engine);
  } catch (e) {
    debugPrint('[LiveAudioSink] native audio unavailable, replies will be text only: $e');
    return NullLiveAudioSink(unavailableReason: e.toString());
  }
}
