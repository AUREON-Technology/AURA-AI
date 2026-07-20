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

    test('parses tool_calls with a nested actions array', () {
      final events = feedAll([
        'event: tool_calls\ndata: {"actions": [{"type": "add_task", "params": {"title": "x"}}]}\n\n',
      ]);
      expect(events[0].kind, SseEventKind.toolCalls);
      expect(events[0].actions, [
        {'type': 'add_task', 'params': {'title': 'x'}},
      ]);
    });

    test('parses an error event', () {
      final events = feedAll(['event: error\ndata: {"message": "boom"}\n\n']);
      expect(events[0].kind, SseEventKind.error);
      expect(events[0].message, 'boom');
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
