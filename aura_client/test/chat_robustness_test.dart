// WP9 (G-14): confirmation and step rendering, through the real chat engine (ChatRepository,
// Drift, the confirmation card and the message bubble) with only the chat stream scripted.
//
// a. A held Engineer turn's confirmation survives an app restart (until the server's 1 h TTL),
//    and approving it then still works.
// b. An approval the server no longer accepts (`confirmation_invalid`) says so, with no Retry
//    card (Retry would resend the original message as a brand-new turn).
// c. The approval resend keeps the held turn's persona, not whichever persona is active now.
// d. Step rows are one per `step_id`, and none is left spinning once the turn is over.
import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/agent_config_repository.dart';
import 'package:aura_client/features/chat/data/chat_connection_error.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/widgets/empty_answer_retry_card.dart';
import 'package:aura_client/features/chat/presentation/widgets/engineer_confirmation_card.dart';
import 'package:aura_client/features/chat/presentation/widgets/message_bubble.dart';
import 'package:aura_client/features/chat/presentation/widgets/step_log_panel.dart';
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

/// The chat stream: records each request and plays the next script (default: a short answer).
class _ScriptedSseClient extends SseAgentClient {
  _ScriptedSseClient(super.ref);

  final requests = <_Request>[];
  final scripts = <List<SseChatEvent>>[];
  Object? throwNext;

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
    final script = scripts.isEmpty
        ? [
            SseChatEvent.fromWire('token', {'text': 'Done.'}),
            SseChatEvent.fromWire('done', {'turn_id': 'turn-next', 'pending_tool_calls': []}),
          ]
        : scripts.removeAt(0);
    for (final event in script) {
      yield event;
    }
  }
}

