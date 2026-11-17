/// SaaS P1-C: a plan refusal (HTTP 402, `plan_limit`) reads as what the plan allows and how to get
/// more -- never as a generic error, and never offering a retry that will be refused again.
library;

import 'dart:convert';

import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:flutter_test/flutter_test.dart';

AuraFailure _refusal(Map<String, dynamic> details) => AuraFailure.from(AuraApiError.fromResponse(
      statusCode: 402,
      body: jsonEncode({
        'error': 'plan_limit',
        'message': 'limit reached',
        'retryable': false,
        'details': details,
      }),
    ));

void main() {
  test('messages: says they are used up, when they come back, and that upgrading raises it', () {
    final failure = _refusal({
      'limit': 'messages', 'plan': 'free', 'used': 50, 'allowed': 50,
      'resets_at': '2026-12-01T00:00:00+00:00',
    });
    expect(failure.kind, AuraFailureKind.planLimit);
    expect(failure.title, "You've used this month's messages");
    expect(failure.message, contains('December 1'));
    expect(failure.message, contains('Upgrade'));
    expect(failure.canRetry, isFalse);
  });

  test('engineer: names the plans that include it', () {
    final failure = _refusal({'limit': 'engineer', 'plan': 'free', 'used': 0, 'allowed': 0, 'resets_at': null});
    expect(failure.title, 'The Engineer agent is on Pro and Team plans');
    expect(failure.canRetry, isFalse);
  });

  test('storage: says storage is full and how to free it', () {
    final failure = _refusal({'limit': 'storage', 'plan': 'free', 'used': 100, 'allowed': 100, 'resets_at': null});
    expect(failure.title, 'Knowledge storage is full');
    expect(failure.message, contains('Delete'));
  });

  test('seats: says the team is full', () {
    final failure = _refusal({'limit': 'seats', 'plan': 'team', 'used': 5, 'allowed': 5, 'resets_at': null});
    expect(failure.title, 'This team has no free seats');
  });

  test('a bare 402 without our envelope still reads as a plan limit', () {
    final failure = AuraFailure.from(AuraApiError.fromResponse(statusCode: 402, body: 'Payment Required'));
    expect(failure.kind, AuraFailureKind.planLimit);
    expect(failure.canRetry, isFalse);
  });
}
