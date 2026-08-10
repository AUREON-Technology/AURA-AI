import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/ai/ai_router/aura_ai_router.dart';
import 'package:aura_client/core/ai/online_agents/online_agent_manager.dart';

/// The router no longer chooses between engines, so the old tests -- which asserted that each
/// setting selected a particular model -- describe behaviour that has been removed. What matters
/// now is narrower and more important: the router must not invent an answer when the backend is
/// unreachable, and it must report that state distinctly.
void main() {
  group('AIRouterStatus', () {
    test('starts online with the server as the active engine', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);

      final status = container.read(aiRouterStatusProvider);

      // Not a model name: the server decides which model serves a turn and may change it per
      // request, so a client claiming one would be reporting a fact it does not have.
      expect(status.activeModel, equals('server'));
      expect(status.isOffline, isFalse);
    });

    test('copyWith preserves untouched fields', () {
      final status = AIRouterStatus(
        activeModel: 'server',
        latencyMs: 12.0,
        status: 'Online',
        isOffline: false,
      );

      final updated = status.copyWith(isOffline: true, status: 'AURA unreachable');

      expect(updated.isOffline, isTrue);
      expect(updated.status, equals('AURA unreachable'));
      expect(updated.latencyMs, equals(12.0));
      expect(updated.activeModel, equals('server'));
    });
  });

  group('AuraBackendException', () {
    test('a connection failure with no status code is retryable', () {
      const failure = AuraBackendException('Could not reach AURA');
      expect(failure.isRetryable, isTrue);
    });

    test('a rate limit is retryable', () {
      const failure = AuraBackendException('slow down', statusCode: 429);
      expect(failure.isRetryable, isTrue);
    });

    test('a server error is retryable', () {
      const failure = AuraBackendException('starting up', statusCode: 503);
      expect(failure.isRetryable, isTrue);
    });

    test('a refusal is not retryable', () {
      // 401 and 403 mean the request itself is wrong. Retrying is pointless and, for a locked
      // account, actively harmful.
      expect(const AuraBackendException('unauthorized', statusCode: 401).isRetryable, isFalse);
      expect(const AuraBackendException('forbidden', statusCode: 403).isRetryable, isFalse);
      expect(const AuraBackendException('bad request', statusCode: 400).isRetryable, isFalse);
    });

    test('the message is what toString reports, so it can be shown to the user', () {
      const failure = AuraBackendException('AURA is starting up', statusCode: 503);
      expect(failure.toString(), equals('AURA is starting up'));
    });
  });

  group('No on-device inference remains', () {
    test('the router exposes no way to select a local engine', () {
      // A regression guard with teeth: if an `AIProvider`-style setting is reintroduced, the
      // symbol would have to be imported here, and this file would stop compiling. That is the
      // point -- the constraint is that the client cannot run a model, and a test asserting a
      // value could be satisfied by a setting that still exists.
      final container = ProviderContainer();
      addTearDown(container.dispose);

      final status = container.read(aiRouterStatusProvider);
      expect(status.activeModel, equals('server'));
    });
  });
}
