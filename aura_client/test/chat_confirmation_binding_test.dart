// F-04: approving a held tool call references that pending call -- its server-issued approval id
// and its own turn -- never "the last user message" or a tool name.
import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/widgets/engineer_confirmation_card.dart';
import 'package:drift/drift.dart' hide isNotNull, isNull;
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Records what each chat request asked for, and answers with an empty `done` frame.
class _RecordingSseClient extends SseAgentClient {
  _RecordingSseClient(super.ref);

  final List<({String message, List<String> approvalIds})> requests = [];

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
    requests.add((message: message, approvalIds: List.of(approvalIds)));
    yield SseChatEvent.fromWire('token', {'text': 'Done.'});
    yield SseChatEvent.fromWire('done', {'turn_id': 'turn-resumed', 'pending_tool_calls': []});
  }
}

const _turnOneCall = PendingToolCall(
  callId: 'step_s1',
  tool: 'delete_record',
  arguments: {'target': 'A'},
  turnId: 'turn-1',
  approvalId: 'apr.tok1.n1',
);

// Same positional call id as turn 1's -- only the approval id tells them apart.
const _turnTwoCall = PendingToolCall(
  callId: 'step_s1',
  tool: 'delete_record',
  arguments: {'target': 'B'},
  turnId: 'turn-2',
  approvalId: 'apr.tok2.n1',
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late ProviderContainer container;
  late _RecordingSseClient sse;

  Future<void> insert(String id, String role, String content, int second, {String? turnId}) {
    return db.into(db.messages).insert(
          MessagesCompanion.insert(
            id: id,
            conversationId: 'conv-1',
            role: role,
            content: content,
            createdAt: DateTime(2026, 10, 28, 12, 0, second),
            turnMetaJson: Value(turnId == null ? null : TurnMeta(turnId: turnId).encode()),
          ),
        );
  }

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) => db),
        sseAgentClientProvider.overrideWith((ref) => _RecordingSseClient(ref)),
      ],
    );
    sse = container.read(sseAgentClientProvider) as _RecordingSseClient;
    // Two turns; each produced one held call. The user has moved on to turn 2.
    await insert('u1', 'user', 'delete record A', 1);
    await insert('a1', 'assistant', 'Confirm?', 2, turnId: 'turn-1');
    await insert('u2', 'user', 'now delete record B', 3);
    await insert('a2', 'assistant', 'Confirm?', 4, turnId: 'turn-2');
    container.read(pendingConfirmationProvider.notifier).addPending([_turnOneCall, _turnTwoCall]);
  });

  tearDown(() async {
    container.dispose();
    await db.close();
  });

  test('approving references the pending call id and its own turn, not the last message',
      () async {
    final ok = await container
        .read(chatRepositoryProvider)
        .confirmPendingCalls(conversationId: 'conv-1', calls: [_turnOneCall]);

    expect(ok, isTrue);
    expect(sse.requests, hasLength(1));
    expect(sse.requests.single.approvalIds, ['apr.tok1.n1']);
    // Turn 1's own message -- not 'now delete record B', the latest user message.
    expect(sse.requests.single.message, 'delete record A');

    // Turn 1's held turn is consumed; turn 2's call (same call id `step_s1`) is untouched.
    final left = container.read(pendingConfirmationProvider);
    expect(left.map((c) => c.approvalId), ['apr.tok2.n1']);
  });

  test('approval is refused while another reply is streaming, and nothing is lost', () async {
    container.read(streamingMessageIdProvider.notifier).set('a2');

    final ok = await container
        .read(chatRepositoryProvider)
        .confirmPendingCalls(conversationId: 'conv-1', calls: [_turnOneCall]);

    expect(ok, isFalse);
    expect(sse.requests, isEmpty);
    expect(container.read(pendingConfirmationProvider), hasLength(2));
  });

  test('a call without a server approval id cannot be confirmed by name', () async {
    const nameOnly = PendingToolCall(tool: 'delete_record', turnId: 'turn-1');

    final ok = await container
        .read(chatRepositoryProvider)
        .confirmPendingCalls(conversationId: 'conv-1', calls: [nameOnly]);

    expect(ok, isFalse);
    expect(sse.requests, isEmpty);
  });

  test('one approval cannot span two turns', () async {
    final ok = await container
        .read(chatRepositoryProvider)
        .confirmPendingCalls(conversationId: 'conv-1', calls: [_turnOneCall, _turnTwoCall]);

    expect(ok, isFalse);
    expect(sse.requests, isEmpty);
  });

  test('rejecting removes exactly that call, not every call sharing its call id', () async {
    await container.read(chatRepositoryProvider).rejectPendingCalls([_turnOneCall]);

    final left = container.read(pendingConfirmationProvider);
    expect(left.map((c) => c.approvalId), ['apr.tok2.n1']);
  });

  test('the done frame carries each held call\'s approval id', () {
    final meta = TurnMeta.fromJson({
      'turn_id': 'turn-9',
      'pending_tool_calls': [
        {'call_id': 'step_s1', 'tool': 'delete_record', 'arguments': {'target': 'A'},
         'approval_id': 'apr.tok9.n1'},
      ],
    });
    expect(meta.pendingToolCalls.single.approvalId, 'apr.tok9.n1');
    expect(meta.pendingToolCalls.single.canConfirm, isTrue);
    expect(PendingToolCall.fromJson(meta.pendingToolCalls.single.toJson()).approvalId,
        'apr.tok9.n1');
  });

  group('confirmation card', () {
    Future<void> pump(WidgetTester tester, List<PendingToolCall> pending,
        {String? streaming}) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            pendingConfirmationProvider.overrideWith(() => _Seeded(pending)),
            streamingMessageIdProvider.overrideWith(() => _Streaming(streaming)),
          ],
          child: MaterialApp(
            theme: AuraThemeV2.dark,
            home: const Scaffold(body: EngineerConfirmationCard(turnId: 'turn-1')),
          ),
        ),
      );
      await tester.pumpAndSettle();
    }

    AuraButton confirmButton(WidgetTester tester) => tester
        .widgetList<AuraButton>(find.byType(AuraButton))
        .singleWhere((b) => b.label.startsWith('Confirm'));

    testWidgets('Confirm is enabled for an approvable call of this turn', (tester) async {
      await pump(tester, [_turnOneCall, _turnTwoCall]);
      expect(confirmButton(tester).onPressed, isNotNull);
    });

    testWidgets('Confirm is disabled while another reply is streaming', (tester) async {
      await pump(tester, [_turnOneCall], streaming: 'a2');
      expect(confirmButton(tester).onPressed, isNull);
    });

    testWidgets('Confirm is disabled for a call with no approval id', (tester) async {
      await pump(tester, [const PendingToolCall(tool: 'delete_record', turnId: 'turn-1')]);
      expect(confirmButton(tester).onPressed, isNull);
    });
  });
}

class _Seeded extends PendingConfirmation {
  _Seeded(this._seed);
  final List<PendingToolCall> _seed;

  @override
  List<PendingToolCall> build() => _seed;
}

class _Streaming extends StreamingMessageId {
  _Streaming(this._id);
  final String? _id;

  @override
  String? build() => _id;
}
