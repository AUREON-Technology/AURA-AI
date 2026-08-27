import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:record/record.dart';

/// Continuous PCM mic source for live voice (Phase H).
///
/// Uses `record` 7.x `startStream` with the voice-communication path so platform
/// AEC/NS can engage. Does not change the `record ^7.1.1` pin.
class LiveMicSource {
  LiveMicSource({AudioRecorder? recorder}) : _recorder = recorder ?? AudioRecorder();

  final AudioRecorder _recorder;
  StreamSubscription<List<int>>? _sub;
  final _pcm = StreamController<List<int>>.broadcast();
  final _amplitude = StreamController<double>.broadcast();

  Stream<List<int>> get pcm => _pcm.stream;
  Stream<double> get amplitude => _amplitude.stream;

  bool get isSupported {
    if (kIsWeb) return false;
    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
      case TargetPlatform.iOS:
        return true;
      default:
        return false;
    }
  }

  Future<bool> ensurePermission() => _recorder.hasPermission();

  Future<void> start({int sampleRate = 16000}) async {
    if (!isSupported) {
      throw UnsupportedError('live mic is mobile-only');
    }
    final permitted = await ensurePermission();
    if (!permitted) {
      throw StateError('microphone permission denied');
    }

    final stream = await _recorder.startStream(
      RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: sampleRate,
        numChannels: 1,
        echoCancel: true,
        noiseSuppress: true,
        autoGain: true,
        androidConfig: const AndroidRecordConfig(
          audioSource: AndroidAudioSource.voiceCommunication,
          audioManagerMode: AudioManagerMode.modeInCommunication,
          speakerphone: true,
        ),
      ),
    );

    _sub = stream.listen((chunk) {
      _pcm.add(chunk);
      _amplitude.add(_rms(chunk));
    });
  }

  Future<void> stop() async {
    await _sub?.cancel();
    _sub = null;
    if (await _recorder.isRecording()) {
      await _recorder.stop();
    }
    _amplitude.add(0);
  }

  Future<void> dispose() async {
    await stop();
    await _recorder.dispose();
    await _pcm.close();
    await _amplitude.close();
  }

  double _rms(List<int> pcm) {
    if (pcm.length < 2) return 0;
    var acc = 0.0;
    final n = pcm.length ~/ 2;
    for (var i = 0; i + 1 < pcm.length; i += 2) {
      final s = pcm[i] | (pcm[i + 1] << 8);
      final signed = s >= 0x8000 ? s - 0x10000 : s;
      acc += signed * signed;
    }
    final mean = acc / n;
    return (mean / (32767.0 * 32767.0)).clamp(0.0, 1.0);
  }
}
