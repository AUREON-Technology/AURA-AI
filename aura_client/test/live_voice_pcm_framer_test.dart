// INT-116: the server's endpointer counts every binary frame as `frame_ms` (20 ms) of audio
// (`aura_core/voice/session.py`, `turn_taking.py`), but `record`'s stream hands over chunks of
// whatever size the platform buffers. The framer re-cuts them into exact 20 ms frames.

import 'dart:typed_data';

import 'package:aura_client/core/ai/voice/live/pcm_framer.dart';
import 'package:flutter_test/flutter_test.dart';

List<int> _ramp(int length, {int start = 0}) => List<int>.generate(length, (i) => (start + i) & 0xff);

void main() {
  test('a 20 ms frame is sample_rate x 0.02 x 2 bytes x channels', () {
    expect(PcmFramer(sampleRate: 16000).frameBytes, 640);
    expect(PcmFramer(sampleRate: 48000).frameBytes, 1920);
    expect(PcmFramer(sampleRate: 24000, channels: 2).frameBytes, 1920);
    expect(PcmFramer(sampleRate: 8000).frameBytes, 320);
  });

  test('re-cuts arbitrary chunks into exact frames, carrying the remainder across chunks', () {
    final framer = PcmFramer(sampleRate: 16000);
    final input = _ramp(640 * 3 + 100);

    final out = <Uint8List>[
      ...framer.add(input.sublist(0, 1000)), // 1 frame + 360 carried
      ...framer.add(input.sublist(1000, 1001)), // odd byte, nothing complete
      ...framer.add(input.sublist(1001)), // completes 2 more, 100 left over
    ];

    expect(out.map((f) => f.length), [640, 640, 640]);
    expect(out.expand((f) => f).toList(), input.sublist(0, 640 * 3),
        reason: 'no byte is dropped, duplicated or reordered');
    expect(framer.pendingBytes, 100);
  });

  test('a chunk smaller than a frame produces nothing until enough has arrived', () {
    final framer = PcmFramer(sampleRate: 16000);
    expect(framer.add(_ramp(200)), isEmpty);
    expect(framer.add(_ramp(200)), isEmpty);
    final frames = framer.add(_ramp(240));
    expect(frames, hasLength(1));
    expect(frames.single, hasLength(640));
  });

  test('flush pads the remainder with silence to one whole frame, then starts clean', () {
    final framer = PcmFramer(sampleRate: 16000);
    framer.add(_ramp(700, start: 1));

    final last = framer.flush();

    expect(last, isNotNull);
    expect(last!.length, 640);
    expect(last.sublist(0, 60), _ramp(60, start: 1 + 640));
    expect(last.sublist(60).every((b) => b == 0), isTrue, reason: 'padding is digital silence');
    expect(framer.pendingBytes, 0);
    expect(framer.flush(), isNull, reason: 'nothing buffered, nothing to send');
  });

  test('flush sends nothing when the audio ended exactly on a frame boundary', () {
    final framer = PcmFramer(sampleRate: 16000);
    expect(framer.add(_ramp(1280)), hasLength(2));
    expect(framer.flush(), isNull);
  });

  test('reset drops a partial frame without sending it', () {
    final framer = PcmFramer(sampleRate: 16000);
    framer.add(_ramp(300));
    framer.reset();
    expect(framer.pendingBytes, 0);
    expect(framer.add(_ramp(640, start: 7)).single, _ramp(640, start: 7));
  });

  test('every emitted frame is its own buffer, safe to hand to the socket and forget', () {
    final framer = PcmFramer(sampleRate: 8000);
    final frames = framer.add(_ramp(640));
    frames.first[0] = 0xAA;
    expect(frames[1][0], isNot(0xAA));
  });
}
