/// PP-10 (provider performance audit, 2026-11-08): during the chat-only launch,
/// a turn whose content no available model may see must read as a stated
/// limitation, not as a connection error with a Retry card that can never work.
library;

import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/chat_connection_error.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:drift/native.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Replays the error frame `AIKernel.stream()` sends for a
/// `CapabilityUnavailableError`, or for a plain outage when [kind] says so.
class _ErrorFrameSseClient extends SseAgentClient {
  _ErrorFrameSseClient(super.ref, this.kind);

  final String kind;

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
    yield SseChatEvent.fromWire('error', {
      'type': 'error',
      'error': kind,
      'message': 'all 1 provider attempt(s) failed before streaming began (hfspace:provider_error)',
      'retryable': kind != 'capability_unavailable',
    });
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('error frame parsing', () {
    test('the backend slug is kept on an error frame', () {
      final event = SseChatEvent.fromWire('error', {
        'error': 'capability_unavailable',
        'message': 'no provider permitted to receive this turn',
      });
      expect(event.kind, SseEventKind.error);
      expect(event.errorKind, 'capability_unavailable');
    });

    test('a frame without a slug still parses', () {
      final event = SseChatEvent.fromWire('error', {'message': 'boom'});
      expect(event.errorKind, isNull);
    });
  });

  group('failure classification', () {
    test('capability_unavailable is its own state and never offers Retry', () {
      final failure = AuraFailure.from(const AuraApiError(
        error: 'capability_unavailable',
        message: 'no provider permitted to receive this turn',
        retryable: false,
        statusCode: 503,
      ));
      expect(failure.kind, AuraFailureKind.featureUnavailable);
      expect(failure.canRetry, isFalse);
      expect(failure.title, kFeatureUnavailableTitle);
      expect(failure.message, kFeatureUnavailableMessage);
    });

    test('a real inference outage keeps its retryable state', () {
      final failure = AuraFailure.from(const AuraApiError(
        error: 'all_providers_failed',
        message: 'down',
        retryable: true,
        statusCode: 503,
      ));
      expect(failure.kind, AuraFailureKind.inferenceUnavailable);
      expect(failure.canRetry, isTrue);
    });
  });

  group('chat transcript', () {
    late AppDatabase db;

    setUp(() async {
      await EncryptionService.init();
      db = AppDatabase.forTesting(NativeDatabase.memory());
    });

    Future<String> sendWith(String kind) async {
      final container = ProviderContainer(
        overrides: [
          databaseProvider.overrideWith((ref) {
            ref.onDispose(db.close);
            return db;
          }),
          sseAgentClientProvider.overrideWith((ref) => _ErrorFrameSseClient(ref, kind)),
        ],
      );
      addTearDown(container.dispose);
      await container
          .read(chatRepositoryProvider)
          .sendChatMessage(conversationId: 'conv-1', userContent: 'summarise my notes');
      final assistant = (await db.select(db.messages).get())
          .singleWhere((m) => m.role == 'assistant');
      return assistant.content;
    }

    test('a content refusal shows the approved copy and no Retry card', () async {
      final text = await sendWith('capability_unavailable');
      expect(text, kChatFeatureUnavailableText);
      expect(isChatConnectionErrorText(text), isFalse);
    });

    test('an ordinary provider failure is still a retryable connection error', () async {
      final text = await sendWith('all_providers_failed');
      expect(isChatConnectionErrorText(text), isTrue);
    });
  });
}
