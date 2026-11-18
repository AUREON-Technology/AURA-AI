// C1 / INT-113: a chat too long for the server's sharing limits (200 messages, 200,000
// characters: `aura_core/domain/sharing.py`) is shared as its most recent messages that fit,
// in order, and the user is told how many older ones were left out. It used to send every row
// and get a 422.

import 'package:aura_client/features/workspaces/data/workspace_models.dart';
import 'package:aura_client/features/workspaces/presentation/share_with_team_button.dart';
import 'package:flutter_test/flutter_test.dart';

List<SharedChatMessage> _chat(int count, {int length = 10}) => [
      for (var i = 0; i < count; i++)
        SharedChatMessage(role: i.isEven ? 'user' : 'assistant', text: '$i'.padRight(length, 'x')),
    ];

int _chars(List<SharedChatMessage> messages) =>
    messages.fold(0, (sum, m) => sum + m.text.runes.length);

void main() {
  test('a chat within both limits is shared whole', () {
    final chat = _chat(12);
    final fit = fitShareLimits(chat);
    expect(fit.messages, chat);
    expect(fit.omitted, 0);
  });

  test('past 200 messages, the latest 200 go, oldest first', () {
    final chat = _chat(230);
    final fit = fitShareLimits(chat);
    expect(fit.messages, hasLength(kMaxSharedMessages));
    expect(fit.messages.first, same(chat[30]));
    expect(fit.messages.last, same(chat.last));
    expect(fit.omitted, 30);
  });

  test('past 200,000 characters, the latest messages that fit go', () {
    final chat = _chat(50, length: 9000); // 450,000 characters
    final fit = fitShareLimits(chat);
    expect(_chars(fit.messages), lessThanOrEqualTo(kMaxSharedChars));
    expect(fit.messages, hasLength(22));
    expect(fit.messages.last, same(chat.last));
    expect(fit.messages.first, same(chat[28]));
    expect(fit.omitted, 28);
  });

  test('characters are counted the way the server counts them (code points)', () {
    // Each emoji is two UTF-16 units but one character to the server.
    final chat = [
      for (var i = 0; i < 3; i++) SharedChatMessage(role: 'user', text: '😀' * 100000),
    ];
    final fit = fitShareLimits(chat);
    expect(fit.messages, hasLength(2));
    expect(fit.omitted, 1);
  });

  test('a latest message longer than the whole limit is cut to it rather than shared as nothing', () {
    final chat = [
      const SharedChatMessage(role: 'user', text: 'short'),
      SharedChatMessage(role: 'assistant', text: 'y' * (kMaxSharedChars + 5)),
    ];
    final fit = fitShareLimits(chat);
    expect(fit.messages, hasLength(1));
    expect(fit.messages.single.text.runes.length, lessThanOrEqualTo(kMaxSharedChars));
    expect(fit.omitted, 1);
  });

  test('an empty chat stays empty', () {
    final fit = fitShareLimits(const []);
    expect(fit.messages, isEmpty);
    expect(fit.omitted, 0);
  });
}
