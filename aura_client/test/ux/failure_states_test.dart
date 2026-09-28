/// Phase 11, rule 35: "never make every connection problem look like a
/// generic error."
///
/// The backend already answers with a stable `kind` slug for ~30 distinct
/// failures; the client rendered all of them as the same red prose. These
/// cover the classification, the copy's promises (what is safe, what to do),
/// and the one thing that matters most — that Retry is offered exactly when
/// retrying can actually help.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';

AuraFailure _kind(String slug, {int? status, Duration? retryAfter}) {
  return AuraFailure.from(AuraApiError(
    error: slug,
    message: 'server said something',
    retryable: true,
    statusCode: status,
    retryAfter: retryAfter,
  ));
}

void main() {
  group('classification by the backend kind slug', () {
    test('inference down is not the same state as unreachable', () {
      // The distinction the user actually needs: did anything answer?
      expect(_kind('unreachable').kind, AuraFailureKind.unreachable);
      expect(_kind('all_providers_failed', status: 503).kind,
          AuraFailureKind.inferenceUnavailable);
      expect(_kind('provider_error', status: 503).kind, AuraFailureKind.inferenceUnavailable);
    });

    test('busy, rate limited and turn-in-flight are three different waits', () {
      expect(_kind('overloaded').kind, AuraFailureKind.busy);
      expect(_kind('rate_limited', status: 429).kind, AuraFailureKind.rateLimited);
      expect(_kind('user_concurrency_limit').kind, AuraFailureKind.turnInFlight);
    });

    test('auth, permission, budget and safety each classify separately', () {
      expect(_kind('authentication_error', status: 401).kind, AuraFailureKind.authRequired);
      expect(_kind('authorization_error', status: 403).kind, AuraFailureKind.forbidden);
      expect(_kind('budget_exceeded').kind, AuraFailureKind.budgetExceeded);
      expect(_kind('safety_violation', status: 400).kind, AuraFailureKind.blocked);
    });

    test('indexing and degraded are recognised', () {
      expect(_kind('index_not_ready').kind, AuraFailureKind.indexing);
      expect(_kind('timeout').kind, AuraFailureKind.degraded);
      expect(_kind('dependency_unavailable').kind, AuraFailureKind.degraded);
    });
  });

  group('falling back to the status code', () {
    test('an unrecognised slug still classifies by status', () {
      expect(_kind('some_future_kind', status: 401).kind, AuraFailureKind.authRequired);
      expect(_kind('some_future_kind', status: 403).kind, AuraFailureKind.forbidden);
      expect(_kind('some_future_kind', status: 429).kind, AuraFailureKind.rateLimited);
      expect(_kind('some_future_kind', status: 502).kind, AuraFailureKind.degraded);
    });

    test('no status and no known slug means nothing answered', () {
      expect(_kind('some_future_kind').kind, AuraFailureKind.unreachable);
    });

    test('a non-API error is classified rather than thrown past', () {
      final failure = AuraFailure.from(StateError('boom'));
      expect(failure.kind, AuraFailureKind.unknown);
      expect(failure.canRetry, isTrue);
      expect(failure.detail, contains('boom'));
    });
  });

  group('retry is offered only where retrying helps', () {
    test('not for a safety refusal — the same request refuses the same way', () {
      expect(_kind('safety_violation').canRetry, isFalse);
    });

    test('not for permission, auth or budget', () {
      expect(_kind('authorization_error').canRetry, isFalse);
      expect(_kind('authentication_error').canRetry, isFalse);
      expect(_kind('budget_exceeded').canRetry, isFalse);
    });

    test('yes for anything transient', () {
      for (final slug in const [
        'unreachable',
        'all_providers_failed',
        'overloaded',
        'rate_limited',
        'index_not_ready',
        'timeout',
      ]) {
        expect(_kind(slug).canRetry, isTrue, reason: slug);
      }
    });
  });

  group('the copy keeps its promises', () {
    test('unreachable does not claim to know whose fault it is', () {
      final message = _kind('unreachable').message.toLowerCase();
      // Saying "you are offline" when the server is down sends the user to
      // check their wifi for nothing.
      expect(message, contains('your connection or the server'));
      expect(message, isNot(contains('no internet')));
    });

    test('inference-down says the server is up, because that is the useful part', () {
      final failure = _kind('all_providers_failed', status: 503);
      expect(failure.message, contains('Control Plane is up'));
    });

    test('a rate limit with Retry-After names the wait', () {
      expect(
        _kind('rate_limited', status: 429, retryAfter: const Duration(seconds: 20)).message,
        contains('20 seconds'),
      );
      expect(
        _kind('rate_limited', status: 429, retryAfter: const Duration(seconds: 1)).message,
        contains('1 second.'),
      );
      expect(
        _kind('rate_limited', status: 429, retryAfter: const Duration(seconds: 150)).message,
        contains('3 minutes'),
      );
    });

    test('without a Retry-After it says "shortly" rather than inventing a number', () {
      expect(_kind('rate_limited', status: 429).message, contains('shortly'));
    });
  });

  group('AuraFailureView', () {
    Future<void> pump(WidgetTester tester, AuraFailure failure, {VoidCallback? onRetry}) {
      return tester.pumpWidget(MaterialApp(
        theme: AuraThemeV2.dark,
        home: Scaffold(
          body: Center(
            child: SingleChildScrollView(
              child: AuraFailureView(failure: failure, onRetry: onRetry),
            ),
          ),
        ),
      ));
    }

    testWidgets('renders a retry for a transient failure and runs it', (tester) async {
      var retried = 0;
      await pump(tester, _kind('unreachable'), onRetry: () => retried++);

      expect(find.text("Couldn't reach AURA"), findsOneWidget);
      await tester.tap(find.text('Retry'));
      expect(retried, 1);
    });

    testWidgets('renders no retry for a safety refusal, even when handed one',
        (tester) async {
      // The caller passing onRetry must not be able to put a useless button on
      // screen — the failure decides.
      await pump(tester, _kind('safety_violation'), onRetry: () {});
      expect(find.text('Retry'), findsNothing);
      expect(find.textContaining('rephrasing'), findsOneWidget);
    });

    testWidgets('each kind maps to a distinct icon', (tester) async {
      final icons = AuraFailureKind.values.map(AuraFailureView.iconFor).toSet();
      expect(icons, hasLength(AuraFailureKind.values.length));
    });

    testWidgets('tone follows what to do, not how bad it sounds', (tester) async {
      // A rate limit is "wait", not "broken".
      expect(AuraFailureView.toneFor(AuraFailureKind.rateLimited), AuraStateTone.warning);
      expect(AuraFailureView.toneFor(AuraFailureKind.unreachable), AuraStateTone.offline);
      expect(AuraFailureView.toneFor(AuraFailureKind.forbidden), AuraStateTone.error);
    });
  });
}
