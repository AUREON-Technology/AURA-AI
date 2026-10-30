import 'package:flutter_test/flutter_test.dart';
import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';

List<SseChatEvent> feedAll(List<String> chunks) {
  final parser = SseFrameParser();
  final events = <SseChatEvent>[];
  for (final c in chunks) {
    events.addAll(parser.feed(c));
  }
  return events;
}

void main() {
  group('SseFrameParser', () {
    test('ignores SSE comment frames such as the server keepalive', () {
      final events = feedAll([
        ': keepalive\n\n',
        'event: token\ndata: {"text": "Hi"}\n\n',
        ': keepalive\n\n',
      ]);
      expect(events.map((e) => e.kind).toList(), [SseEventKind.token]);
      expect(events.single.text, 'Hi');
    });

    test('parses a single complete token frame', () {
      final events = feedAll(['event: token\ndata: {"text": "Hello"}\n\n']);
      expect(events.length, 1);
      expect(events[0].kind, SseEventKind.token);
      expect(events[0].text, 'Hello');
    });

    test('parses multiple frames delivered in one chunk', () {
      final events = feedAll([
        'event: token\ndata: {"text": "Hi "}\n\n'
            'event: token\ndata: {"text": "there"}\n\n'
            'event: done\ndata: {}\n\n',
      ]);
      expect(events.map((e) => e.kind).toList(), [SseEventKind.token, SseEventKind.token, SseEventKind.done]);
      expect(events[0].text, 'Hi ');
      expect(events[1].text, 'there');
    });

    test('parses a frame split across chunk boundaries', () {
      final events = feedAll([
        'event: tok',
        'en\ndata: {"tex',
        't": "partial"}\n\n',
      ]);
      expect(events.length, 1);
      expect(events[0].kind, SseEventKind.token);
      expect(events[0].text, 'partial');
    });

    test('parses tool_calls with a nested calls array', () {
      final events = feedAll([
        'event: tool_calls\ndata: {"calls": [{"name": "calculator", "arguments": {"expr": "1+1"}}]}\n\n',
      ]);
      expect(events[0].kind, SseEventKind.toolCalls);
      expect(events[0].actions, [
        {'name': 'calculator', 'arguments': {'expr': '1+1'}},
      ]);
    });

    test('parses actions frames for PendingActionsExecutor', () {
      final events = feedAll([
        'event: actions\ndata: {"actions": [{"type": "add_task", "params": {"title": "Buy milk"}}]}\n\n',
      ]);
      expect(events[0].kind, SseEventKind.actions);
      expect(events[0].actions, [
        {'type': 'add_task', 'params': {'title': 'Buy milk'}},
      ]);
    });

    test('parses a step progress frame', () {
      final events = feedAll([
        'event: step\ndata: {"step_id": "s1", "kind": "tool", "status": "running"}\n\n',
      ]);
      expect(events[0].kind, SseEventKind.step);
    });

    test('parses an error event', () {
      final events = feedAll(['event: error\ndata: {"message": "boom"}\n\n']);
      expect(events[0].kind, SseEventKind.error);
      expect(events[0].message, 'boom');
    });

    test('parses a citations event', () {
      final events = feedAll([
        'event: citations\ndata: {"citations": ["my-doc@v1 (page 4 > Heading)"]}\n\n',
      ]);
      expect(events[0].kind, SseEventKind.citations);
      expect(events[0].citations, ['my-doc@v1 (page 4 > Heading)']);
    });

    test('parses a citations event split across chunk boundaries', () {
      final events = feedAll([
        'event: citat',
        'ions\ndata: {"citations": ["doc',
        'A", "docB"]}\n\n',
      ]);
      expect(events.length, 1);
      expect(events[0].kind, SseEventKind.citations);
      expect(events[0].citations, ['docA', 'docB']);
    });

    test('parses a revision event', () {
      final events = feedAll([
        'event: revision\ndata: {"reason": "output_guard_redaction", "text": "redacted answer"}\n\n',
      ]);
      expect(events[0].kind, SseEventKind.revision);
      expect(events[0].text, 'redacted answer');
    });

    test('done event carries a citations safety-net field when present', () {
      final events = feedAll([
        'event: done\ndata: {"tainted": false, "guards_complete": true, "citations": ["docA"]}\n\n',
      ]);
      expect(events[0].kind, SseEventKind.done);
      expect(events[0].citations, ['docA']);
    });

    test('done event maps turn_id, confidence, degraded, and confirmation', () {
      // The backend always sends needs_confirmation as a list of tool names, never a bare
      // boolean -- see aura_core/kernel/kernel.py's `done` frame.
      final events = feedAll([
        'event: done\ndata: {"turn_id":"t-1","confidence":0.2,"degraded":true,"needs_confirmation":["propose_health_log"],"tainted":true,"guards_complete":true,"usage":{"tokens":12}}\n\n',
      ]);
      final meta = events[0].turnMeta;
      expect(meta, isNotNull);
      expect(meta!.turnId, 't-1');
      expect(meta.confidence, 0.2);
      expect(meta.degraded, isTrue);
      expect(meta.needsConfirmation, isTrue);
      expect(meta.tainted, isTrue);
      expect(meta.showLowConfidence, isTrue);
    });

    test('done event with an empty needs_confirmation list is not confirmation-pending', () {
      final events = feedAll([
        'event: done\ndata: {"turn_id":"t-2","needs_confirmation":[]}\n\n',
      ]);
      expect(events[0].turnMeta!.needsConfirmation, isFalse);
    });

    test('step frame carries a label', () {
      final events = feedAll([
        'event: step\ndata: {"step_id": "s1", "kind": "tool", "status": "running", "label": "search"}\n\n',
      ]);
      expect(events[0].step?.label, 'search');
      expect(events[0].step?.status, 'running');
    });

    test('an unrecognised event type is reported as unknown, never as done', () {
      final events = feedAll(['event: some_future_frame\ndata: {"foo": "bar"}\n\n']);
      expect(events.length, 1);
      expect(events[0].kind, SseEventKind.unknown);
      expect(events[0].kind, isNot(SseEventKind.done));
      expect(events[0].unknownType, 'some_future_frame');
    });

    test('holds back an incomplete trailing frame until the next feed', () {
      final parser = SseFrameParser();
      final first = parser.feed('event: token\ndata: {"text": "a"}\n\n' 'event: token\ndata: {"tex');
      expect(first.length, 1);
      expect(first[0].text, 'a');

      final second = parser.feed('t": "b"}\n\n');
      expect(second.length, 1);
      expect(second[0].text, 'b');
    });

    test('malformed data payload does not throw and is dropped gracefully', () {
      expect(() => feedAll(['event: token\ndata: not json\n\n']), returnsNormally);
    });

    test('empty input yields no events', () {
      expect(feedAll(['']), isEmpty);
    });
  });
}
