// WP1 / WP2 (D-03, D-05): the two Engineer refusals are not connection errors. A turn refused
// for missing consent asks for consent (no Retry card); a turn refused by the daily cap says when
// the cap resets, in local time (no Retry card either). Both arrive as an SSE `error` frame, or
// as an HTTP 403 / 429 before the stream opens.
import 'package:aura_client/core/ai/engineer/engineer_consent_repository.dart';
import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/chat_connection_error.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:drift/native.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/intl.dart';

const _resetAt = '2026-11-18T00:00:00+00:00';

/// Replays one refusal, as a frame or as the HTTP error the stream request would throw.
class _RefusingSseClient extends SseAgentClient {
  _RefusingSseClient(super.ref, {required this.kind, required this.details, this.asHttp = false});

  final String kind;
  final Map<String, dynamic> details;
  final bool asHttp;

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
    if (asHttp) {
      throw AuraApiError(
        error: kind,
        message: 'refused',
        retryable: false,
        statusCode: kind == 'consent_required' ? 403 : 429,
        details: details,
      );
    }
    yield SseChatEvent.fromWire('error', {
      'type': 'error',
      'error': kind,
      'message': 'refused',
      'retryable': false,
      'details': details,
    });
  }
}

/// Counts fetches, so a test can see the cached consent being dropped.
class _CountingConsentRepository implements EngineerConsentRepository {
  int fetches = 0;

  @override
  Future<EngineerConsent> fetch() async {
    fetches++;
    return const EngineerConsent(
      accepted: true,
      noticeVersion: kEngineerConsentNoticeVersion,
      currentVersion: kEngineerConsentNoticeVersion,
    );
  }

  @override
  Future<EngineerConsent> accept({String noticeVersion = kEngineerConsentNoticeVersion}) =>
      throw UnimplementedError();

  @override
  Future<EngineerConsent> withdraw() => throw UnimplementedError();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(EncryptionService.init);

  Future<(ProviderContainer, Message, _CountingConsentRepository)> send(
    String kind,
    Map<String, dynamic> details, {
    bool asHttp = false,
  }) async {
    final db = AppDatabase.forTesting(NativeDatabase.memory());
    final consent = _CountingConsentRepository();
    final container = ProviderContainer(overrides: [
      databaseProvider.overrideWith((ref) {
        ref.onDispose(db.close);
        return db;
      }),
      sseAgentClientProvider.overrideWith(
        (ref) => _RefusingSseClient(ref, kind: kind, details: details, asHttp: asHttp),
      ),
      engineerConsentRepositoryProvider.overrideWithValue(consent),
    ]);
    addTearDown(container.dispose);
    // The app believed consent was current before this turn.
    await container.read(engineerConsentProvider.future);
    await container
        .read(chatRepositoryProvider)
        .sendChatMessage(conversationId: 'conv-1', userContent: 'fix the build');
    final assistant =
        (await db.select(db.messages).get()).singleWhere((m) => m.role == 'assistant');
    return (container, assistant, consent);
  }

  for (final asHttp in [false, true]) {
    final via = asHttp ? 'an HTTP 403' : 'a frame';

    test('consent_required as $via: a notice row, no Retry, a consent prompt', () async {
      final (container, assistant, consent) =
          await send('consent_required', {'current_version': '2026-11-17'}, asHttp: asHttp);

      expect(assistant.content, kChatEngineerConsentNeededText);
      expect(isChatConnectionErrorText(assistant.content), isFalse, reason: 'no Retry card');

      final prompt = container.read(engineerConsentPromptProvider);
      expect(prompt, isNotNull);
      expect(prompt!.conversationId, 'conv-1');
      expect(prompt.messageId, assistant.id);
      expect(prompt.currentVersion, '2026-11-17');
      expect(container.read(streamingMessageIdProvider), isNull,
          reason: 'released before the prompt, so agreeing can resend');

      // The cached "accepted" was stale: it is fetched again.
      await container.read(engineerConsentProvider.future);
      expect(consent.fetches, 2);
    });

    test('user_quota_exhausted as ${asHttp ? 'an HTTP 429' : 'a frame'}: '
        'the limit message with the local reset time, no Retry', () async {
      final (container, assistant, _) =
          await send('user_quota_exhausted', {'reset_at': _resetAt, 'limit_tokens': 200000}, asHttp: asHttp);

      final localTime = DateFormat.jm().format(DateTime.parse(_resetAt).toLocal());
      expect(assistant.content, startsWith("You’ve reached today’s Engineer limit. It resets at "));
      expect(assistant.content, contains(localTime));
      expect(isChatConnectionErrorText(assistant.content), isFalse, reason: 'no Retry card');
      expect(container.read(engineerConsentPromptProvider), isNull);
    });
  }

  group('engineerLimitReachedText', () {
    test('names the reset in local time, with the day when it is not today', () {
      final reset = DateTime.parse(_resetAt).toLocal();
      final sameDay = engineerLimitReachedText({'reset_at': _resetAt}, now: reset);
      expect(sameDay,
          "You’ve reached today’s Engineer limit. It resets at ${DateFormat.jm().format(reset)}.");

      final dayBefore = engineerLimitReachedText(
        {'reset_at': _resetAt},
        now: reset.subtract(const Duration(days: 1)),
      );
      expect(dayBefore, contains(DateFormat('EEE').add_jm().format(reset)));
    });

    test('a timestamp without an offset is UTC, as the contract says', () {
      final reset = DateTime.parse(_resetAt).toLocal();
      expect(
        engineerLimitReachedText({'reset_at': '2026-11-18T00:00:00'}, now: reset),
        engineerLimitReachedText({'reset_at': _resetAt}, now: reset),
      );
    });

    test('without a reset time it still says what happened', () {
      expect(engineerLimitReachedText(null),
          "You’ve reached today’s Engineer limit. Try again after it resets.");
      expect(engineerLimitReachedText({'reset_at': 'soon'}),
          "You’ve reached today’s Engineer limit. Try again after it resets.");
    });
  });
}
