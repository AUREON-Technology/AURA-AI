// AUD-07: the workspace and billing routes raise worded refusals; the screens must show them.
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/features/workspaces/presentation/workspace_providers.dart';
import 'package:flutter_test/flutter_test.dart';

AuraApiError _error(int status, String slug, String message) => AuraApiError(
      error: slug,
      message: message,
      retryable: false,
      statusCode: status,
    );

void main() {
  test('a 422 seats refusal says which range the plan takes', () {
    final text = failureText(_error(422, 'validation_error', "plan 'team' takes 2-50 seats"));
    expect(text, contains("plan 'team' takes 2-50 seats"));
  });

  test('a 403 for an unverified email keeps the generic line and adds the reason', () {
    final text = failureText(
        _error(403, 'authorization_error', 'verify your email address before buying a plan'));
    expect(text, contains('verify your email address before buying a plan'));
  });

  test('a 409 conflict explains itself', () {
    final text = failureText(_error(409, 'conflict', 'transfer ownership first'));
    expect(text, contains('transfer ownership first'));
  });

  test('a server error does not echo the server message', () {
    final text = failureText(_error(500, 'internal_error', 'stack trace here'));
    expect(text, isNot(contains('stack trace here')));
  });

  test('the same sentence is not printed twice', () {
    final once = failureText(_error(409, 'conflict', 'transfer ownership first'));
    expect('transfer ownership first'.allMatches(once).length, 1);
  });
}
