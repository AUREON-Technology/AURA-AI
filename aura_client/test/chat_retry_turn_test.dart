// C1: retry, approval resend and stream-end robustness of the chat engine.
//
// F2 / INT-108: Retry resends the turn whose bubble was tapped -- its own user message, persona
//   and workspace -- never "the last user message"; it refuses while a reply is streaming and
//   removes the failed turn only once the resend has been accepted. Only the latest failed turn
//   offers Retry at all.
// F-04: an approval resend that never reached the server keeps the confirmation card.
// F21: a stream that ends without a `done` frame is an interrupted turn, not a finished one.

import 'dart:async';

import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/chat_connection_error.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/widgets/message_bubble.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:aura_client/l10n/generated/app_localizations_en.dart';
import 'package:drift/drift.dart' hide isNotNull, isNull;
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

final _l10n = AppLocalizationsEn();

typedef _Request = ({
  String message,
  String agentId,
  List<String> approvalIds,
  String? workspaceRoot,
  bool isolation,
});

/// Records each request and plays the next script (default: a short, finished answer).
class _ScriptedSseClient extends SseAgentClient {
  _ScriptedSseClient(super.ref);

  final requests = <_Request>[];
  final scripts = <List<SseChatEvent>>[];
  Object? throwNext;

  /// When set, the stream waits on it after recording the request (a reply still in flight).
  Completer<void>? hold;

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
    requests.add((
      message: message,
      agentId: agentId,
      approvalIds: List.of(approvalIds),
      workspaceRoot: workspaceRoot,
      isolation: desktopTaskIsolation,
    ));
    final error = throwNext;
    throwNext = null;
    if (error != null) throw error;
    final wait = hold;
    if (wait != null) await wait.future;
    final script = scripts.isEmpty
        ? [
            SseChatEvent.fromWire('token', {'text': 'Done.'}),
            SseChatEvent.fromWire('done', {'turn_id': 'turn-${requests.length}'}),
          ]
        : scripts.removeAt(0);
    for (final event in script) {
      yield event;
    }
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
  });

  tearDown(() async => db.close());

  /// A fresh app session over the same database: what a restart looks like to the chat engine.
  ProviderContainer session() {
    final container = ProviderContainer(overrides: [
      databaseProvider.overrideWith((ref) => db),
      sseAgentClientProvider.overrideWith((ref) => _ScriptedSseClient(ref)),
    ]);
    addTearDown(container.dispose);
    return container;
  }

  _ScriptedSseClient sseOf(ProviderContainer c) => c.read(sseAgentClientProvider) as _ScriptedSseClient;

  Future<List<Message>> rows() => (db.select(db.messages)
        ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.asc)]))
      .get();

  /// A user message and the failed reply to it, [minutesAgo] minutes back.
  Future<String> seedFailedTurn(
    String userId,
    String text, {
    required int minutesAgo,
    String agentId = 'orchestrator',
    TurnMeta? meta,
  }) async {
    final at = DateTime.now().subtract(Duration(minutes: minutesAgo));
    await db.into(db.messages).insert(MessagesCompanion.insert(
          id: userId,
          conversationId: 'conv-1',
          role: 'user',
          content: text,
          createdAt: at,
        ));
    final errorId = 'err-$userId';
    await db.into(db.messages).insert(MessagesCompanion.insert(
          id: errorId,
          conversationId: 'conv-1',
          role: 'assistant',
          content: kChatConnectionErrorMessage,
          agentId: Value(agentId),
          turnMetaJson: Value(meta?.encode()),
          createdAt: at.add(const Duration(seconds: 1)),
        ));
    return errorId;
  }

  group('Retry (F2 / INT-108)', () {
    test('retrying the older of two failed turns resends that turn\'s own message', () async {
      final older = await seedFailedTurn('u1', 'first question', minutesAgo: 5);
      final newer = await seedFailedTurn('u2', 'second question', minutesAgo: 2);
      final c = session();

      final accepted = await c.read(chatRepositoryProvider)
          .retryLastFailedTurn(conversationId: 'conv-1', errorMessageId: older);

      expect(accepted, isTrue);
      expect(sseOf(c).requests.map((r) => r.message), ['first question']);
      final ids = (await rows()).map((m) => m.id).toList();
      expect(ids, isNot(contains('u1')), reason: 'the retried turn is replaced by its resend');
      expect(ids, isNot(contains(older)));
      expect(ids, containsAll(['u2', newer]), reason: 'the other failed turn is left alone');
    });

    test('carries the failed turn\'s persona, workspace root and isolation, even after a restart',
        () async {
      final error = await seedFailedTurn('u1', 'run the tests',
          minutesAgo: 1,
          agentId: 'engineer',
          meta: const TurnMeta(workspaceRoot: '/home/me/aura', desktopTaskIsolation: true));
      final c = session();

      await c.read(chatRepositoryProvider)
          .retryLastFailedTurn(conversationId: 'conv-1', errorMessageId: error);

      final sent = sseOf(c).requests.single;
      expect(sent.agentId, 'engineer');
      expect(sent.workspaceRoot, '/home/me/aura');
      expect(sent.isolation, isTrue);
    });

    test('a failed turn remembers where it ran, so a retry after a restart still knows', () async {
      final first = session();
      sseOf(first).throwNext = Exception('SSE chat request failed with status 503');
      await first.read(chatRepositoryProvider).sendChatMessage(
          conversationId: 'conv-1',
          userContent: 'run the tests',
          workspaceRoot: '/home/me/aura',
          desktopTaskIsolation: true);
      final error = (await rows()).singleWhere((m) => m.role == 'assistant');
      expect(isChatConnectionErrorText(error.content), isTrue);

      final restarted = session();
      await restarted.read(chatRepositoryProvider)
          .retryLastFailedTurn(conversationId: 'conv-1', errorMessageId: error.id);

      final sent = sseOf(restarted).requests.single;
      expect(sent.workspaceRoot, '/home/me/aura');
      expect(sent.isolation, isTrue);
    });

    test('while a reply is streaming, Retry does nothing destructive', () async {
      final error = await seedFailedTurn('u1', 'first question', minutesAgo: 5);
      final c = session();
      c.read(streamingMessageIdProvider.notifier).set('some-other-reply');

      final accepted = await c.read(chatRepositoryProvider)
          .retryLastFailedTurn(conversationId: 'conv-1', errorMessageId: error);

      expect(accepted, isFalse);
      expect(sseOf(c).requests, isEmpty);
      expect((await rows()).map((m) => m.id), ['u1', error]);
    });

    test('a double tap sends the turn once', () async {
      final error = await seedFailedTurn('u1', 'first question', minutesAgo: 5);
      final c = session();
      final repo = c.read(chatRepositoryProvider);

      final results = await Future.wait([
        repo.retryLastFailedTurn(conversationId: 'conv-1', errorMessageId: error),
        repo.retryLastFailedTurn(conversationId: 'conv-1', errorMessageId: error),
      ]);

      expect(results.where((r) => r), hasLength(1));
      expect(sseOf(c).requests, hasLength(1));
      expect((await rows()).where((m) => m.role == 'user'), hasLength(1));
    });

    test('the failed turn stays until the resend is accepted, then is replaced at once', () async {
      final error = await seedFailedTurn('u1', 'first question', minutesAgo: 5);
      final c = session();
      final sse = sseOf(c)..hold = Completer<void>();

      final retry = c.read(chatRepositoryProvider)
          .retryLastFailedTurn(conversationId: 'conv-1', errorMessageId: error);
      // The resend is in flight: the old rows are already gone, the new ones are there.
      while (sse.requests.isEmpty) {
        await Future<void>.delayed(Duration.zero);
      }
      final during = await rows();
      expect(during.map((m) => m.id), isNot(contains(error)));
      expect(during.where((m) => m.role == 'user').single.content, 'first question');
      sse.hold!.complete();
      expect(await retry, isTrue);
    });

    test('a failed approval resend has no user row of its own: Retry keeps the held turn\'s', () async {
      final at = DateTime.now().subtract(const Duration(minutes: 3));
      await db.into(db.messages).insert(MessagesCompanion.insert(
            id: 'u1', conversationId: 'conv-1', role: 'user', content: 'write main.py', createdAt: at));
      await db.into(db.messages).insert(MessagesCompanion.insert(
            id: 'held',
            conversationId: 'conv-1',
            role: 'assistant',
            content: '',
            createdAt: at.add(const Duration(seconds: 1))));
      await db.into(db.messages).insert(MessagesCompanion.insert(
            id: 'resend-failed',
            conversationId: 'conv-1',
            role: 'assistant',
            content: kChatConnectionErrorMessage,
            createdAt: at.add(const Duration(seconds: 2))));
      final c = session();

      await c.read(chatRepositoryProvider)
          .retryLastFailedTurn(conversationId: 'conv-1', errorMessageId: 'resend-failed');

      expect(sseOf(c).requests.single.message, 'write main.py');
      final after = await rows();
      expect(after.map((m) => m.id), containsAll(['u1', 'held']));
      expect(after.map((m) => m.id), isNot(contains('resend-failed')));
      expect(after.where((m) => m.role == 'user'), hasLength(1), reason: 'no duplicate user row');
    });

    testWidgets('only the latest failed turn offers Retry', (tester) async {
      late Message older;
      late Message newer;
      final c = session();
      await tester.runAsync(() async {
        final olderId = await seedFailedTurn('u1', 'first question', minutesAgo: 5);
        final newerId = await seedFailedTurn('u2', 'second question', minutesAgo: 2);
        final all = await rows();
        older = all.singleWhere((m) => m.id == olderId);
        newer = all.singleWhere((m) => m.id == newerId);
        // Kept alive by a listener, as the chat screen's bubbles keep it.
        c.listen(latestAssistantMessageIdProvider('conv-1'), (_, _) {});
        await c.read(latestAssistantMessageIdProvider('conv-1').future);
      });

      Future<void> show(Message m) async {
        await tester.pumpWidget(UncontrolledProviderScope(
          container: c,
          child: MaterialApp(
            theme: AuraThemeV2.dark,
            localizationsDelegates: AppLocalizations.localizationsDelegates,
            supportedLocales: AppLocalizations.supportedLocales,
            home: Scaffold(body: SingleChildScrollView(child: MessageBubble(message: m, isUser: false))),
          ),
        ));
        await tester.pump();
      }

      await show(older);
      expect(find.text(_l10n.chatCloudUnavailableTitle), findsOneWidget);
      expect(find.text(_l10n.retry), findsNothing);

      await show(newer);
      expect(find.text(_l10n.retry), findsOneWidget);
    });
  });

  group('approval resend (F-04)', () {
    const call = PendingToolCall(
      callId: 'step_s1',
      tool: 'write_file',
      arguments: {'path': 'main.py'},
      approvalId: 'apr.tok1.n1',
    );

    Future<void> seedHeld() async {
      final at = DateTime.now().subtract(const Duration(minutes: 2));
      await db.into(db.messages).insert(MessagesCompanion.insert(
            id: 'u1', conversationId: 'conv-1', role: 'user', content: 'write main.py', createdAt: at));
      await db.into(db.messages).insert(MessagesCompanion.insert(
            id: 'a1',
            conversationId: 'conv-1',
            role: 'assistant',
            content: '',
            turnId: const Value('turn-1'),
            turnMetaJson: Value(const TurnMeta(
              needsConfirmation: true,
              pendingToolCalls: [call],
              turnId: 'turn-1',
            ).encode()),
            createdAt: at.add(const Duration(seconds: 1))));
    }

    test('a resend that never reached the server keeps the card', () async {
      await seedHeld();
      final c = session();
      final held = call.copyWith(turnId: 'turn-1');
      c.read(pendingConfirmationProvider.notifier).addPending([held]);
      sseOf(c).throwNext = Exception('SSE chat request failed with status 503');

      await c.read(chatRepositoryProvider).confirmPendingCalls(conversationId: 'conv-1', calls: [held]);

      expect(c.read(pendingConfirmationProvider).map((p) => p.approvalId), ['apr.tok1.n1'],
          reason: 'the user can approve again');
      final meta = TurnMeta.decode((await rows()).singleWhere((m) => m.id == 'a1').turnMetaJson);
      expect(meta.confirmationResolved, isFalse);
    });

    test('a resend the server answered clears the card', () async {
      await seedHeld();
      final c = session();
      final held = call.copyWith(turnId: 'turn-1');
      c.read(pendingConfirmationProvider.notifier).addPending([held]);

      expect(
        await c.read(chatRepositoryProvider).confirmPendingCalls(conversationId: 'conv-1', calls: [held]),
        isTrue,
      );

      expect(c.read(pendingConfirmationProvider), isEmpty);
      expect(sseOf(c).requests.single.approvalIds, ['apr.tok1.n1']);
    });
  });

  group('stream end without done (F21)', () {
    test('text then a clean end: kept, but marked interrupted', () async {
      final c = session();
      sseOf(c).scripts.add([SseChatEvent.fromWire('token', {'text': 'Half an ans'})]);

      await c.read(chatRepositoryProvider).sendChatMessage(conversationId: 'conv-1', userContent: 'hi');

      final reply = (await rows()).singleWhere((m) => m.role == 'assistant');
      expect(reply.content, 'Half an ans');
      expect(TurnMeta.decode(reply.turnMetaJson).interrupted, isTrue);
      expect(c.read(streamingMessageIdProvider), isNull);
    });

    test('nothing then a clean end: a connection error with Retry, not an empty "success"', () async {
      final c = session();
      sseOf(c).scripts.add(const []);

      await c.read(chatRepositoryProvider).sendChatMessage(conversationId: 'conv-1', userContent: 'hi');

      final reply = (await rows()).singleWhere((m) => m.role == 'assistant');
      expect(isChatConnectionErrorText(reply.content), isTrue);
    });

    test('a finished turn is not marked interrupted', () async {
      final c = session();

      await c.read(chatRepositoryProvider).sendChatMessage(conversationId: 'conv-1', userContent: 'hi');

      final reply = (await rows()).singleWhere((m) => m.role == 'assistant');
      expect(reply.content, 'Done.');
      expect(TurnMeta.decode(reply.turnMetaJson).interrupted, isFalse);
    });
  });
}
