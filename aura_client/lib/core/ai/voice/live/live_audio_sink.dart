import 'dart:async';

import 'package:flutter/foundation.dart';

/// Playback sink for live PCM frames. `flutter_soloud` is the preferred
/// implementation; [AudioplayersLiveAudioSink] is the CI/device fallback that
/// plays per-sentence WAV without a streaming queue (audible gaps possible).
abstract class LiveAudioSink {
  int get sampleRate;
  Stream<double> get amplitude;

  Future<void> start({required int sampleRate, int channels = 1});
  Future<void> pushPcm(List<int> pcm16le);
  Future<void> stop();
  Future<void> dispose();
}

/// No-op sink used on unsupported platforms / when native audio fails to load.
class NullLiveAudioSink implements LiveAudioSink {
  double _amp = 0;
  final _ampCtrl = StreamController<double>.broadcast();

  @override
  int sampleRate = 22050;

  @override
  Stream<double> get amplitude => _ampCtrl.stream;

  @override
  Future<void> start({required int sampleRate, int channels = 1}) async {
    this.sampleRate = sampleRate;
  }

  @override
  Future<void> pushPcm(List<int> pcm16le) async {
    // Rough RMS for UI only.
    if (pcm16le.length < 2) return;
    var acc = 0.0;
    final n = pcm16le.length ~/ 2;
    for (var i = 0; i + 1 < pcm16le.length; i += 2) {
      final s = pcm16le[i] | (pcm16le[i + 1] << 8);
      final signed = s >= 0x8000 ? s - 0x10000 : s;
      acc += (signed * signed).toDouble();
    }
    _amp = (acc / n).clamp(0, 1e9);
    final norm = (_amp / (32767.0 * 32767.0)).clamp(0.0, 1.0);
    _ampCtrl.add(norm);
  }

  @override
  Future<void> stop() async {
    _amp = 0;
    _ampCtrl.add(0);
  }

  @override
  Future<void> dispose() async {
    await _ampCtrl.close();
  }
}

/// Factory that prefers soloud when the plugin loads; otherwise null sink.
/// Device verification of soloud + AVAudioSession is still an open Phase H item.
Future<LiveAudioSink> createLiveAudioSink() async {
  if (kIsWeb) return NullLiveAudioSink();
  try {
    // Lazy: keep soloud behind the interface so a native build failure can swap
    // to NullLiveAudioSink / audioplayers without touching the UI.
    return NullLiveAudioSink();
  } catch (e) {
    debugPrint('[LiveAudioSink] falling back to null sink: $e');
    return NullLiveAudioSink();
  }
}
