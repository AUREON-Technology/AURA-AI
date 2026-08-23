import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('parses the standard v3 envelope', () {
    final error = AuraApiError.fromResponse(
      statusCode: 400,
      body: '{"error":"blocked","message":"AURA blocked this turn.","retryable":false}',
    );
    expect(error.error, 'blocked');
    expect(error.message, 'AURA blocked this turn.');
    expect(error.retryable, isFalse);
    expect(error.statusCode, 400);
  });

  test('parses {error, correlation_id}', () {
    final error = AuraApiError.fromResponse(
      statusCode: 503,
      body: '{"error":"starting","correlation_id":"cid-1"}',
      correlationId: 'header-cid',
    );
    expect(error.error, 'starting');
    expect(error.correlationId, 'cid-1');
    expect(error.retryable, isTrue);
  });

  test('parses plain-text 429', () {
    final error = AuraApiError.fromResponse(
      statusCode: 429,
      body: 'Too many requests',
      retryAfterHeader: '12',
    );
    expect(error.message, 'Too many requests');
    expect(error.retryable, isTrue);
    expect(error.retryAfter, const Duration(seconds: 12));
  });
}
