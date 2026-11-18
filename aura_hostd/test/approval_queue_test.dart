import 'package:aura_hostd/src/approval_queue.dart';
import 'package:aura_hostd/src/error_codes.dart';
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
  _retentionGroup();
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

// G-21 (WP9): the queue holds an approval -- with a write's full content -- only while it is
// pending. A decided, expired or cancelled one is dropped, keeping just enough (its id and
// outcome) for a late or duplicate answer to stay a harmless no-op. And the number pending at
// once is capped, so a runaway agent cannot grow the daemon without bound.
void _retentionGroup() {
  group('retention and cap (G-21)', () {
    PendingApproval write(ApprovalQueue queue, [String content = 'x']) => queue.createForWrite(
          operations: [WriteOp(path: 'a.txt', finalContent: content)],
          diffText: '+$content',
          cwd: '/w',
          rootPath: '/w',
        );

    test('an approved or rejected approval is no longer held, and answering again is a no-op',
        () async {
      final queue = ApprovalQueue();
      final approved = write(queue, 'big content');
      final rejected = queue.createForCommand(canonicalCommand: 'make', cwd: '/w', rootPath: '/w');
      expect(queue.heldCount, 2);
      queue.respond(approved.id, true);
      queue.respond(rejected.id, false);
      expect(queue.heldCount, 0);
      expect(await approved.decision, ApprovalStatus.approved);
      expect(await rejected.decision, ApprovalStatus.rejected);
      expect(queue.respond(approved.id, false), isTrue, reason: 'still known: a no-op, not a 404');
      expect(queue.cancel(rejected.id), isTrue);
      expect(approved.status, ApprovalStatus.approved);
      expect(rejected.status, ApprovalStatus.rejected);
    });

    test('a cancelled approval is no longer held', () async {
      final queue = ApprovalQueue();
      final approval = write(queue);
      queue.cancel(approval.id);
      expect(queue.heldCount, 0);
      expect(queue.respond(approval.id, true), isTrue);
      expect(approval.status, ApprovalStatus.cancelled);
    });

    test('an expired approval is no longer held', () async {
      final queue = ApprovalQueue(timeout: const Duration(milliseconds: 20));
      final approval = write(queue);
      expect(approval.expiresAt.difference(approval.requestedAt), const Duration(milliseconds: 20));
      expect(await queue.awaitDecision(approval), ApprovalStatus.expired);
      expect(queue.heldCount, 0);
      expect(queue.list(), isEmpty);
      expect(queue.respond(approval.id, true), isTrue);
      expect(approval.status, ApprovalStatus.expired);
    });

    test('only a bounded number of decided ids is remembered', () {
      final queue = ApprovalQueue(rememberedDecisions: 3);
      final first = queue.createForCommand(canonicalCommand: 'a', cwd: '/w', rootPath: '/w');
      queue.respond(first.id, true);
      for (var i = 0; i < 3; i++) {
        queue.respond(queue.createForCommand(canonicalCommand: 'b$i', cwd: '/w', rootPath: '/w').id, true);
      }
      expect(queue.respond(first.id, false), isFalse, reason: 'the oldest decision was forgotten');
      expect(first.status, ApprovalStatus.approved);
    });

    test('ids stay unique as approvals come and go', () {
      final queue = ApprovalQueue();
      final ids = <String>{};
      for (var i = 0; i < 50; i++) {
        final approval = queue.createForCommand(canonicalCommand: 'c', cwd: '/w', rootPath: '/w');
        expect(ids.add(approval.id), isTrue);
        queue.respond(approval.id, true);
      }
    });

    test('a request beyond the pending cap is refused with approval_queue_full', () {
      final queue = ApprovalQueue(maxPending: 3);
      final held = [for (var i = 0; i < 3; i++) write(queue, '$i')];
      for (final create in <void Function()>[
        () => write(queue),
        () => queue.createForCommand(canonicalCommand: 'make', cwd: '/w', rootPath: '/w'),
      ]) {
        expect(
          create,
          throwsA(isA<ApprovalQueueFullError>()
              .having((e) => e.code, 'code', HostdErrorCode.approvalQueueFull)
              .having((e) => e.message, 'message',
                  'Too many changes are waiting for your approval; answer some first.')),
        );
      }
      expect(queue.heldCount, 3);
      queue.respond(held.first.id, false);
      expect(() => write(queue), returnsNormally, reason: 'answering one makes room again');
    });

    test('the default cap is 100', () {
      final queue = ApprovalQueue();
      for (var i = 0; i < maxPendingApprovals; i++) {
        queue.createForCommand(canonicalCommand: 'c$i', cwd: '/w', rootPath: '/w');
      }
      expect(maxPendingApprovals, 100);
      expect(() => queue.createForCommand(canonicalCommand: 'one more', cwd: '/w', rootPath: '/w'),
          throwsA(isA<ApprovalQueueFullError>()));
    });
  });
}
