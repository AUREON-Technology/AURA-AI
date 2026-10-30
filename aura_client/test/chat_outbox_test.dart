import 'dart:convert';

import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/sync/sync_payloads.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:drift/drift.dart';
import 'package:drift/native.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

class _FailingSseClient extends SseAgentClient {
  _FailingSseClient(super.ref);

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
  }) async* {
    throw http.ClientException(
      'Connection refused',
      Uri.parse('http://localhost:8000/api/v3/chat/stream'),
    );
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late ProviderContainer container;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
        sseAgentClientProvider.overrideWith((ref) => _FailingSseClient(ref)),
      ],
    );
  });

  tearDown(() {
    container.dispose();
  });

  test('sendChatMessage enqueues the user message on the outbox', () async {
    final repo = container.read(chatRepositoryProvider);
    await repo.sendChatMessage(conversationId: 'conv-1', userContent: 'hello from test');

    final messages = await db.select(db.messages).get();
    final user = messages.singleWhere((m) => m.role == 'user');
    expect(user.content, 'hello from test');

    final outbox = await db.select(db.outboxEntries).get();
    final userUpserts = outbox.where(
      (e) => e.targetTable == 'messages' && e.operation == 'upsert' && e.recordId == user.id,
    );
    expect(userUpserts, isNotEmpty);

    final payload = jsonDecode(userUpserts.first.payloadJson!) as Map<String, dynamic>;
    expect(payload['conversation_id'], 'conv-1');
    expect(payload['role'], 'user');
    expect(payload.containsKey('content_enc'), isTrue);
    expect(EncryptionService.decryptText(payload['content_enc'] as String), 'hello from test');
  });

  test('SyncPayloads.message uses Postgres column names and ciphertext', () async {
    await db.into(db.messages).insert(
      MessagesCompanion.insert(
        id: '11111111-1111-1111-1111-111111111111',
        conversationId: 'c',
        role: 'assistant',
        content: 'plain',
        createdAt: DateTime.now(),
        modelUsed: const Value('gpt'),
        tokensUsed: const Value(12),
        citationsJson: Value(jsonEncode(['doc-a'])),
        toolCallsJson: Value(jsonEncode([
          {'name': 'search_knowledge', 'settled': true},
        ])),
        agentId: const Value('orchestrator'),
      ),
    );
    final row = await (db.select(db.messages)..where((m) => m.id.equals('11111111-1111-1111-1111-111111111111')))
        .getSingle();
    final payload = SyncPayloads.message(row);
    expect(payload.keys, containsAll([
      'conversation_id',
      'role',
      'content_enc',
      'model_used',
      'tokens_used',
      'citations_enc',
      'tool_calls_enc',
      'persona',
    ]));
    expect(payload['persona'], 'orchestrator');
    expect(payload['content_enc'], isNot('plain'));
    expect(EncryptionService.decryptText(payload['content_enc'] as String), 'plain');
    expect(EncryptionService.decryptText(payload['citations_enc'] as String), jsonEncode(['doc-a']));
  });
}
