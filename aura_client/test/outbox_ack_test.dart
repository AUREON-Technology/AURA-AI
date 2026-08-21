import 'package:aura_client/core/sync/outbox_ack.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('drops applied and conflicted rows, keeps error rows', () {
    const pending = ['ob-1', 'ob-2', 'ob-3'];
    final drop = outboxIdsToDrop(
      pendingIds: pending,
      results: [
        {'id': 'rec-a', 'table': 'messages', 'status': 'applied'},
        {'id': 'rec-b', 'table': 'messages', 'status': 'error', 'detail': 'unknown table'},
        {'id': 'rec-c', 'table': 'messages', 'status': 'conflict'},
      ],
    );
    expect(drop, ['ob-1', 'ob-3']);
  });

  test('leaves every row when the result list is empty', () {
    expect(
      outboxIdsToDrop(pendingIds: ['ob-1', 'ob-2'], results: const []),
      isEmpty,
    );
  });
}
