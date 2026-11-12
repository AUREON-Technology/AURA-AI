import 'package:aura_hostd/src/approval_queue.dart';
import 'package:test/test.dart';

void main() {
  group('ApprovalQueue', () {
    test('create returns a pending approval that appears in list()', () {
      final queue = ApprovalQueue();
      final approval =
          queue.createForCommand(canonicalCommand: 'npm install left-pad', cwd: '/ws', rootPath: '/ws');
      expect(approval.status, ApprovalStatus.pending);
      expect(queue.list().map((a) => a.id), contains(approval.id));
    });

    test('respond(approved: true) resolves the decision and removes it from list()', () async {
      final queue = ApprovalQueue();
      final approval = queue.createForCommand(canonicalCommand: 'npm test', cwd: '/ws', rootPath: '/ws');
      final found = queue.respond(approval.id, true);
      expect(found, isTrue);
      expect(await approval.decision, ApprovalStatus.approved);
      expect(queue.list(), isEmpty);
    });

    test('respond(approved: false) resolves to rejected', () async {
      final queue = ApprovalQueue();
      final approval = queue.createForCommand(canonicalCommand: 'npm test', cwd: '/ws', rootPath: '/ws');
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
      final approval = queue.createForCommand(canonicalCommand: 'npm test', cwd: '/ws', rootPath: '/ws');
      expect(queue.respond(approval.id, true), isTrue);
      expect(queue.respond(approval.id, false), isTrue, reason: 'already-resolved id is a no-op, not an error');
      expect(await approval.decision, ApprovalStatus.approved, reason: 'first decision wins');
    });

    test('displayCommand redacts a bearer token but canonicalCommand keeps the real one', () {
      final queue = ApprovalQueue();
      final approval = queue.createForCommand(
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
      final approval = queue.createForCommand(
        canonicalCommand: 'export TOKEN=super-secret-value && run-thing',
        cwd: '/ws',
        rootPath: '/ws',
      );
      final json = approval.toJson();
      expect(json.values.join(' '), isNot(contains('super-secret-value')));
    });

    test('operationHash (command) is stable for identical command/cwd/root and differs when root changes', () {
      final a = PendingApproval.hashForCommand(command: 'npm test', cwd: '/ws', rootPath: '/ws');
      final b = PendingApproval.hashForCommand(command: 'npm test', cwd: '/ws', rootPath: '/ws');
      final c = PendingApproval.hashForCommand(command: 'npm test', cwd: '/ws', rootPath: '/other-ws');
      expect(a, equals(b));
      expect(a, isNot(equals(c)));
    });

    test('command approvals have kind command and no write fields', () {
      final queue = ApprovalQueue();
      final approval = queue.createForCommand(canonicalCommand: 'npm test', cwd: '/ws', rootPath: '/ws');
      expect(approval.kind, OperationKind.command);
      expect(approval.writeOperations, isNull);
      expect(approval.diffPreview, isNull);
      expect(approval.toJson()['diff_preview'], isNull);
      expect(approval.toJson()['paths'], isNull);
    });

    test('two concurrent approvals for the same workspace resolve independently', () async {
      final queue = ApprovalQueue();
      final first = queue.createForCommand(canonicalCommand: 'npm install a', cwd: '/ws', rootPath: '/ws');
      final second = queue.createForCommand(canonicalCommand: 'npm install b', cwd: '/ws', rootPath: '/ws');
      queue.respond(second.id, true);
      queue.respond(first.id, false);
      expect(await first.decision, ApprovalStatus.rejected);
      expect(await second.decision, ApprovalStatus.approved);
    });

    test('awaitDecision resolves immediately once respond() is called, without waiting for the timeout', () async {
      final queue = ApprovalQueue();
      final approval = queue.createForCommand(canonicalCommand: 'npm test', cwd: '/ws', rootPath: '/ws');
      final future = queue.awaitDecision(approval);
      queue.respond(approval.id, true);
      final status = await future.timeout(const Duration(seconds: 2));
      expect(status, ApprovalStatus.approved);
    });
  });

  group('ApprovalQueue write approvals (D6)', () {
    test('createForWrite returns a kind:write approval with a redacted diffPreview', () {
      final queue = ApprovalQueue();
      final approval = queue.createForWrite(
        operations: const [WriteOp(path: 'a.txt', finalContent: 'new content')],
        diffText: '--- a/a.txt\n+++ b/a.txt\n+new content',
        cwd: '/ws',
        rootPath: '/ws',
      );
      expect(approval.kind, OperationKind.write);
      expect(approval.canonicalCommand, isNull);
      expect(approval.displayCommand, isNull);
      expect(approval.writeOperations, hasLength(1));
      expect(approval.diffPreview, contains('new content'));
    });

    test('toJson for a write approval carries diff_preview and paths, never a command field', () {
      final queue = ApprovalQueue();
      final approval = queue.createForWrite(
        operations: const [
          WriteOp(path: 'a.txt', finalContent: 'x'),
          WriteOp(path: 'b.txt', finalContent: 'y'),
        ],
        diffText: '--- a/a.txt\n+++ b/a.txt\n+x',
        cwd: '/ws',
        rootPath: '/ws',
      );
      final json = approval.toJson();
      expect(json['kind'], 'write');
      expect(json['command'], isNull);
      expect(json['diff_preview'], contains('+x'));
      expect(json['paths'], ['a.txt', 'b.txt']);
    });

    test('a diff containing a credential-shaped string is redacted in diffPreview', () {
      final queue = ApprovalQueue();
      final approval = queue.createForWrite(
        operations: const [WriteOp(path: '.env', finalContent: 'API_KEY=super-secret-123')],
        diffText: '--- a/.env\n+++ b/.env\n+API_KEY=super-secret-123',
        cwd: '/ws',
        rootPath: '/ws',
      );
      expect(approval.diffPreview, isNot(contains('super-secret-123')));
      expect(approval.diffPreview, contains('[REDACTED]'));
    });

    test('operationHash (write) is stable for identical operations/cwd/root and differs when content changes', () {
      const ops = [WriteOp(path: 'a.txt', finalContent: 'hello')];
      const opsChanged = [WriteOp(path: 'a.txt', finalContent: 'goodbye')];
      final a = PendingApproval.hashForWrite(operations: ops, cwd: '/ws', rootPath: '/ws');
      final b = PendingApproval.hashForWrite(operations: ops, cwd: '/ws', rootPath: '/ws');
      final c = PendingApproval.hashForWrite(operations: opsChanged, cwd: '/ws', rootPath: '/ws');
      expect(a, equals(b));
      expect(a, isNot(equals(c)));
    });

    test('respond/awaitDecision work identically for write approvals as for command approvals', () async {
      final queue = ApprovalQueue();
      final approval = queue.createForWrite(
        operations: const [WriteOp(path: 'a.txt', finalContent: 'x')],
        diffText: 'diff',
        cwd: '/ws',
        rootPath: '/ws',
      );
      queue.respond(approval.id, true);
      expect(await approval.decision, ApprovalStatus.approved);
      expect(queue.list(), isEmpty);
    });
  });

  group('redactDiffForDisplay', () {
    test('redacts a credential appearing on an added diff line', () {
      final result = redactDiffForDisplay('--- a/.env\n+++ b/.env\n+API_KEY=super-secret-123');
      expect(result, isNot(contains('super-secret-123')));
    });

    test('leaves an ordinary diff untouched', () {
      const diff = '--- a/a.txt\n+++ b/a.txt\n+hello world';
      expect(redactDiffForDisplay(diff), diff);
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
  _cancelGroup();
}



// Audit FSA-041: the server cancels a call it has given up on; the daemon must stop waiting for
// the human and must never execute a late Approve.
void _cancelGroup() {
  group('cancel (FSA-041)', () {
    test('cancelling a pending approval resolves its decision as cancelled and delists it', () async {
      final queue = ApprovalQueue();
      final approval = queue.createForCommand(canonicalCommand: 'make', cwd: '/w', rootPath: '/w');
      expect(queue.cancel(approval.id), isTrue);
      expect(await approval.decision, ApprovalStatus.cancelled);
      expect(queue.list(), isEmpty);
    });

    test('a late Approve after cancellation is ignored', () async {
      final queue = ApprovalQueue();
      final approval = queue.createForCommand(canonicalCommand: 'make', cwd: '/w', rootPath: '/w');
      queue.cancel(approval.id);
      expect(queue.respond(approval.id, true), isTrue); // known id, no-op
      expect(approval.status, ApprovalStatus.cancelled);
    });

    test('cancelling an unknown or already-decided approval changes nothing', () {
      final queue = ApprovalQueue();
      expect(queue.cancel('appr_nope'), isFalse);
      final approval = queue.createForCommand(canonicalCommand: 'make', cwd: '/w', rootPath: '/w');
      queue.respond(approval.id, true);
      expect(queue.cancel(approval.id), isTrue);
      expect(approval.status, ApprovalStatus.approved);
    });
  });
}
