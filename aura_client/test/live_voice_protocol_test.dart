import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/ai/voice/live/live_voice_protocol.dart';

void main() {
  group('LiveVoiceProtocol', () {
    test('auth / confirm / reauth encode', () {
      expect(
        const LiveAuthFrame(token: 'abc', attestation: {'x-device': '1'}).toJson()['type'],
        'auth',
      );
      // F-04: confirmation is by server-issued approval id; tool names are never sent.
      final confirm = const LiveConfirmFrame(approvalIds: ['apr.t.n']).toJson();
      expect(confirm['approval_ids'], ['apr.t.n']);
      expect(confirm.containsKey('tools'), isFalse);
      expect(const LiveReauthFrame(token: 'xyz').toJson()['type'], 'reauth');
    });

    test('ready dual clocks parse', () {
      final frame = LiveServerFrame.tryParse({
        'type': 'ready',
        'capture': {'sample_rate': 16000, 'channels': 1, 'format': 'pcm_s16le'},
        'playback': {'sample_rate': 22050, 'channels': 1, 'format': 'pcm_s16le'},
      });
      expect(frame, isA<LiveReadyFrame>());
      final ready = frame! as LiveReadyFrame;
      expect(ready.capture.sampleRate, 16000);
      expect(ready.playback.sampleRate, 22050);
    });

    test('confirmation and done parse', () {
      final conf = LiveServerFrame.tryParse({
        'type': 'confirmation',
        'tools': ['propose_task'],
        'text': 'confirm?',
      });
      expect(conf, isA<LiveConfirmationFrame>());
      final withCalls = LiveServerFrame.tryParse({
        'type': 'confirmation',
        'tools': ['propose_task'],
        'calls': [
          {'approval_id': 'apr.t.n', 'call_id': 'step_s1', 'tool': 'propose_task',
           'arguments': {'title': 'x'}},
        ],
        'text': 'confirm?',
      })! as LiveConfirmationFrame;
      expect(withCalls.calls.single['approval_id'], 'apr.t.n');
      final done = LiveServerFrame.tryParse({
        'type': 'done',
        'needs_confirmation': ['propose_task'],
      });
      expect(done, isA<LiveDoneFrame>());
      final actions = LiveServerFrame.tryParse({
        'type': 'actions',
        'actions': [
          {'type': 'add_task', 'params': {'title': 'Buy milk'}},
        ],
      });
      expect(actions, isA<LiveActionsFrame>());
      expect((actions! as LiveActionsFrame).actions.single['type'], 'add_task');
    });
  });
}
