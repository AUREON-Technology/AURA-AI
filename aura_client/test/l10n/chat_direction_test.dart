import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/aura_theme_v2.dart';
import 'package:aura_client/features/chat/presentation/widgets/message_bubble.dart';
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

// A message reads in its own direction whatever the app language, and code inside it stays
// left-to-right (docs/plans/arabic-language_plan.md, stage 2).

Message _assistant(String content) => Message(
      id: 'a1',
      conversationId: 'conv-1',
      role: 'assistant',
      content: content,
      createdAt: DateTime.utc(2026, 11, 17, 12),
    );

class _NoSentImages extends SentChatImages {
  @override
  Map<String, List<ChatImageAttachment>> build() => const {};
}

Widget _bubble(Message message) => ProviderScope(
      overrides: [sentChatImagesProvider.overrideWith(_NoSentImages.new)],
      child: MaterialApp(
        theme: AuraThemeV2.light,
        home: Scaffold(body: SingleChildScrollView(child: MessageBubble(message: message, isUser: false))),
      ),
    );

void main() {
  testWidgets('an Arabic reply in an English app reads right-to-left, its code left-to-right',
      (tester) async {
    await tester.pumpWidget(_bubble(_assistant('شغّل الأمر ده:\n\n```\nfoo();\n```\n')));
    await tester.pump();

    expect(tester.takeException(), isNull);
    expect(Directionality.of(tester.element(find.byType(MarkdownBody))), TextDirection.rtl);
    expect(Directionality.of(tester.element(find.text('foo();'))), TextDirection.ltr);
  });

  testWidgets('an English reply stays left-to-right', (tester) async {
    await tester.pumpWidget(_bubble(_assistant('Run this:\n\n```\nfoo();\n```\n')));
    await tester.pump();

    expect(Directionality.of(tester.element(find.byType(MarkdownBody))), TextDirection.ltr);
  });
}
