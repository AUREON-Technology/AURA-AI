import 'dart:async';
import 'dart:convert';

import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/chat_connection_error.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/widgets/chat_composer.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

// F-31 / R4.1: a stalled chat stream must not lock the chat, and Stop must unlock it at once,
// keeping the partial answer in both cases.

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

List<int> _frame(String event, Map<String, dynamic> data) =>
    utf8.encode('event: $event\ndata: ${jsonEncode(data)}\n\n');

/// Answers 200 with headers, then whatever [body] emits -- which, in these tests, is one token
/// and then silence: the body stream is never closed.
MockClient _streamingClient(StreamController<List<int>> body) =>
    MockClient.streaming((request, _) async => http.StreamedResponse(body.stream, 200));

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late StreamController<List<int>> body;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    body = StreamController<List<int>>();
  });

  // Not awaited: `close()` on a stream nobody listened to never completes.
  tearDown(() => unawaited(body.close()));

  ProviderContainer containerWith({required Duration idleTimeout}) {
    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        sseAgentClientProvider.overrideWith(
          (ref) => SseAgentClient(
            ref,
            httpClient: _streamingClient(body),
            idleTimeout: idleTimeout,
          ),
        ),
      ],
    );
    addTearDown(container.dispose);
    return container;
  }

  Future<Message> assistantRow() async =>
      (await db.select(db.messages).get()).singleWhere((m) => m.role == 'assistant');

  test('headers, one token, then silence: the lock clears within the idle timeout', () async {
    const idle = Duration(milliseconds: 100);
    final container = containerWith(idleTimeout: idle);
    body.add(_frame('token', {'text': 'Partial answer'}));

    final stopwatch = Stopwatch()..start();
    final send = container
        .read(chatRepositoryProvider)
        .sendChatMessage(conversationId: 'conv-1', userContent: 'hello');
    await pumpEventQueue();
    expect(container.read(streamingMessageIdProvider), isNotNull);

    // Without the idle timeout this future never completes.
    await send.timeout(const Duration(seconds: 5));
    stopwatch.stop();

    expect(container.read(streamingMessageIdProvider), isNull);
    expect(stopwatch.elapsed, lessThan(const Duration(seconds: 3)));
    final assistant = await assistantRow();
    expect(assistant.content, 'Partial answer');
    expect(isChatConnectionErrorText(assistant.content), isFalse);
    expect(TurnMeta.decode(assistant.turnMetaJson).interrupted, isTrue);
  });

  test('keepalive comments hold an otherwise idle stream open', () async {
    const idle = Duration(milliseconds: 150);
    final container = containerWith(idleTimeout: idle);
    final send = container
        .read(chatRepositoryProvider)
        .sendChatMessage(conversationId: 'conv-1', userContent: 'hello');

    // Four keepalives 60ms apart: 240ms in all, longer than the idle timeout, but no single gap
    // reaches it.
    for (var i = 0; i < 4; i++) {
      body.add(utf8.encode(': keepalive\n\n'));
      await Future<void>.delayed(const Duration(milliseconds: 60));
    }
    body
      ..add(_frame('token', {'text': 'Done.'}))
      ..add(_frame('done', {}));
    unawaited(body.close());
    await send.timeout(const Duration(seconds: 5));

    final assistant = await assistantRow();
    expect(assistant.content, 'Done.');
    expect(TurnMeta.decode(assistant.turnMetaJson).interrupted, isFalse);
  });

  test('Stop unlocks immediately and keeps the partial text', () async {
    // The default 45s idle timeout: only Stop can end this turn inside the test's time.
    final container = containerWith(idleTimeout: const Duration(seconds: 45));
    body.add(_frame('token', {'text': 'Half an ans'}));

    final repo = container.read(chatRepositoryProvider);
    final send = repo.sendChatMessage(conversationId: 'conv-1', userContent: 'hello');
    await pumpEventQueue();
    expect(container.read(streamingMessageIdProvider), isNotNull);

    repo.stopStreaming();
    // Synchronously: the chat is unlocked before the turn has even finished saving.
    expect(container.read(streamingMessageIdProvider), isNull);

    await send.timeout(const Duration(seconds: 2));
    final assistant = await assistantRow();
    expect(assistant.content, 'Half an ans');
    expect(TurnMeta.decode(assistant.turnMetaJson).interrupted, isTrue);
  });

  testWidgets('the composer shows Stop while streaming, and pressing it unlocks the chat',
      (tester) async {
    final semantics = tester.ensureSemantics();
    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
      ],
    );
    addTearDown(container.dispose);
    container.read(streamingMessageIdProvider.notifier).set('assistant-1');

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          theme: AuraThemeV2.light,
          home: Scaffold(
            body: ChatComposer(
              controller: TextEditingController(),
              isMobile: true,
              onSend: () {},
            ),
          ),
        ),
      ),
    );

    final stop = find.byTooltip(kStopGeneratingLabel);
    expect(stop, findsOneWidget);
    // What a screen reader announces for the button.
    final node = tester.getSemantics(stop).getSemanticsData();
    expect(node.tooltip, kStopGeneratingLabel);
    expect(node.flagsCollection.isButton, isTrue);

    await tester.tap(stop);
    await tester.pump();

    expect(container.read(streamingMessageIdProvider), isNull);
    expect(find.byTooltip(kStopGeneratingLabel), findsNothing);
    expect(find.byIcon(Icons.send), findsOneWidget);
    semantics.dispose();
  });
}
