import 'dart:convert';
import 'dart:typed_data';

import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/aura_theme_v2.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/presentation/widgets/message_bubble.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// A real 1x1 PNG, so `Image.memory` decodes it.
final _png = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
);

ChatImageAttachment _image(String name) => ChatImageAttachment(bytes: _png, name: name);

/// Records what each turn sent; the first call fails so the retry path can be exercised.
class _RecordingSseClient extends SseAgentClient {
  final calls = <({String message, List<ChatImageAttachment> images})>[];
  final bool failFirst;

  _RecordingSseClient(super.ref, {this.failFirst = false});

  @override
  Stream<SseChatEvent> streamAgentEvents({
    required String message,
    required String sessionId,
    required String agentId,
    bool useKnowledge = true,
    bool useMemory = true,
    bool useTools = true,
    List<String> approvalIds = const [],
    String? correlationId,
    int maxOutputTokens = 1024,
    double temperature = 0.3,
    List<ChatImageAttachment> images = const [],
    String? workspaceRoot,
    bool desktopTaskIsolation = false,
  }) async* {
    calls.add((message: message, images: images));
    if (failFirst && calls.length == 1) {
      throw Exception('SSE chat request failed with status 503: unavailable');
    }
    yield SseChatEvent.fromWire('token', {'text': 'A blue square.'});
    yield SseChatEvent.fromWire('done', {});
  }
}

Message _userMessage(String content, {String id = 'u1'}) => Message(
      id: id,
      conversationId: 'conv-1',
      role: 'user',
      content: content,
      createdAt: DateTime.utc(2026, 10, 30, 12),
    );

Widget _bubble(Message message, {Map<String, List<ChatImageAttachment>> sent = const {}}) {
  return ProviderScope(
    overrides: [
      sentChatImagesProvider.overrideWith(() => _PresetSentImages(sent)),
    ],
    child: MaterialApp(
      theme: AuraThemeV2.light,
      home: Scaffold(body: MessageBubble(message: message, isUser: true)),
    ),
  );
}

class _PresetSentImages extends SentChatImages {
  final Map<String, List<ChatImageAttachment>> preset;
  _PresetSentImages(this.preset);

  @override
  Map<String, List<ChatImageAttachment>> build() => preset;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('image marker', () {
    test('splits the text from the count', () {
      expect(splitImagesNote('What is this?\n[1 image attached]'),
          (text: 'What is this?', imageCount: 1));
      expect(splitImagesNote('Compare\n[3 images attached]'), (text: 'Compare', imageCount: 3));
      expect(splitImagesNote('No images here'), (text: 'No images here', imageCount: 0));
      // Only a trailing marker counts, not one quoted mid-message.
      expect(splitImagesNote('I typed [2 images attached] myself, then more').imageCount, 0);
    });
  });

  group('sent images', () {
    test('keep only the most recent messages', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final sent = container.read(sentChatImagesProvider.notifier);
      for (var i = 0; i < kSentImageMessages + 5; i++) {
        sent.remember('m$i', [_image('$i.png')]);
      }
      final kept = container.read(sentChatImagesProvider);
      expect(kept.length, kSentImageMessages);
      expect(kept.containsKey('m0'), isFalse);
      expect(kept.containsKey('m${kSentImageMessages + 4}'), isTrue);
    });

    test('ignore a message with no images', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      container.read(sentChatImagesProvider.notifier).remember('m1', const []);
      expect(container.read(sentChatImagesProvider), isEmpty);
    });
  });

  group('user bubble', () {
    testWidgets('shows thumbnails, not the marker, and opens a preview', (tester) async {
      // A tap that misses its target (e.g. a clipped close button) must fail, not hit the barrier.
      WidgetController.hitTestWarningShouldBeFatal = true;
      addTearDown(() => WidgetController.hitTestWarningShouldBeFatal = false);
      await tester.pumpWidget(_bubble(
        _userMessage('What is this?\n[2 images attached]'),
        sent: {'u1': [_image('a.png'), _image('b.png')]},
      ));
      await tester.pumpAndSettle();

      expect(find.text('What is this?'), findsOneWidget);
      expect(find.textContaining('images attached]'), findsNothing);
      expect(find.bySemanticsLabel('Sent image 1 of 2: a.png. Open preview'), findsOneWidget);
      expect(find.bySemanticsLabel('Sent image 2 of 2: b.png. Open preview'), findsOneWidget);

      await tester.tap(find.bySemanticsLabel('Sent image 2 of 2: b.png. Open preview'));
      await tester.pumpAndSettle();
      expect(find.byType(InteractiveViewer), findsOneWidget);
      await tester.tap(find.byTooltip('Close preview'));
      await tester.pumpAndSettle();
      expect(find.byType(InteractiveViewer), findsNothing);
    });

    testWidgets('says how many were attached once the images are gone', (tester) async {
      await tester.pumpWidget(_bubble(_userMessage('What is this?\n[1 image attached]')));
      await tester.pumpAndSettle();

      expect(find.text('1 image attached'), findsOneWidget);
      expect(find.text('What is this?'), findsOneWidget);
      expect(find.byType(Image), findsNothing);
    });

    testWidgets('a text message is unchanged', (tester) async {
      await tester.pumpWidget(_bubble(_userMessage('hello')));
      await tester.pumpAndSettle();
      expect(find.text('hello'), findsOneWidget);
      expect(find.byIcon(Icons.image_outlined), findsNothing);
    });
  });

  group('repository', () {
    late AppDatabase db;

    setUp(() async {
      await EncryptionService.init();
      db = AppDatabase.forTesting(NativeDatabase.memory());
    });

    ProviderContainer containerWith(_RecordingSseClient Function(Ref) client) {
      final container = ProviderContainer(overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
        sseAgentClientProvider.overrideWith(client),
      ]);
      addTearDown(container.dispose);
      return container;
    }

    test('a sent message keeps its images in memory under its own id', () async {
      late _RecordingSseClient sse;
      final container = containerWith((ref) => sse = _RecordingSseClient(ref));

      await container.read(chatRepositoryProvider).sendChatMessage(
            conversationId: 'conv-1', userContent: 'What is this?', images: [_image('a.png')]);

      final user = (await db.select(db.messages).get()).singleWhere((m) => m.role == 'user');
      expect(user.content, 'What is this?\n[1 image attached]');
      expect(container.read(sentChatImagesProvider)[user.id]!.single.name, 'a.png');
      expect(sse.calls.single.images.single.bytes, Uint8List.fromList(_png));
    });

    test('a retry resends the text without the marker, with the images', () async {
      late _RecordingSseClient sse;
      final container = containerWith((ref) => sse = _RecordingSseClient(ref, failFirst: true));
      final repo = container.read(chatRepositoryProvider);

      await repo.sendChatMessage(
          conversationId: 'conv-1', userContent: 'What is this?', images: [_image('a.png')]);
      final error = (await db.select(db.messages).get()).singleWhere((m) => m.role == 'assistant');
      await repo.retryLastFailedTurn(conversationId: 'conv-1', errorMessageId: error.id);

      expect(sse.calls, hasLength(2));
      expect(sse.calls.last.message, 'What is this?');
      expect(sse.calls.last.images.single.name, 'a.png');
      final users = (await db.select(db.messages).get()).where((m) => m.role == 'user').toList();
      expect(users.single.content, 'What is this?\n[1 image attached]');
      expect(container.read(sentChatImagesProvider)[users.single.id], isNotNull);
    });
  });
}
