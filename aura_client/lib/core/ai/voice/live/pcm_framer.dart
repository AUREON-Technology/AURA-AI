import 'dart:typed_data';

/// Re-cuts captured PCM into fixed-duration frames for `/api/v3/voice/live` (INT-116).
///
/// The server's endpointer counts every binary frame as `frame_ms` (20 ms) of audio
/// (`aura_core/voice/session.py` `LiveVoiceConfig.frame_ms`, `turn_taking.py`), and its VAD sizes
/// its analysis window the same way. `record`'s stream hands over chunks of whatever size the
/// platform's buffer happens to be (often 40-100 ms, sometimes an odd number of bytes), so sending
/// them as they come made the server's speech/silence timing wrong by that factor. This carries the
/// remainder of each chunk into the next and only ever emits whole frames.
class PcmFramer {
  PcmFramer({
    required int sampleRate,
    int channels = 1,
    int bytesPerSample = 2,
    int frameMs = 20,
  }) : frameBytes = sampleRate * frameMs ~/ 1000 * bytesPerSample * channels {
    if (frameBytes <= 0) {
      throw ArgumentError('frame of $frameMs ms at $sampleRate Hz has no samples');
    }
  }

  /// Bytes in one frame: `sample_rate x frame_ms/1000 x bytes_per_sample x channels`.
  final int frameBytes;

  final BytesBuilder _pending = BytesBuilder(copy: true);

  /// Bytes held back because they do not yet make a whole frame.
  int get pendingBytes => _pending.length;

  /// Adds [chunk] and returns every frame now complete, in order. Each is its own buffer.
  List<Uint8List> add(List<int> chunk) {
    if (chunk.isEmpty) return const [];
    _pending.add(chunk);
    if (_pending.length < frameBytes) return const [];
    final all = _pending.takeBytes();
    final whole = all.length - all.length % frameBytes;
    final frames = <Uint8List>[
      for (var offset = 0; offset < whole; offset += frameBytes)
        Uint8List.fromList(Uint8List.sublistView(all, offset, offset + frameBytes)),
    ];
    if (whole < all.length) _pending.add(Uint8List.sublistView(all, whole));
    return frames;
  }

  /// The partial frame still buffered, padded with silence (zero samples) to a whole frame, or
  /// `null` when nothing is buffered. Called when the utterance is committed, so its last few
  /// milliseconds reach the server instead of waiting for audio that will not come.
  Uint8List? flush() {
    if (_pending.isEmpty) return null;
    final rest = _pending.takeBytes();
    return Uint8List(frameBytes)..setRange(0, rest.length, rest);
  }

  /// Drops any partial frame (the session ended; it belongs to no utterance).
  void reset() => _pending.clear();
}
