import 'dart:typed_data';

import 'package:flutter_soloud/flutter_soloud.dart';

import 'live_audio_sink.dart';

/// [LivePcmBackend] on flutter_soloud's buffered PCM streams (INT-102).
///
/// Every native call is here; the buffering, ordering and barge-in logic is
/// [BufferedLiveAudioSink]'s and is unit-tested against a fake. Whether this plays audibly on a
/// phone (and coexists with the mic's voice-communication audio session) needs a device.
class SoloudPcmBackend implements LivePcmBackend {
  SoloudPcmBackend({SoLoud? soloud}) : _soloud = soloud ?? SoLoud.instance;

  final SoLoud _soloud;

  /// Seconds of audio a stream waits for before it starts (or resumes after running dry between
  /// sentences). The server sends whole sentences, so this is reached on the first one.
  static const _bufferingSeconds = 0.25;

  /// The engine is a process-wide singleton: started once, on the first live session, and left
  /// running for later ones (each session releases its own streams).
  @override
  Future<void> init() async {
    if (_soloud.isInitialized) return;
    await _soloud.init();
  }

  @override
  Future<LivePcmVoice> open({required int sampleRate, required int channels}) async {
    final source = _soloud.setBufferStream(
      sampleRate: sampleRate,
      channels: channels == 2 ? Channels.stereo : Channels.mono,
      format: BufferType.s16le,
      // Played audio is freed as it goes; one reply's stream has one listener.
      bufferingType: BufferingType.released,
      bufferingTimeNeeds: _bufferingSeconds,
    );
    try {
      final handle = await _soloud.play(source);
      return _SoloudVoice(_soloud, source, handle);
    } catch (_) {
      await _soloud.disposeSource(source);
      rethrow;
    }
  }
}

class _SoloudVoice implements LivePcmVoice {
  _SoloudVoice(this._soloud, this._source, this._handle);

  final SoLoud _soloud;
  final AudioSource _source;
  final SoundHandle _handle;
  bool _released = false;

  @override
  void append(Uint8List pcm16le) => _soloud.addAudioDataStream(_source, pcm16le);

  @override
  void end() => _soloud.setDataIsEnded(_source);

  @override
  bool get isFinished => _released || !_soloud.getIsValidVoiceHandle(_handle);

  @override
  Future<void> release() async {
    if (_released) return;
    _released = true;
    if (!_soloud.isInitialized) return;
    // Disposing the source stops every voice playing it, at once.
    try {
      await _soloud.disposeSource(_source);
    } catch (_) {
      // Already gone: a released-buffer stream frees itself once it has played out.
    }
  }
}
