import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:drift/native.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// FSA-006, client half: the Desktop IDE's active workspace root travels with the turn, and a
/// retry or an approval resend of that turn keeps it, so the daemon runs the Engineer's commands
/// in the root the user was looking at rather than in whichever root was granted first.
class _RecordingSseClient extends SseAgentClient {
  final roots = <String?>[];
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
  }) async* {
    roots.add(workspaceRoot);
    if (failFirst && roots.length == 1) {
      throw Exception('SSE chat request failed with status 503: unavailable');
    }
    yield SseChatEvent.fromWire('token', {'text': 'Ran it.'});
    yield SseChatEvent.fromWire('done', {});
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const root = '/home/me/projects/aura';
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

  group('workspace root', () {
    test('goes with the turn it was sent for', () async {
      late _RecordingSseClient sse;
      final container = containerWith((ref) => sse = _RecordingSseClient(ref));

      await container.read(chatRepositoryProvider).sendChatMessage(
          conversationId: 'conv-1', userContent: 'run the tests', workspaceRoot: root);

      expect(sse.roots, [root]);
    });

    test('is absent for a surface that has none', () async {
      late _RecordingSseClient sse;
      final container = containerWith((ref) => sse = _RecordingSseClient(ref));

      await container
          .read(chatRepositoryProvider)
          .sendChatMessage(conversationId: 'conv-1', userContent: 'hello');

      expect(sse.roots, [null]);
    });

    test('a retry of a failed turn keeps the root the turn was sent with', () async {
      late _RecordingSseClient sse;
      final container = containerWith((ref) => sse = _RecordingSseClient(ref, failFirst: true));
      final repo = container.read(chatRepositoryProvider);

      await repo.sendChatMessage(
          conversationId: 'conv-1', userContent: 'run the tests', workspaceRoot: root);
      final error = (await db.select(db.messages).get()).singleWhere((m) => m.role == 'assistant');
      await repo.retryLastFailedTurn(conversationId: 'conv-1', errorMessageId: error.id);

      expect(sse.roots, [root, root]);
    });

    test('a later turn with a different root replaces the remembered one', () async {
      late _RecordingSseClient sse;
      final container = containerWith((ref) => sse = _RecordingSseClient(ref));
      final repo = container.read(chatRepositoryProvider);

      await repo.sendChatMessage(
          conversationId: 'conv-1', userContent: 'first', workspaceRoot: root);
      await repo.sendChatMessage(
          conversationId: 'conv-1', userContent: 'second', workspaceRoot: '/home/me/other');

      expect(sse.roots, [root, '/home/me/other']);
    });
  });
}
