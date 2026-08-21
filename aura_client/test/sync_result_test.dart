import 'package:aura_client/core/sync/sync_result.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('success has no failure kind', () {
    const result = SyncResult.success();
    expect(result.ok, isTrue);
    expect(result.failure, isNull);
  });

  test('maps HTTP classes into distinct failure kinds', () {
    expect(const SyncResult.fail(SyncFailureKind.unauthorized).ok, isFalse);
    expect(
      const SyncResult.fail(SyncFailureKind.unauthorized).failure,
      SyncFailureKind.unauthorized,
    );
    expect(
      const SyncResult.fail(SyncFailureKind.unavailable).failure,
      SyncFailureKind.unavailable,
    );
    expect(
      const SyncResult.fail(SyncFailureKind.network).failure,
      SyncFailureKind.network,
    );
  });
}
