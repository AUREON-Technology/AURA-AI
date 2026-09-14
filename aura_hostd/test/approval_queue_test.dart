import 'package:aura_hostd/src/approval_queue.dart';
import 'package:test/test.dart';

void main() {
  group('ApprovalQueue', () {
    test('create returns a pending approval that appears in list()', () {
      final queue = ApprovalQueue();
      final approval =
          queue.create(canonicalCommand: 'npm install left-pad', cwd: '/ws', rootPath: '/ws');
      expect(approval.status, ApprovalStatus.pending);
      expect(queue.list().map((a) => a.id), contains(approval.id));
    });

    test('respond(approved: true) resolves the decision and removes it from list()', () async {
      final queue = ApprovalQueue();
      final approval = queue.create(canonicalCommand: 'npm test', cwd: '/ws', rootPath: '/ws');
      final found = queue.respond(approval.id, true);
      expect(found, isTrue);
      expect(await approval.decision, ApprovalStatus.approved);
      expect(queue.list(), isEmpty);
    });

    test('respond(approved: false) resolves to rejected', () async {
      final queue = ApprovalQueue();
      final approval = queue.create(canonicalCommand: 'npm test', cwd: '/ws', rootPath: '/ws');
      queue.respond(approval.id, false);
      expect(await approval.decision, ApprovalStatus.rejected);
    });

    test('respond on an unknown id returns false', () {
      final queue = ApprovalQueue();
      expect(queue.respond('appr_does_not_exist', true), isFalse);
    });

    test('responding twice is idempotent -- the second response does not overwrite the first',
        () async {
      final queue = ApprovalQueue();
      final approval = queue.create(canonicalCommand: 'npm test', cwd: '/ws', rootPath: '/ws');
      expect(queue.respond(approval.id, true), isTrue);
      expect(queue.respond(approval.id, false), isTrue, reason: 'already-resolved id is a no-op, not an error');
      expect(await approval.decision, ApprovalStatus.approved, reason: 'first decision wins');
    });

    test('displayCommand redacts a bearer token but canonicalCommand keeps the real one', () {
      final queue = ApprovalQueue();
      final approval = queue.create(
        canonicalCommand: 'curl -H "Authorization: Bearer sk-real-secret-value" https://example.com',
        cwd: '/ws',
        rootPath: '/ws',
      );
      expect(approval.canonicalCommand, contains('sk-real-secret-value'));
      expect(approval.displayCommand, isNot(contains('sk-real-secret-value')));
      expect(approval.displayCommand, contains('[REDACTED]'));
    });

    test('toJson never includes canonicalCommand, only the redacted displayCommand', () {
      final queue = ApprovalQueue();
      final approval = queue.create(
        canonicalCommand: 'export TOKEN=super-secret-value && run-thing',
        cwd: '/ws',
        rootPath: '/ws',
      );
      final json = approval.toJson();
      expect(json.values.join(' '), isNot(contains('super-secret-value')));
    });

    test('commandHash is stable for identical command/cwd/root and differs when root changes', () {
      final a = PendingApproval.hashFor(command: 'npm test', cwd: '/ws', rootPath: '/ws');
      final b = PendingApproval.hashFor(command: 'npm test', cwd: '/ws', rootPath: '/ws');
      final c = PendingApproval.hashFor(command: 'npm test', cwd: '/ws', rootPath: '/other-ws');
      expect(a, equals(b));
      expect(a, isNot(equals(c)));
    });

    test('two concurrent approvals for the same workspace resolve independently', () async {
      final queue = ApprovalQueue();
      final first = queue.create(canonicalCommand: 'npm install a', cwd: '/ws', rootPath: '/ws');
      final second = queue.create(canonicalCommand: 'npm install b', cwd: '/ws', rootPath: '/ws');
      queue.respond(second.id, true);
      queue.respond(first.id, false);
      expect(await first.decision, ApprovalStatus.rejected);
      expect(await second.decision, ApprovalStatus.approved);
    });

    test('awaitDecision resolves immediately once respond() is called, without waiting for the timeout', () async {
      final queue = ApprovalQueue();
      final approval = queue.create(canonicalCommand: 'npm test', cwd: '/ws', rootPath: '/ws');
      final future = queue.awaitDecision(approval);
      queue.respond(approval.id, true);
      final status = await future.timeout(const Duration(seconds: 2));
      expect(status, ApprovalStatus.approved);
    });
  });

  group('redactForDisplay', () {
    test('redacts a Bearer token', () {
      final result = redactForDisplay('curl -H "Authorization: Bearer abc.def.ghi"');
      expect(result, isNot(contains('abc.def.ghi')));
    });

    test('redacts an env-style token= assignment', () {
      final result = redactForDisplay('MY_API_KEY=super-secret-123 run-thing');
      expect(result, isNot(contains('super-secret-123')));
    });

    test('leaves an ordinary command untouched', () {
      const command = 'npm install left-pad --save-dev';
      expect(redactForDisplay(command), command);
    });
  });
}