const _heldCall = PendingToolCall(
  callId: 'step_s1',
  tool: 'write_file',
  arguments: {'path': 'main.py', 'content': 'print(1)'},
  approvalId: 'apr.tok1.n1',
);

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

  /// An Engineer turn, held for approval of [_heldCall], started [age] ago.
  Future<void> seedHeldTurn({Duration age = const Duration(minutes: 10)}) async {
    final started = DateTime.now().subtract(age);
    await db.into(db.messages).insert(MessagesCompanion.insert(
          id: 'u1',
          conversationId: 'conv-1',
          role: 'user',
          content: 'write main.py',
          createdAt: started.subtract(const Duration(seconds: 1)),
        ));
    await db.into(db.messages).insert(MessagesCompanion.insert(
          id: 'a1',
          conversationId: 'conv-1',
          role: 'assistant',
          content: '',
          agentId: const Value('engineer'),
          createdAt: started,
          turnMetaJson: Value(const TurnMeta(
            turnId: 'turn-1',
            needsConfirmation: true,
            pendingToolCalls: [_heldCall],
            workspaceRoot: '/home/me/api',
            desktopTaskIsolation: true,
          ).encode()),
        ));
  }

  Future<Message> row(String id) => (db.select(db.messages)..where((m) => m.id.equals(id))).getSingle();

  group('G-14a: pending confirmations survive a restart', () {
    test('the held call is offered again, scoped to its turn', () async {
      await seedHeldTurn();
      final c = session();

      await c.read(chatRepositoryProvider).rehydratePendingConfirmations('conv-1');

      final pending = c.read(pendingConfirmationProvider);
      expect(pending.map((p) => p.approvalId), ['apr.tok1.n1']);
      expect(pending.single.turnId, 'turn-1');
    });

    test('loading the conversation twice does not offer it twice', () async {
      await seedHeldTurn();
      final c = session();
      final repo = c.read(chatRepositoryProvider);

      await repo.rehydratePendingConfirmations('conv-1');
      await repo.rehydratePendingConfirmations('conv-1');

      expect(c.read(pendingConfirmationProvider), hasLength(1));
    });

    test('a turn older than the server TTL (1 h) is not offered', () async {
      await seedHeldTurn(age: const Duration(minutes: 61));
      final c = session();

      await c.read(chatRepositoryProvider).rehydratePendingConfirmations('conv-1');

      expect(c.read(pendingConfirmationProvider), isEmpty);
    });

    test('approving after a restart resumes the held turn where it ran, as its persona', () async {
      await seedHeldTurn();
      final c = session();
      final repo = c.read(chatRepositoryProvider);
      await repo.rehydratePendingConfirmations('conv-1');
      expect(c.read(activeAgentIdProvider), 'orchestrator', reason: 'the app restarted on the default');

      final ok = await repo.confirmPendingCalls(
          conversationId: 'conv-1', calls: c.read(pendingConfirmationProvider));

      expect(ok, isTrue);
      final request = sseOf(c).requests.single;
      expect(request.approvalIds, ['apr.tok1.n1']);
      expect(request.message, 'write main.py');
      // G-14c: the held turn's persona, not the active one.
      expect(request.agentId, 'engineer');
      // WP4: the same folder and the same isolated worktree as the held turn.
      expect(request.workspaceRoot, '/home/me/api');
      expect(request.isolation, isTrue);
    });

    test('an answered turn is not offered again after the next restart', () async {
      await seedHeldTurn();
      final first = session();
      await first.read(chatRepositoryProvider).rehydratePendingConfirmations('conv-1');
      await first
          .read(chatRepositoryProvider)
          .confirmPendingCalls(conversationId: 'conv-1', calls: first.read(pendingConfirmationProvider));

      final second = session();
      await second.read(chatRepositoryProvider).rehydratePendingConfirmations('conv-1');

      expect(second.read(pendingConfirmationProvider), isEmpty);
      expect(TurnMeta.decode((await row('a1')).turnMetaJson).confirmationResolved, isTrue);
    });

    test('a declined call is not offered again either', () async {
      await seedHeldTurn();
      final first = session();
      await first.read(chatRepositoryProvider).rehydratePendingConfirmations('conv-1');
      await first.read(chatRepositoryProvider).rejectPendingCalls(first.read(pendingConfirmationProvider));

      final second = session();
      await second.read(chatRepositoryProvider).rehydratePendingConfirmations('conv-1');

      expect(second.read(pendingConfirmationProvider), isEmpty);
      expect(sseOf(first).requests, isEmpty, reason: 'declining sends nothing');
    });
  });

  group('G-14c: the resend keeps the held turn persona', () {
    test('its row is labelled Engineer, so the conversation stays an Engineer task', () async {
      await seedHeldTurn();
      final c = session();
      final repo = c.read(chatRepositoryProvider);
      await repo.rehydratePendingConfirmations('conv-1');

      await repo.confirmPendingCalls(conversationId: 'conv-1', calls: c.read(pendingConfirmationProvider));

      final assistants = (await db.select(db.messages).get()).where((m) => m.role == 'assistant');
      expect(assistants.map((m) => m.agentId), everyElement('engineer'));
      expect(await repo.watchConversationsForAgent('engineer').first, contains('conv-1'));
    });
  });

  group('G-14b: an expired approval', () {
    Future<Message> approveExpired(Object? failure, List<SseChatEvent>? frames) async {
      await seedHeldTurn();
      final c = session();
      final repo = c.read(chatRepositoryProvider);
      await repo.rehydratePendingConfirmations('conv-1');
      final sse = sseOf(c);
      if (failure != null) sse.throwNext = failure;
      if (frames != null) sse.scripts.add(frames);

      await repo.confirmPendingCalls(conversationId: 'conv-1', calls: c.read(pendingConfirmationProvider));

      return (await db.select(db.messages).get()).lastWhere((m) => m.role == 'assistant' && m.id != 'a1');
    }

    test('as an error frame: an "expired" row, not a connection error', () async {
      final reply = await approveExpired(null, [
        SseChatEvent.fromWire('error', {
          'error': 'confirmation_invalid',
          'message': 'approval expired or already used',
          'retryable': false,
        }),
      ]);

      expect(reply.content, kChatApprovalExpiredText);
      expect(isChatConnectionErrorText(reply.content), isFalse, reason: 'no Retry card');
    });

    test('as the 409 before the stream opens: the same', () async {
      final reply = await approveExpired(
        const AuraApiError(
          error: 'confirmation_invalid',
          message: 'approval expired or already used',
          retryable: false,
          statusCode: 409,
        ),
        null,
      );

      expect(reply.content, kChatApprovalExpiredText);
    });
  });

  group('G-14d: steps', () {
    test('one row per step_id, the latest status wins, and none is left running after done', () async {
      final c = session();
      sseOf(c).scripts.add([
        SseChatEvent.fromWire('step', {'step_id': 's1', 'status': 'running', 'description': 'Read main.py'}),
        SseChatEvent.fromWire('step', {'step_id': 's2', 'status': 'running', 'description': 'Run tests'}),
        SseChatEvent.fromWire('step', {'step_id': 's1', 'status': 'succeeded', 'description': 'Read main.py'}),
        SseChatEvent.fromWire('token', {'text': 'Done.'}),
        SseChatEvent.fromWire('done', {'turn_id': 't-steps'}),
      ]);

      await c.read(chatRepositoryProvider).sendChatMessage(conversationId: 'conv-2', userContent: 'go');

      final reply = (await db.select(db.messages).get()).singleWhere((m) => m.role == 'assistant');
      final steps = TurnMeta.decode(reply.turnMetaJson).steps;
      expect(steps.map((s) => s.stepId), ['s1', 's2']);
      expect(steps.first.status, 'succeeded');
      expect(steps.map((s) => s.status), isNot(contains('running')));
    });

    testWidgets('a stored turn with duplicate or unfinished rows renders one row each, no spinner',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: AuraThemeV2.dark,
        home: const Scaffold(
          body: StepLogPanel(steps: [
            TurnStep(stepId: 's1', status: 'running', label: 'Read main.py'),
            TurnStep(stepId: 's1', status: 'succeeded', label: 'Read main.py'),
            TurnStep(stepId: 's2', status: 'running', label: 'Run tests'),
          ]),
        ),
      ));

      expect(find.text('Read main.py'), findsOneWidget);
      expect(find.text('Run tests'), findsOneWidget);
      expect(find.byType(CircularProgressIndicator), findsNothing);
    });

    testWidgets('while the turn is live, a running step still spins', (tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: AuraThemeV2.dark,
        home: const Scaffold(
          body: StepLogPanel(live: true, steps: [TurnStep(stepId: 's2', status: 'running', label: 'Run tests')]),
        ),
      ));

      expect(find.byType(CircularProgressIndicator), findsOneWidget);
    });
  });

  group('the bubble', () {
    Future<void> pumpBubble(WidgetTester tester, ProviderContainer c, String id) async {
      final message = await tester.runAsync(() => row(id));
      await tester.pumpWidget(UncontrolledProviderScope(
        container: c,
        child: MaterialApp(
          theme: AuraThemeV2.dark,
          localizationsDelegates: AppLocalizations.localizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: Scaffold(body: SingleChildScrollView(child: MessageBubble(message: message!, isUser: false))),
        ),
      ));
      await tester.pump();
    }

    testWidgets('after a restart: the card is back and the hint points down at it', (tester) async {
      await tester.runAsync(() => seedHeldTurn());
      final c = session();
      await tester.runAsync(() => c.read(chatRepositoryProvider).rehydratePendingConfirmations('conv-1'));

      await pumpBubble(tester, c, 'a1');

      expect(find.text(_l10n.chatAwaitingConfirmation), findsOneWidget);
      expect(_l10n.chatAwaitingConfirmation, contains('below'));
      expect(find.text(_l10n.chatConfirmAction), findsOneWidget);
      final hint = tester.getRect(find.text(_l10n.chatAwaitingConfirmation));
      final card = tester.getRect(find.byType(EngineerConfirmationCard));
      expect(card.top, greaterThan(hint.top), reason: 'the card is below the hint');
    });

    testWidgets('past the TTL: "expired, ask again", no card and no Retry', (tester) async {
      await tester.runAsync(() => seedHeldTurn(age: const Duration(hours: 2)));
      final c = session();
      await tester.runAsync(() => c.read(chatRepositoryProvider).rehydratePendingConfirmations('conv-1'));

      await pumpBubble(tester, c, 'a1');

      expect(find.text(_l10n.chatApprovalExpiredNotice), findsOneWidget);
      expect(find.text(_l10n.chatConfirmAction), findsNothing);
      expect(find.byType(EmptyAnswerRetryCard), findsNothing);
    });

    testWidgets('an expired-approval reply reads "expired, ask again" with no Retry', (tester) async {
      await tester.runAsync(() => db.into(db.messages).insert(MessagesCompanion.insert(
            id: 'a9',
            conversationId: 'conv-1',
            role: 'assistant',
            content: kChatApprovalExpiredText,
            createdAt: DateTime.now(),
          )));
      final c = session();

      await pumpBubble(tester, c, 'a9');

      expect(find.text(_l10n.chatApprovalExpiredNotice), findsOneWidget);
      expect(find.text(_l10n.retry), findsNothing);
    });
  });
}
