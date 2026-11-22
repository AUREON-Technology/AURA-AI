// delete_file and rename_file: the daemon's own refusals, trust gating and approval flow.
//
// What is asserted is the effect on disk (does the file still exist, with what content) and the
// gate it went through, not call choreography.

import 'dart:async';
import 'dart:io';

import 'package:aura_hostd/src/approval_queue.dart';
import 'package:aura_hostd/src/config.dart';
import 'package:aura_hostd/src/containment.dart';
import 'package:aura_hostd/src/error_codes.dart';
import 'package:aura_hostd/src/task_handler.dart';
import 'package:aura_hostd/src/workspace_store.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  late Directory tempDir;
  late Directory workspaceDir;
  late WorkspaceRoots roots;
  late ApprovalQueue approvals;
  late TaskHandler handler;

  String at(String relative) => p.join(workspaceDir.path, relative);
  File file(String relative, [String? content]) {
    final f = File(at(relative));
    if (content != null) {
      f.parent.createSync(recursive: true);
      f.writeAsStringSync(content);
    }
    return f;
  }

  Matcher rpcError([Matcher? message]) =>
      throwsA(isA<RpcError>().having((e) => e.message, 'message', message ?? isNotEmpty));

  // The call reaches the approval queue after resolving its root, which takes a few microtasks.
  Future<PendingApproval> nextApproval() async {
    for (var i = 0; i < 100 && approvals.list().isEmpty; i++) {
      await Future<void>.delayed(Duration.zero);
    }
    return approvals.list().single;
  }

  setUp(() {
    tempDir = Directory.systemTemp.createTempSync('aura_hostd_file_ops_test_');
    workspaceDir = Directory(p.join(tempDir.path, 'workspace'))..createSync();
    roots = WorkspaceRoots(const []);
    roots.addRoot(workspaceDir.path);
    approvals = ApprovalQueue();
    handler = TaskHandler(roots, approvals);
  });

  tearDown(() => tempDir.deleteSync(recursive: true));

  group('delete_file in a trusted workspace', () {
    setUp(() => roots.setTrusted(workspaceDir.path, true));

    test('removes the named file and nothing else', () async {
      file('a.txt', 'a');
      file('b.txt', 'b');
      file('sub/c.txt', 'c');

      final result = await handler.deleteFile({'path': 'a.txt'});

      expect(result['deleted'], 'a.txt');
      expect(file('a.txt').existsSync(), isFalse);
      expect(file('b.txt').readAsStringSync(), 'b');
      expect(file('sub/c.txt').readAsStringSync(), 'c');
      expect(approvals.list(), isEmpty, reason: 'a trusted workspace never queues');
    });

    test('accepts a nested path and an absolute path inside the root', () async {
      file('sub/deep/c.txt', 'c');
      file('d.txt', 'd');

      await handler.deleteFile({'path': 'sub/deep/c.txt'});
      await handler.deleteFile({'path': at('d.txt')});

      expect(file('sub/deep/c.txt').existsSync(), isFalse);
      expect(file('d.txt').existsSync(), isFalse);
    });

    test('refuses a directory and leaves everything in it', () async {
      file('dir/keep.txt', 'keep');
      await expectLater(handler.deleteFile({'path': 'dir'}), rpcError(contains('is a directory')));
      expect(file('dir/keep.txt').readAsStringSync(), 'keep');
    });

    test('refuses a file that does not exist', () async {
      await expectLater(handler.deleteFile({'path': 'nope.txt'}), rpcError(contains('does not exist')));
    });

    test('refuses a path that leaves the root, by .. or by an absolute path', () async {
      final outside = File(p.join(tempDir.path, 'outside.txt'))..writeAsStringSync('mine');
      await expectLater(handler.deleteFile({'path': '../outside.txt'}), rpcError(contains('no granted workspace root')));
      await expectLater(handler.deleteFile({'path': outside.path}), rpcError(contains('no granted workspace root')));
      expect(outside.readAsStringSync(), 'mine');
    });

    test('refuses the root itself and names that do not name an entry', () async {
      for (final path in ['.', '..', '', 'sub/..']) {
        await expectLater(handler.deleteFile({'path': path}), throwsA(anything), reason: 'path "$path"');
      }
      expect(workspaceDir.existsSync(), isTrue);
    });

    test('refuses anything inside a .git directory, hooks or not', () async {
      file('.git/index', 'idx');
      file('.git/hooks/pre-commit', '#!/bin/sh\n');
      file('sub/.git/HEAD', 'ref');
      for (final path in ['.git/index', '.git/hooks/pre-commit', 'sub/.git/HEAD']) {
        await expectLater(
          handler.deleteFile({'path': path}),
          throwsA(isA<RpcError>().having((e) => e.code, 'code', isNotNull)),
          reason: path,
        );
        expect(file(path).existsSync(), isTrue, reason: path);
      }
    });

    test('does not refuse names that only start with .git', () async {
      file('.gitignore', 'x');
      file('.github/ci.yml', 'x');
      await handler.deleteFile({'path': '.gitignore'});
      await handler.deleteFile({'path': '.github/ci.yml'});
      expect(file('.gitignore').existsSync(), isFalse);
      expect(file('.github/ci.yml').existsSync(), isFalse);
    });

    test('deleting a symlink removes the link, not the file it points at', () async {
      final target = file('target.txt', 'precious');
      Link(at('link.txt')).createSync(target.path);

      await handler.deleteFile({'path': 'link.txt'});

      expect(FileSystemEntity.typeSync(at('link.txt'), followLinks: false), FileSystemEntityType.notFound);
      expect(target.readAsStringSync(), 'precious');
    }, skip: Platform.isWindows ? 'symlinks need elevation on Windows' : false);
  });

  group('delete_file in an untrusted workspace', () {
    test('queues for approval with the removed lines as the diff, and deletes only once approved', () async {
      final target = file('notes.txt', 'line one\nline two');

      final future = handler.deleteFile({'path': 'notes.txt'});
      final pending = await nextApproval();
      expect(pending.diffPreview, allOf(contains('-line one'), contains('-line two')));
      expect(target.existsSync(), isTrue, reason: 'nothing is deleted while the person decides');

      approvals.respond(pending.id, true);
      await future;
      expect(target.existsSync(), isFalse);
    });

    test('a rejection leaves the file exactly as it was', () async {
      final target = file('notes.txt', 'keep me');
      final future = handler.deleteFile({'path': 'notes.txt'});
      approvals.respond((await nextApproval()).id, false);

      await expectLater(future, rpcError(contains('rejected by the user')));
      expect(target.readAsStringSync(), 'keep me');
    });

    test('an approved delete is refused if the file changed while the approval was pending', () async {
      final target = file('notes.txt', 'original');
      final future = handler.deleteFile({'path': 'notes.txt'});
      final pending = await nextApproval();
      target.writeAsStringSync('edited while the person was deciding');

      approvals.respond(pending.id, true);

      await expectLater(future, rpcError(contains('changed on disk')));
      expect(target.readAsStringSync(), 'edited while the person was deciding');
    });

    test('a binary file is queued with a size note instead of crashing the preview', () async {
      final bytes = [0xff, 0xfe, 0x00, 0x80, 0xc3, 0x28];
      final target = file('blob.bin')..writeAsBytesSync(bytes);

      final future = handler.deleteFile({'path': 'blob.bin'});
      final pending = await nextApproval();
      expect(pending.diffPreview, contains('binary file blob.bin (${bytes.length} bytes)'));
      approvals.respond(pending.id, true);
      await future;
      expect(target.existsSync(), isFalse);
    });

    test('revoking the root while the person decides stops the delete', () async {
      final target = file('notes.txt', 'keep me');
      final future = handler.deleteFile({'path': 'notes.txt'});
      final pending = await nextApproval();

      roots.removeRoot(workspaceDir.path);
      approvals.respond(pending.id, true);

      await expectLater(future, rpcError());
      expect(target.readAsStringSync(), 'keep me');
    });
  });

  group('rename_file in a trusted workspace', () {
    setUp(() => roots.setTrusted(workspaceDir.path, true));

    test('moves a file, keeping its content and removing the old path', () async {
      file('old.txt', 'content');

      final result = await handler.renameFile({'path': 'old.txt', 'new_path': 'new.txt'});

      expect(result, {'renamed': 'old.txt', 'to': 'new.txt'});
      expect(file('old.txt').existsSync(), isFalse);
      expect(file('new.txt').readAsStringSync(), 'content');
      expect(approvals.list(), isEmpty);
    });

    test('creates the destination directory when it does not exist yet', () async {
      file('old.txt', 'content');
      await handler.renameFile({'path': 'old.txt', 'new_path': 'a/b/c/new.txt'});
      expect(file('a/b/c/new.txt').readAsStringSync(), 'content');
    });

    test('renames a directory with everything in it', () async {
      file('src/one.txt', '1');
      file('src/inner/two.txt', '2');

      await handler.renameFile({'path': 'src', 'new_path': 'lib'});

      expect(Directory(at('src')).existsSync(), isFalse);
      expect(file('lib/one.txt').readAsStringSync(), '1');
      expect(file('lib/inner/two.txt').readAsStringSync(), '2');
    });

    test('never replaces an existing file: both keep their content', () async {
      file('a.txt', 'a');
      file('b.txt', 'b');

      await expectLater(
        handler.renameFile({'path': 'a.txt', 'new_path': 'b.txt'}),
        rpcError(contains('already exists')),
      );

      expect(file('a.txt').readAsStringSync(), 'a');
      expect(file('b.txt').readAsStringSync(), 'b');
    });

    test('refuses a missing source, the same path twice, and a directory moved into itself', () async {
      file('dir/f.txt', 'f');
      await expectLater(handler.renameFile({'path': 'nope.txt', 'new_path': 'x.txt'}), rpcError(contains('does not exist')));
      await expectLater(handler.renameFile({'path': 'dir', 'new_path': 'dir'}), rpcError(contains('already exists')));
      await expectLater(
        handler.renameFile({'path': 'dir', 'new_path': 'dir/inner'}),
        rpcError(contains('into itself')),
      );
      expect(file('dir/f.txt').readAsStringSync(), 'f');
    });

    test('refuses a destination outside the root and leaves the source in place', () async {
      file('a.txt', 'a');
      await expectLater(
        handler.renameFile({'path': 'a.txt', 'new_path': '../escaped.txt'}),
        throwsA(isA<ContainmentViolation>()),
      );
      await expectLater(
        handler.renameFile({'path': 'a.txt', 'new_path': p.join(tempDir.path, 'escaped.txt')}),
        throwsA(isA<ContainmentViolation>()),
      );
      expect(file('a.txt').readAsStringSync(), 'a');
      expect(File(p.join(tempDir.path, 'escaped.txt')).existsSync(), isFalse);
    });

    test('refuses to move into or out of a .git directory, or onto a hook', () async {
      file('a.txt', 'a');
      file('.git/index', 'idx');
      file('.git/hooks/placeholder', 'x');
      for (final destination in ['.git/hooks/pre-commit', '.git/config', '.git/anything', 'sub/.git']) {
        await expectLater(
          handler.renameFile({'path': 'a.txt', 'new_path': destination}),
          throwsA(isA<RpcError>().having((e) => e.code, 'code', isNotNull)),
          reason: destination,
        );
        expect(file(destination).existsSync(), isFalse, reason: destination);
      }
      await expectLater(
        handler.renameFile({'path': '.git/index', 'new_path': 'stolen-index'}),
        throwsA(isA<RpcError>()),
      );
      await expectLater(
        handler.renameFile({'path': '.git', 'new_path': 'dot-git-moved'}),
        throwsA(isA<RpcError>()),
      );
      expect(file('a.txt').readAsStringSync(), 'a');
      expect(file('.git/index').readAsStringSync(), 'idx');
    });

    test('renaming a symlink moves the link, not its target', () async {
      final target = file('target.txt', 'precious');
      Link(at('link.txt')).createSync(target.path);

      await handler.renameFile({'path': 'link.txt', 'new_path': 'moved-link.txt'});

      expect(FileSystemEntity.typeSync(at('moved-link.txt'), followLinks: false), FileSystemEntityType.link);
      expect(FileSystemEntity.typeSync(at('link.txt'), followLinks: false), FileSystemEntityType.notFound);
      expect(target.readAsStringSync(), 'precious');
    }, skip: Platform.isWindows ? 'symlinks need elevation on Windows' : false);
  });

  group('rename_file in an untrusted workspace', () {
    test('queues for approval and moves only once approved', () async {
      file('old.txt', 'content');

      final future = handler.renameFile({'path': 'old.txt', 'new_path': 'new.txt'});
      final pending = await nextApproval();
      expect(pending.diffPreview, contains('rename old.txt -> new.txt'));
      expect(file('old.txt').existsSync(), isTrue);
      expect(file('new.txt').existsSync(), isFalse);

      approvals.respond(pending.id, true);
      await future;
      expect(file('old.txt').existsSync(), isFalse);
      expect(file('new.txt').readAsStringSync(), 'content');
    });

    test('a rejection moves nothing', () async {
      file('old.txt', 'content');
      final future = handler.renameFile({'path': 'old.txt', 'new_path': 'new.txt'});
      approvals.respond((await nextApproval()).id, false);

      await expectLater(future, rpcError(contains('rejected by the user')));
      expect(file('old.txt').readAsStringSync(), 'content');
      expect(file('new.txt').existsSync(), isFalse);
    });

    test('an approved rename is refused if the destination appeared meanwhile', () async {
      file('old.txt', 'content');
      final future = handler.renameFile({'path': 'old.txt', 'new_path': 'new.txt'});
      final pending = await nextApproval();
      file('new.txt', 'someone else got here first');

      approvals.respond(pending.id, true);

      await expectLater(future, rpcError(contains('changed on disk')));
      expect(file('new.txt').readAsStringSync(), 'someone else got here first');
      expect(file('old.txt').readAsStringSync(), 'content');
    });

    test('an approved rename is refused if the source changed meanwhile', () async {
      final source = file('old.txt', 'content');
      final future = handler.renameFile({'path': 'old.txt', 'new_path': 'new.txt'});
      final pending = await nextApproval();
      source.writeAsStringSync('edited while the person was deciding');

      approvals.respond(pending.id, true);

      await expectLater(future, rpcError(contains('changed on disk')));
      expect(file('new.txt').existsSync(), isFalse);
    });
  });

  group('remote tasks (phone / web)', () {
    late ConfigStore config;
    late WorkspaceStore store;
    late String root;

    setUp(() {
      final configDir = Directory(p.join(tempDir.path, 'config'));
      config = ConfigStore.forConfigDir(configDir);
      store = WorkspaceStore.forConfigDir(configDir);
      store.save(roots); // workspaceDir granted, untrusted
      root = workspaceDir.resolveSymbolicLinksSync();
      handler = TaskHandler.live(
        store,
        approvalQueue: approvals,
        remoteControlEnabled: config.remoteControlEnabled,
      );
      config.save(config.load().copyWith(remoteControl: true));
    });

    Future<String> remoteTask() async =>
        (await handler.createTask({'origin': 'remote', 'cwd': root}))['task_id'] as String;

    test('an unconfirmed delete or rename is refused, nothing is queued and nothing changes', () async {
      final taskId = await remoteTask();
      file('a.txt', 'a');
      for (final confirmed in [null, false, 'true', 1]) {
        final extra = confirmed == null ? const <String, Object?>{} : {'confirmed': confirmed};
        await expectLater(
          handler.deleteFile({'task_id': taskId, 'path': 'a.txt', ...extra}),
          throwsA(isA<RpcError>().having((e) => e.code, 'code', HostdErrorCode.remoteConfirmationRequired)),
          reason: 'delete, confirmed=$confirmed',
        );
        await expectLater(
          handler.renameFile({'task_id': taskId, 'path': 'a.txt', 'new_path': 'b.txt', ...extra}),
          throwsA(isA<RpcError>().having((e) => e.code, 'code', HostdErrorCode.remoteConfirmationRequired)),
          reason: 'rename, confirmed=$confirmed',
        );
      }
      expect(approvals.list(), isEmpty);
      expect(file('a.txt').readAsStringSync(), 'a');
      expect(file('b.txt').existsSync(), isFalse);
    });

    test('a confirmed rename and delete apply without queueing on the desktop', () async {
      final taskId = await remoteTask();
      file('a.txt', 'a');

      await handler.renameFile({'task_id': taskId, 'path': 'a.txt', 'new_path': 'b.txt', 'confirmed': true});
      expect(file('b.txt').readAsStringSync(), 'a');
      await handler.deleteFile({'task_id': taskId, 'path': 'b.txt', 'confirmed': true});

      expect(file('b.txt').existsSync(), isFalse);
      expect(approvals.list(), isEmpty);
    });

    test('both stop once remote control is switched off', () async {
      final taskId = await remoteTask();
      file('a.txt', 'a');
      config.save(config.load().copyWith(remoteControl: false));

      await expectLater(
        handler.deleteFile({'task_id': taskId, 'path': 'a.txt', 'confirmed': true}),
        throwsA(isA<RpcError>().having((e) => e.code, 'code', HostdErrorCode.remoteControlDisabled)),
      );
      await expectLater(
        handler.renameFile({'task_id': taskId, 'path': 'a.txt', 'new_path': 'b.txt', 'confirmed': true}),
        throwsA(isA<RpcError>().having((e) => e.code, 'code', HostdErrorCode.remoteControlDisabled)),
      );
      expect(file('a.txt').readAsStringSync(), 'a');
    });
  });
}
