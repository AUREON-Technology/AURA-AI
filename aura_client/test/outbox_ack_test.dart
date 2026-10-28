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

  test('a non-retryable error is marked so; one without the flag counts as retryable', () {
    final ack = classifyOutboxResults(
      pendingIds: ['ob-1', 'ob-2'],
      results: [
        {'id': 'rec-a', 'table': 'tasks', 'status': 'error', 'detail': 'invalid value in payload', 'retryable': false},
        {'id': 'rec-b', 'table': 'tasks', 'status': 'error', 'detail': 'unknown table'},
      ],
    );
    expect(ack.drop, isEmpty);
    expect(ack.failures.map((f) => (f.outboxId, f.retryable)), [('ob-1', false), ('ob-2', true)]);
    expect(ack.failures.first.detail, 'invalid value in payload');
  });

  test('server rows carried by conflict and merge results are collected by table', () {
    final ack = classifyOutboxResults(
      pendingIds: ['ob-1', 'ob-2', 'ob-3'],
      results: [
        {'id': 'rec-a', 'table': 'tasks', 'status': 'conflict', 'row': {'id': 'rec-a'}},
        {'id': 'rec-b', 'table': 'health_records', 'status': 'applied', 'server_id': 'srv', 'row': {'id': 'srv'}},
        {'id': 'rec-c', 'table': 'tasks', 'status': 'applied'},
      ],
    );
    expect(ack.drop, ['ob-1', 'ob-2', 'ob-3']);
    expect(ack.serverRows.keys, unorderedEquals(['tasks', 'health_records']));
    expect(ack.serverRows['health_records']!.single['id'], 'srv');
  });

  test('leaves every row when the result list is empty', () {
    expect(
      outboxIdsToDrop(pendingIds: ['ob-1', 'ob-2'], results: const []),
      isEmpty,
    );
  });
}
