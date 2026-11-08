import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/ai/orchestrator/agent_orchestrator.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/chat_connection_error.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:drift/native.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

class _ActionsSseClient extends SseAgentClient {
  _ActionsSseClient(super.ref);

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
  }) async* {
    yield SseChatEvent.fromWire('token', {'text': 'I can add that.'});
    yield SseChatEvent.fromWire('actions', {
      'actions': [
        {
          'type': 'add_task',
          'params': {'title': 'Buy milk', 'priority': 'high'},
        },
      ],
    });
    yield SseChatEvent.fromWire('done', {});
  }
}

class _Status500SseClient extends SseAgentClient {
  _Status500SseClient(super.ref);

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
  }) async* {
    throw Exception('SSE chat request failed with status 500: internal error');
  }
}

class _SpyOrchestrator extends ClientAgentOrchestrator {
  int calls = 0;

  _SpyOrchestrator(super.ref);

  @override
  Stream<String> executeAgentStream({
    required String agentId,
    required List<Map<String, String>> messages,
    Map<String, String>? customInstructions,
    bool useRag = true,
  }) async* {
    calls++;
    yield 'orchestrator must not run';
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
  });

  test('actions frames populate pendingActionsProvider', () async {
    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
        sseAgentClientProvider.overrideWith((ref) => _ActionsSseClient(ref)),
      ],
    );
    addTearDown(container.dispose);

    final repo = container.read(chatRepositoryProvider);
    await repo.sendChatMessage(conversationId: 'conv-1', userContent: 'add a task');

    final actions = container.read(pendingActionsProvider);
    expect(actions, hasLength(1));
    expect(actions.first['type'], 'add_task');
    expect(actions.first['params']['title'], 'Buy milk');

    final assistant = (await db.select(db.messages).get())
        .singleWhere((m) => m.role == 'assistant');
    expect(assistant.content, 'I can add that.');
  });

  test('SSE failure does not invoke the v1 orchestrator', () async {
    _SpyOrchestrator? spy;
    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
        sseAgentClientProvider.overrideWith((ref) => _Status500SseClient(ref)),
        clientAgentOrchestratorProvider.overrideWith((ref) {
          spy = _SpyOrchestrator(ref);
          return spy!;
        }),
      ],
    );
    addTearDown(container.dispose);

    final repo = container.read(chatRepositoryProvider);
    await repo.sendChatMessage(conversationId: 'conv-1', userContent: 'hello');

    expect(spy?.calls ?? 0, 0);
    final assistant = (await db.select(db.messages).get())
        .singleWhere((m) => m.role == 'assistant');
    expect(isChatConnectionErrorText(assistant.content), isTrue);
  });
}
